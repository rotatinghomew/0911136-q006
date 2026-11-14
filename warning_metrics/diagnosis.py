"""统计口径一致性诊断。

本模块只读源表，不修改任何上报数值，也不生成"看起来平衡"的替代表。
它把三类已有信息组合起来定位矛盾来源：

* 跨表线性约束（constraints）与各自声明的舍入/误差带；
* 表单 scope_members 的上下级与重叠范围，重叠范围不能直接累加；
* 表单修订链（supersedes）与 cell.source_ref，重复上报、局部修订可追溯到源单。

输出面向简报发出前的人工作业：给出候选记录集合、证据与修正建议，
存在多个同等合理解释时全部保留、保留歧义，由复核人员决定放行。
缺报（value 为 null）按未知处理，绝不按零参与推断；
person 与 household 量纲不互相推断，不做人口/户数相除式认定。
"""

from fractions import Fraction
from itertools import combinations

# 候选解释集合枚举的安全上限：仅枚举到该规模为止，避免组合爆炸。
# 真实复核场景中最小解释集合通常很小；超过上限时输出截断说明而非穷举。
MAX_CANDIDATE_CELLS = 5
MAX_CANDIDATE_SETS = 64
MAX_OVERLAP_PAIRS = 128


def _fraction(value):
    """把供数值转为精确有理数；整数保持整数，浮点按其精确值处理。"""
    if value is None:
        return None
    return Fraction(value)


def _json_number(value):
    """Fraction 转回 JSON 可表示的数；非整数保留分子分母，不丢精度。"""
    if value is None:
        return None
    if value.denominator == 1:
        return value.numerator
    return {"numerator": value.numerator, "denominator": value.denominator}


def _effective_tolerance(rule, cells):
    """约束容许度 = 约束自身 tolerance + 各单元格声明误差带按系数加权。"""
    total = Fraction(rule["tolerance"])
    for key, coefficient in rule["terms"].items():
        total += abs(Fraction(coefficient)) * Fraction(cells[key]["tolerance"])
    return total


def evaluate_constraints(tables):
    """逐条评估约束，复用与 analysis.residuals 相同的口径与校验规则。"""
    cells = {row["cell_id"]: row for row in tables["cells"]}
    output = []
    for rule in tables["constraints"]:
        if any(key not in cells for key in rule["terms"]):
            raise ValueError("约束引用未知单元格")
        selected = [cells[key] for key in rule["terms"]]
        units = {row["unit"] for row in selected}
        if len(units) > 1:
            raise ValueError("约束包含不同量纲，不能在同一约束内平衡：" + rule["constraint_id"])
        unit = next(iter(units))
        if any(row["value"] is None for row in selected):
            output.append({
                "constraint_id": rule["constraint_id"],
                "unit": unit,
                "status": "unknown",
                "residual": None,
                "tolerance": _json_number(_effective_tolerance(rule, cells)),
                "description": rule.get("description", ""),
            })
            continue
        residual = sum(_fraction(cells[key]["value"]) * Fraction(coefficient)
                       for key, coefficient in rule["terms"].items()) - Fraction(rule["rhs"])
        tolerance = _effective_tolerance(rule, cells)
        status = "balanced" if abs(residual) <= tolerance else "conflict"
        output.append({
            "constraint_id": rule["constraint_id"],
            "unit": unit,
            "status": status,
            "residual": _json_number(residual),
            "tolerance": _json_number(tolerance),
            "description": rule.get("description", ""),
        })
    return output


def _rational_feasible(matrix, vector):
    """Gauss-Jordan 消元判断线性方程组 Ax=b 是否存在有理数解。"""
    rows = [list(row) + [rhs] for row, rhs in zip(matrix, vector)]
    width = len(matrix[0])
    pivot_row = 0
    for column in range(width):
        candidate = next((index for index in range(pivot_row, len(rows))
                          if rows[index][column] != 0), None)
        if candidate is None:
            continue
        rows[pivot_row], rows[candidate] = rows[candidate], rows[pivot_row]
        pivot = rows[pivot_row][column]
        rows[pivot_row] = [entry / pivot for entry in rows[pivot_row]]
        for index in range(len(rows)):
            if index != pivot_row and rows[index][column] != 0:
                factor = rows[index][column]
                rows[index] = [entry - factor * pivot_entry
                               for entry, pivot_entry in zip(rows[index], rows[pivot_row])]
        pivot_row += 1
    return not any(all(entry == 0 for entry in row[:-1]) and row[-1] != 0 for row in rows)


def _unique_integer_solution(matrix, vector, variable_count):
    """满秩且唯一解时返回整数解，否则返回 None（无解或多解）。"""
    rows = [list(row) + [rhs] for row, rhs in zip(matrix, vector)]
    width = variable_count
    pivot_row = 0
    pivot_columns = []
    for column in range(width):
        candidate = next((index for index in range(pivot_row, len(rows))
                          if rows[index][column] != 0), None)
        if candidate is None:
            continue
        rows[pivot_row], rows[candidate] = rows[candidate], rows[pivot_row]
        pivot = rows[pivot_row][column]
        rows[pivot_row] = [entry / pivot for entry in rows[pivot_row]]
        for index in range(len(rows)):
            if index != pivot_row and rows[index][column] != 0:
                factor = rows[index][column]
                rows[index] = [entry - factor * pivot_entry
                               for entry, pivot_entry in zip(rows[index], rows[pivot_row])]
        pivot_columns.append(column)
        pivot_row += 1
    if any(all(entry == 0 for entry in row[:-1]) and row[-1] != 0 for row in rows):
        return None
    if len(pivot_columns) != width:
        return None  # 自由变量存在，解不唯一，保留歧义
    solution = [None] * width
    for row, column in zip(rows, pivot_columns):
        solution[column] = row[-1]
    if any(value is None or value.denominator != 1 for value in solution):
        return None
    return solution


def find_candidate_explanations(tables, evaluation):
    """枚举能同时解释全部冲突约束的最小候选单元格集合。

    命中集合（hitting set）只保证覆盖，不保证真能平衡；这里对每个集合
    做精确线性可行性检验，只保留能同时满足全部冲突约束的最小集合。
    多个同等合理解释全部返回，不替复核人员选定一个。
    """
    cells = {row["cell_id"]: row for row in tables["cells"]}
    conflict_rules = [rule for rule, result in zip(tables["constraints"], evaluation)
                      if result["status"] == "conflict"]
    if not conflict_rules:
        return {"truncated": False, "candidates": []}

    universe = sorted({key for rule in conflict_rules for key in rule["terms"]})
    # 量纲隔离：候选集合只在同一 unit 内组建。不同量纲的冲突约束分别诊断，
    # 避免出现"改户数去平衡人数"式的跨量纲解释。
    unit_groups = {}
    for rule in conflict_rules:
        unit = next(iter({cells[key]["unit"] for key in rule["terms"]}))
        unit_groups.setdefault(unit, []).append(rule)
    if len(unit_groups) > 1:
        group_reports = [_explanations_for_unit(unit, rules, cells)
                         for unit, rules in sorted(unit_groups.items())]
        return {
            "truncated": any(report["truncated"] for report in group_reports),
            "candidate_count": sum(report["candidate_count"] for report in group_reports),
            "candidates": [candidate for report in group_reports
                           for candidate in report["candidates"]],
            "unit_separated": True,
            "enumeration_limit": {"max_cells": MAX_CANDIDATE_CELLS,
                                  "max_sets": MAX_CANDIDATE_SETS},
            "note": "不同量纲（person/household）的冲突分开枚举，候选集合不跨量纲；"
                    "候选只定位记录与修正量，不回写源表，同规模候选不排序为唯一结论",
        }
    return _explanations_for_unit(next(iter(unit_groups)), conflict_rules, cells)


def _explanations_for_unit(unit, conflict_rules, cells):
    """在单一量纲内枚举最小候选解释集合。"""
    universe = sorted({key for rule in conflict_rules for key in rule["terms"]})
    index = {cell_id: position for position, cell_id in enumerate(universe)}
    # 设候选单元格修正量为 d（新值 = 现值 + d），其余单元格不动。
    # 原约束残差为 r = sum(a*x) - rhs，修正后要求 sum(a*d) = -r。
    # 故方程组为 A d = -r；右端是各冲突约束当前残差的相反数。
    matrix = []
    vector = []
    for rule in conflict_rules:
        row = [Fraction(0)] * len(universe)
        for key, coefficient in rule["terms"].items():
            row[index[key]] = Fraction(coefficient)
        matrix.append(row)
        observed = sum(Fraction(cells[key]["value"]) * Fraction(coefficient)
                       for key, coefficient in rule["terms"].items())
        residual = observed - Fraction(rule["rhs"])
        vector.append(-residual)

    # 单元格在多少条冲突约束中出现，作为复核优先级提示（不是结论）。
    touch_count = {cell_id: 0 for cell_id in universe}
    for rule in conflict_rules:
        for key in rule["terms"]:
            touch_count[key] += 1

    candidates = []
    truncated = False
    for size in range(1, min(MAX_CANDIDATE_CELLS, len(universe)) + 1):
        for combo in combinations(universe, size):
            positions = [index[cell_id] for cell_id in combo]
            sub_matrix = [[row[position] for position in positions] for row in matrix]
            # 候选变量在某条冲突约束中完全不出现时，该约束残差无法由本集合解释，
            # 直接判不可行（不能只看候选在至少一条约束里出现过）。
            if any(all(coefficient == 0 for coefficient in row) and rhs != 0
                   for row, rhs in zip(sub_matrix, vector)):
                continue
            if not _rational_feasible(sub_matrix, vector):
                continue
            # 最小性检验：去掉任一单元格后仍可行，则该集合不是最小解释。
            minimal = True
            if size > 1:
                for reduced in combinations(positions, size - 1):
                    reduced_matrix = [[row[position] for position in reduced] for row in matrix]
                    if all(any(coefficient != 0 for coefficient in row) or rhs == 0
                           for row, rhs in zip(reduced_matrix, vector)) \
                            and _rational_feasible(reduced_matrix, vector):
                        minimal = False
                        break
            if not minimal:
                continue
            integer_solution = _unique_integer_solution(sub_matrix, vector, size)
            members = []
            for order, cell_id in enumerate(combo):
                cell = cells[cell_id]
                member = {
                    "cell_id": cell_id,
                    "form_id": cell["form_id"],
                    "scope_id": cell["scope_id"],
                    "metric": cell["metric"],
                    "unit": cell["unit"],
                    "observed": cell["value"],
                    "source_ref": cell["source_ref"],
                    "conflict_constraint_count": touch_count[cell_id],
                }
                if integer_solution is not None:
                    delta = integer_solution[order]
                    implied = Fraction(cell["value"]) + delta
                    member["delta_if_others_unchanged"] = _json_number(delta)
                    member["implied_value_if_others_unchanged"] = _json_number(implied)
                    band = Fraction(cell["tolerance"])
                    if abs(delta) > band:
                        member["outside_declared_tolerance_band"] = True
                else:
                    member["implied_value_if_others_unchanged"] = None
                    member["implied_note"] = ("候选集合存在自由变量，修正量可取多个值，"
                                              "系统不给单点修正值，保留歧义")
                members.append(member)
            candidates.append({
                "cell_ids": list(combo),
                "unit": unit,
                "members": members,
                "integer_values_determined": integer_solution is not None,
                "explains": [rule["constraint_id"] for rule in conflict_rules],
                "ambiguity_note": "若同时返回多个候选集合，它们同等合理，本系统不选择唯一修正",
            })
            if len(candidates) >= MAX_CANDIDATE_SETS:
                truncated = True
                break
        if truncated:
            break
    if not candidates:
        truncated = len(universe) > MAX_CANDIDATE_CELLS
    return {
        "truncated": truncated,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "enumeration_limit": {"max_cells": MAX_CANDIDATE_CELLS,
                              "max_sets": MAX_CANDIDATE_SETS},
        "note": "候选集合只定位可解释矛盾的记录与取值区间，不回写源表；同规模候选之间不排序为唯一结论",
    }


def diagnose_scopes(tables):
    """利用 forms.scope_members 识别重叠/上下级范围，提示不可直接累加。"""
    forms = {row["form_id"]: row for row in tables["forms"]}
    scopes = {form_id: set(row["scope_members"]) for form_id, row in forms.items()}
    pairs = []
    form_ids = sorted(forms)
    for first, second in combinations(form_ids, 2):
        left, right = scopes[first], scopes[second]
        overlap = left & right
        if not overlap:
            continue
        if left <= right:
            relation = "first_subordinate_of_second"
        elif right <= left:
            relation = "second_subordinate_of_first"
        else:
            relation = "partial_overlap"
        pairs.append({
            "forms": [first, second],
            "relation": relation,
            "shared_scope_members": sorted(overlap),
            "risk": "范围重叠的同量纲单元格不能直接累加，需先按 scope 去重或确认层级口径",
        })
        if len(pairs) >= MAX_OVERLAP_PAIRS:
            break

    # 同一 scope、同一指标、同一量纲在多张有效表单中出现：重复上报嫌疑，可追源单。
    buckets = {}
    for cell in tables["cells"]:
        if cell["value"] is None:
            continue
        buckets.setdefault((cell["scope_id"], cell["metric"], cell["unit"]), []).append(cell)
    duplicates = []
    for (scope_id, metric, unit), group in sorted(buckets.items()):
        distinct_forms = {cell["form_id"] for cell in group}
        if len(distinct_forms) > 1:
            duplicates.append({
                "scope_id": scope_id,
                "metric": metric,
                "unit": unit,
                "cell_ids": sorted(cell["cell_id"] for cell in group),
                "form_ids": sorted(distinct_forms),
                "source_refs": sorted(cell["source_ref"] for cell in group),
                "risk": "同一范围同一指标被多张表单上报，需凭 source_ref 核对是否重复上报",
            })
    return {"scope_overlap_pairs": pairs, "duplicate_report_groups": duplicates}


def diagnose_missing(tables):
    """缺报检测：null 是未知不是零；另核对约束/表单引用完整性。"""
    cells = {row["cell_id"]: row for row in tables["cells"]}
    missing_cells = [{
        "cell_id": row["cell_id"],
        "form_id": row["form_id"],
        "scope_id": row["scope_id"],
        "metric": row["metric"],
        "unit": row["unit"],
        "source_ref": row["source_ref"],
        "interpretation": "value=null 表示缺报/未知，禁止按 0 参与汇总或约束平衡",
    } for row in tables["cells"] if row["value"] is None]

    unknown_constraint_refs = []
    for rule in tables["constraints"]:
        for key in rule["terms"]:
            if key not in cells:
                unknown_constraint_refs.append(
                    {"constraint_id": rule["constraint_id"], "cell_id": key})

    # 上级表单覆盖的范围是否在下级表单中至少有同量纲上报（缺报不是零）。
    # 只有当某个成员范围由"更细一级"的其他表单覆盖时才判缺口，
    # 避免把乡镇汇总表（自身单元格在汇总范围上）误判为缺报。
    forms = {row["form_id"]: row for row in tables["forms"]}
    forms_by_scope = {}
    for cell in tables["cells"]:
        forms_by_scope.setdefault((cell["scope_id"], cell["unit"]), set()).add(cell["form_id"])
    form_scopes = {form_id: set(row["scope_members"]) for form_id, row in forms.items()}
    coverage_gaps = []
    for form_id, form in sorted(forms.items()):
        units = sorted({cell["unit"] for cell in tables["cells"]
                        if cell["form_id"] == form_id})
        for unit in units:
            for member in sorted(form_scopes[form_id]):
                covering_forms = {other for other in forms_by_scope.get((member, unit), set())
                                  if other != form_id and other in form_scopes}
                if not covering_forms:
                    continue
                if not any(member in form_scopes[other] for other in covering_forms):
                    coverage_gaps.append({
                        "form_id": form_id,
                        "scope_id": member,
                        "unit": unit,
                        "covering_form_ids": sorted(covering_forms),
                        "interpretation": "下级表单覆盖该范围但缺少同量纲单元格，按缺报处理而非零",
                    })
    return {
        "missing_cells": missing_cells,
        "unknown_constraint_refs": unknown_constraint_refs,
        "coverage_gaps": coverage_gaps,
    }


def diagnose_revisions(tables):
    """修订链与重复上报追溯：supersedes 必须可追、无环、局部修订可定位。"""
    forms = {row["form_id"]: row for row in tables["forms"]}
    issues = []
    superseding = []
    for form_id, form in sorted(forms.items()):
        target = form.get("supersedes")
        if target is None:
            continue
        if target not in forms:
            issues.append({"form_id": form_id, "supersedes": target,
                           "problem": "supersedes 指向不存在的表单，修订链断裂"})
            continue
        superseding.append((form_id, target))
        # 环检测（沿修订链走，长度不超过表单数）。
        seen = set()
        cursor = target
        while cursor is not None and cursor in forms and cursor not in seen:
            seen.add(cursor)
            cursor = forms[cursor].get("supersedes")
        if cursor is not None and cursor in seen:
            issues.append({"form_id": form_id,
                           "problem": "修订链存在环，无法确定留档版本"})

    superseded_forms = {target for _, target in superseding}
    scopes = {form_id: set(row["scope_members"]) for form_id, row in forms.items()}
    revisions = []
    for form_id, target in superseding:
        new_scope, old_scope = scopes[form_id], scopes[target]
        if new_scope == old_scope:
            kind = "full_revision"
        elif new_scope & old_scope:
            kind = "partial_revision"
        else:
            kind = "disjoint_revision"
        revisions.append({
            "form_id": form_id,
            "supersedes": target,
            "revision_kind": kind,
            "shared_scope_members": sorted(new_scope & old_scope),
            "trace_note": "局部修订仅覆盖部分范围，原表留档；按 source_ref 核对差异单元格",
        })

    # 被取代表单仍被约束引用：确认使用的是留档值还是漏接新表。
    cells = {row["cell_id"]: row for row in tables["cells"]}
    stale_references = []
    for rule in tables["constraints"]:
        for key in rule["terms"]:
            cell = cells.get(key)
            if cell and cell["form_id"] in superseded_forms:
                stale_references.append({
                    "constraint_id": rule["constraint_id"],
                    "cell_id": key,
                    "form_id": cell["form_id"],
                    "source_ref": cell["source_ref"],
                    "note": "约束引用的单元格来自已被 supersedes 的表单，需确认是否应接入修订后源单",
                })

    missing_source_refs = sorted(cell["cell_id"] for cell in tables["cells"]
                                 if not cell.get("source_ref"))
    dangling_form_refs = sorted({cell["form_id"] for cell in tables["cells"]
                                 if cell["form_id"] not in forms})
    return {
        "revision_chain_issues": issues,
        "revisions": revisions,
        "stale_constraint_references": stale_references,
        "cells_without_source_ref": missing_source_refs,
        "cells_referencing_unknown_form": dangling_form_refs,
    }


def build_advisories(evaluation, explanations, scopes, missing, revisions):
    """汇总不擅自改数的人工修正建议；每条建议只指向证据，不生成替代表。"""
    advisories = []
    conflict_count = sum(1 for row in evaluation if row["status"] == "conflict")
    if conflict_count:
        advisories.append({
            "kind": "conflict_review",
            "priority": "high",
            "message": ("存在 %d 条超出容许误差带的冲突约束；按候选解释集合中的 "
                        "cell_id 与 source_ref 向源单位核实，不得直接改数" % conflict_count),
            "evidence_constraints": [row["constraint_id"] for row in evaluation
                                     if row["status"] == "conflict"],
        })
    if explanations.get("candidates"):
        advisories.append({
            "kind": "ambiguity_preserved",
            "priority": "high",
            "message": ("共有 %d 个同等合理的最小候选解释集合，系统不选择唯一修正；"
                        "复核人员需逐集合查看证据后决定放行" % explanations["candidate_count"]),
        })
    if explanations.get("truncated"):
        advisories.append({
            "kind": "enumeration_truncated",
            "priority": "medium",
            "message": "候选集合达到枚举上限被截断，可能存在更大规模的解释，需人工扩大排查",
        })
    flagged_cells = {}
    for candidate in explanations.get("candidates", []):
        for member in candidate["members"]:
            if member.get("outside_declared_tolerance_band"):
                flagged_cells.setdefault(member["cell_id"], member["source_ref"])
    for cell_id, source_ref in sorted(flagged_cells.items()):
        advisories.append({
            "kind": "outside_tolerance_band",
            "priority": "medium",
            "message": ("单元格 %s 在某个候选解释中的隐含修正量超出其声明误差带，若采纳该解释"
                        "需源单位重新上报，不能在汇总环节代改" % cell_id),
            "evidence_cell": cell_id,
            "source_ref": source_ref,
        })
    if scopes["scope_overlap_pairs"]:
        advisories.append({
            "kind": "scope_overlap",
            "priority": "medium",
            "message": "存在范围重叠/上下级表单，汇总前先按 scope 去重，禁止直接累加重叠范围",
            "evidence_pairs": len(scopes["scope_overlap_pairs"]),
        })
    if scopes["duplicate_report_groups"]:
        advisories.append({
            "kind": "duplicate_report",
            "priority": "high",
            "message": "发现同范围同指标多表单上报，凭 source_ref 追溯是否重复上报",
            "evidence_groups": scopes["duplicate_report_groups"],
        })
    if missing["missing_cells"]:
        advisories.append({
            "kind": "missing_not_zero",
            "priority": "high",
            "message": "缺报单元格按未知处理，禁止以 0 补齐后让汇总表看起来平衡",
            "evidence_cells": [row["cell_id"] for row in missing["missing_cells"]],
        })
    if missing["coverage_gaps"]:
        advisories.append({
            "kind": "coverage_gap",
            "priority": "medium",
            "message": "部分上级范围缺少同量纲下级上报，需催报而非按零汇总",
        })
    if revisions["revision_chain_issues"] or revisions["stale_constraint_references"]:
        advisories.append({
            "kind": "revision_trace",
            "priority": "medium",
            "message": "修订链存在断裂/环或约束仍引用已被取代表单，按 supersedes 与 source_ref 追溯源单",
        })
    if revisions["cells_referencing_unknown_form"] or revisions["cells_without_source_ref"] \
            or missing["unknown_constraint_refs"]:
        advisories.append({
            "kind": "reference_integrity",
            "priority": "high",
            "message": "存在无法追溯到表单/源单的引用，证据链不完整，简报不得放行",
        })
    return advisories


def diagnose(tables):
    """生成统计口径一致性诊断报告（只读，不修改 tables，不产出替代表）。

    person 与 household 始终分开处理：约束混合量纲直接报错，
    模块内不做人口数/户数相除，也不据比值推断瞒报。
    """
    evaluation = evaluate_constraints(tables)
    explanations = find_candidate_explanations(tables, evaluation)
    scope_report = diagnose_scopes(tables)
    missing_report = diagnose_missing(tables)
    revision_report = diagnose_revisions(tables)
    advisories = build_advisories(evaluation, explanations, scope_report,
                                  missing_report, revision_report)
    return {
        "scope": ("统计口径一致性诊断：定位能解释矛盾的候选记录集合与证据，"
                  "只读源表、不回写数值、不生成平衡替代表；缺报按未知，"
                  "person/household 不互推，多个同等解释保留歧义"),
        "constraint_checks": evaluation,
        "candidate_explanations": explanations,
        "scope_diagnosis": scope_report,
        "missing_report_diagnosis": missing_report,
        "revision_diagnosis": revision_report,
        "advisories": advisories,
        "release_rule": "简报发出前由复核人员查看证据决定放行；系统不给放行结论",
    }
