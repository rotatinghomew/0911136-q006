"""验证统计口径一致性诊断的业务边界与歧义保留。"""
import copy
import json
import unittest
from fractions import Fraction
from pathlib import Path

from warning_metrics.analysis import summarize
from warning_metrics.diagnosis import (
    diagnose,
    evaluate_constraints,
    diagnose_revisions,
    diagnose_scopes,
)


ROOT = Path(__file__).resolve().parents[1]


def load_tables():
    return json.loads((ROOT / "fixtures/dataset.json").read_text())["tables"]


class CandidateExplanationTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_tables()

    def test_singleton_cannot_explain_constraint_it_does_not_touch(self):
        # sum 与 states 两条冲突约束没有公共单元格；只改任何一个单元格
        # 都无法同时解释二者，最小解释必须是双单元格集合。
        report = diagnose(self.tables)
        candidates = report["candidate_explanations"]["candidates"]
        self.assertTrue(candidates)
        self.assertTrue(all(len(candidate["cell_ids"]) == 2 for candidate in candidates))
        for candidate in candidates:
            self.assertEqual(["states", "sum"], sorted(candidate["explains"]))

    def test_multiple_equally_plausible_explanations_are_all_retained(self):
        report = diagnose(self.tables)
        candidates = report["candidate_explanations"]["candidates"]
        # 8 个最小双单元格组合都能线性地同时平衡两条约束，系统不得只选一个。
        self.assertEqual(8, len(candidates))
        self.assertGreaterEqual(len({tuple(candidate["cell_ids"]) for candidate in candidates}), 2)
        for candidate in candidates:
            self.assertTrue(candidate["integer_values_determined"])
            for member in candidate["members"]:
                self.assertIn("delta_if_others_unchanged", member)
                self.assertIn("source_ref", member)

    def test_implied_delta_actually_balances_every_conflict(self):
        cells = {row["cell_id"]: row for row in self.tables["cells"]}
        report = diagnose(self.tables)
        conflict_rules = [rule for rule, result in zip(self.tables["constraints"],
                                                       report["constraint_checks"])
                          if result["status"] == "conflict"]
        for candidate in report["candidate_explanations"]["candidates"]:
            deltas = {member["cell_id"]: member["delta_if_others_unchanged"]
                      for member in candidate["members"]}
            for rule in conflict_rules:
                residual = sum(
                    Fraction(cells[key]["value"]) * Fraction(coefficient)
                    + Fraction(deltas.get(key, 0)) * Fraction(coefficient)
                    for key, coefficient in rule["terms"].items()
                ) - Fraction(rule["rhs"])
                tolerance = Fraction(rule["tolerance"]) + sum(
                    abs(Fraction(coefficient)) * Fraction(cells[key]["tolerance"])
                    for key, coefficient in rule["terms"].items())
                self.assertLessEqual(abs(residual), tolerance)

    def test_compensating_errors_balance_sheet_still_flagged(self):
        # 构造两张村表各错 5 人、方向相反，单条约束残差为零（看似平衡），
        # 但乡镇独立总数暴露矛盾；诊断必须仍给出冲突与候选集合。
        tables = copy.deepcopy(self.tables)
        cells = {row["cell_id"]: row for row in tables["cells"]}
        cells["a"]["value"] = 25
        cells["b"]["value"] = 25
        cells["ab"]["value"] = 50  # a+b 表面对得上
        tables["constraints"] = [
            {"constraint_id": "village_sum",
             "terms": {"a": 1, "b": 1, "ab": -1}, "rhs": 0, "tolerance": 0,
             "description": "村合计与乡镇一致"},
            {"constraint_id": "independent_town",
             "terms": {"ab": 1, "shelter": -1, "home": -1}, "rhs": 0,
             "tolerance": 0, "description": "乡镇独立口径核对"},
        ]
        report = diagnose(tables)
        statuses = {row["constraint_id"]: row["status"]
                    for row in report["constraint_checks"]}
        self.assertEqual("balanced", statuses["village_sum"])
        self.assertEqual("conflict", statuses["independent_town"])
        candidates = report["candidate_explanations"]["candidates"]
        implicated = {cell for candidate in candidates
                      for cell in candidate["cell_ids"]}
        # 平衡约束里的 a、b 与冲突约束里的 ab/shelter/home 都应进入候选视野，
        # 系统不能因为汇总对得上就放行，也不能断言是哪个村瞒报。
        self.assertIn("ab", implicated)

    def test_diagnosis_is_read_only(self):
        before = json.dumps(self.tables, sort_keys=True, ensure_ascii=False)
        diagnose(self.tables)
        after = json.dumps(self.tables, sort_keys=True, ensure_ascii=False)
        self.assertEqual(before, after)

    def test_no_balanced_replacement_table_is_produced(self):
        report = diagnose(self.tables)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("corrected", serialized)
        self.assertNotIn("replacement", serialized)
        # 输出只含建议与证据，不含整表重写后的 cells/forms/constraints。
        self.assertNotIn("cells", report)
        self.assertNotIn("forms", report)


class MissingAndUnitTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_tables()

    def test_null_is_unknown_not_zero(self):
        report = diagnose(self.tables)
        missing = report["missing_report_diagnosis"]["missing_cells"]
        self.assertEqual(["unknown"], [row["cell_id"] for row in missing])
        check = next(row for row in report["constraint_checks"]
                     if row["constraint_id"] == "missing")
        self.assertEqual("unknown", check["status"])
        self.assertIsNone(check["residual"])
        advisory_kinds = [row["kind"] for row in report["advisories"]]
        self.assertIn("missing_not_zero", advisory_kinds)

    def test_mixed_units_rejected_without_cross_inference(self):
        tables = copy.deepcopy(self.tables)
        cells = {row["cell_id"]: row for row in tables["cells"]}
        cells["b"]["unit"] = "household"
        with self.assertRaises(ValueError):
            diagnose(tables)

    def test_person_and_household_constraints_stay_separate(self):
        # 同一张表上户数与人数各自成约束，诊断不得跨量纲组队或做比值认定。
        tables = copy.deepcopy(self.tables)
        for cell in tables["cells"]:
            cell["unit"] = "person"
        tables["cells"].append({
            "cell_id": "hh_a", "form_id": "f1", "scope_id": "v1",
            "metric": "households", "value": 7, "unit": "household",
            "tolerance": 0, "source_ref": "source-hh-a"})
        tables["cells"].append({
            "cell_id": "hh_b", "form_id": "f2", "scope_id": "v2",
            "metric": "households", "value": 9, "unit": "household",
            "tolerance": 0, "source_ref": "source-hh-b"})
        tables["constraints"].append({
            "constraint_id": "hh_sum",
            "terms": {"hh_a": 1, "hh_b": 1}, "rhs": 20, "tolerance": 0,
            "description": "户数口径核对"})
        report = diagnose(tables)
        hh_check = next(row for row in report["constraint_checks"]
                        if row["constraint_id"] == "hh_sum")
        self.assertEqual("household", hh_check["unit"])
        for candidate in report["candidate_explanations"]["candidates"]:
            units = {member["unit"] for member in candidate["members"]}
            self.assertEqual(1, len(units))
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("瞒报", serialized)
        self.assertNotIn("per_household", serialized)

    def test_unknown_constraint_reference_is_reported_not_crashed(self):
        tables = copy.deepcopy(self.tables)
        tables["constraints"].append({
            "constraint_id": "ghost", "terms": {"nonexistent": 1},
            "rhs": 0, "tolerance": 0, "description": "悬空引用"})
        with self.assertRaises(ValueError):
            evaluate_constraints(tables)


class ScopeAndRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_tables()

    def test_hierarchy_and_overlap_scopes_are_identified(self):
        report = diagnose_scopes(self.tables)
        pairs = {(pair["forms"][0], pair["forms"][1]): pair["relation"]
                 for pair in report["scope_overlap_pairs"]}
        self.assertEqual("first_subordinate_of_second", pairs[("f1", "f3")])
        self.assertEqual("first_subordinate_of_second", pairs[("f2", "f3")])

    def test_partial_overlap_flagged(self):
        tables = copy.deepcopy(self.tables)
        # f5 与 f3 在 v2 相交但互不包含，属于真正的局部口径重叠。
        tables["forms"].append({"form_id": "f5", "revision": 1, "supersedes": None,
                                "scope_members": ["v2", "v3"]})
        report = diagnose_scopes(tables)
        relations = {(pair["forms"][0], pair["forms"][1]): pair["relation"]
                     for pair in report["scope_overlap_pairs"]}
        self.assertEqual("partial_overlap", relations[("f3", "f5")])
        self.assertEqual("first_subordinate_of_second", relations[("f2", "f5")])

    def test_duplicate_report_group_traces_source_refs(self):
        tables = copy.deepcopy(self.tables)
        tables["cells"].append({
            "cell_id": "a_dup", "form_id": "f5", "scope_id": "v1",
            "metric": "people", "value": 21, "unit": "person",
            "tolerance": 0, "source_ref": "source-a-duplicate"})
        tables["forms"].append({"form_id": "f5", "revision": 1, "supersedes": None,
                                "scope_members": ["v1"]})
        report = diagnose_scopes(tables)
        group = next(group for group in report["duplicate_report_groups"]
                     if group["scope_id"] == "v1")
        self.assertEqual(["a", "a_dup"], group["cell_ids"])
        self.assertEqual(["source-a", "source-a-duplicate"], group["source_refs"])

    def test_supersedes_chain_and_partial_revision_traceable(self):
        tables = copy.deepcopy(self.tables)
        tables["forms"].append({"form_id": "f3r2", "revision": 2,
                                "supersedes": "f3", "scope_members": ["v1"]})
        report = diagnose_revisions(tables)
        revision = next(row for row in report["revisions"]
                        if row["form_id"] == "f3r2")
        self.assertEqual("partial_revision", revision["revision_kind"])
        self.assertEqual(["v1"], revision["shared_scope_members"])
        stale = {row["cell_id"] for row in report["stale_constraint_references"]}
        # f3 被取代后，仍引用其单元格的约束必须被提示追溯源单。
        self.assertTrue({"ab", "shelter", "home"} & stale)

    def test_broken_and_cyclic_supersedes_are_issues(self):
        tables = copy.deepcopy(self.tables)
        tables["forms"].append({"form_id": "f9", "revision": 1,
                                "supersedes": "missing-form", "scope_members": []})
        broken = diagnose_revisions(tables)
        self.assertTrue(any(issue["form_id"] == "f9"
                            for issue in broken["revision_chain_issues"]))
        tables["forms"] = [
            {"form_id": "x", "revision": 1, "supersedes": "y", "scope_members": []},
            {"form_id": "y", "revision": 2, "supersedes": "x", "scope_members": []},
        ]
        cyclic = diagnose_revisions(tables)
        self.assertTrue(cyclic["revision_chain_issues"])

    def test_missing_source_ref_and_dangling_form_flagged(self):
        tables = copy.deepcopy(self.tables)
        tables["cells"][0]["source_ref"] = ""
        tables["cells"][1]["form_id"] = "ghost-form"
        report = diagnose_revisions(tables)
        self.assertIn("a", report["cells_without_source_ref"])
        self.assertIn("ghost-form", report["cells_referencing_unknown_form"])
        kinds = [row["kind"] for row in diagnose(tables)["advisories"]]
        self.assertIn("reference_integrity", kinds)


class ToleranceAndSummaryCoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.tables = load_tables()

    def test_residual_within_combined_tolerance_is_balanced(self):
        tables = copy.deepcopy(self.tables)
        # states 残差 -2，约束 tolerance=1，给 shelter 声明误差带 1，
        # 合并容许度 1+1=2，恰好吸收残差，应判平衡而非冲突。
        cells = {row["cell_id"]: row for row in tables["cells"]}
        cells["shelter"]["tolerance"] = 1
        checks = {row["constraint_id"]: row for row in evaluate_constraints(tables)}
        self.assertEqual("balanced", checks["states"]["status"])
        self.assertEqual(2, checks["states"]["tolerance"])

    def test_diagnosis_keeps_existing_summary_contract(self):
        summary = summarize(self.tables)
        report = diagnose(self.tables)
        # 现行摘要字段不被诊断改动，诊断作为并列能力存在。
        self.assertEqual({"residuals", "form_count", "scope"}, set(summary))
        self.assertIn("constraint_checks", report)
        residual_ids = {row["constraint_id"] for row in summary["residuals"]}
        check_ids = {row["constraint_id"] for row in report["constraint_checks"]}
        self.assertEqual(residual_ids, check_ids)
        for old, new in zip(summary["residuals"], report["constraint_checks"]):
            self.assertEqual(old["status"], new["status"])
            self.assertEqual(old["residual"], new["residual"])

    def test_fractional_residual_kept_exact(self):
        tables = copy.deepcopy(self.tables)
        cells = {row["cell_id"]: row for row in tables["cells"]}
        cells["a"]["value"] = 20.5
        report = evaluate_constraints(tables)
        sum_check = next(row for row in report if row["constraint_id"] == "sum")
        self.assertEqual({"numerator": -9, "denominator": 2},
                         sum_check["residual"])

    def test_enumeration_truncation_is_disclosed_not_silently_complete(self):
        import warning_metrics.diagnosis as module
        original_limit = module.MAX_CANDIDATE_SETS
        module.MAX_CANDIDATE_SETS = 2
        try:
            report = diagnose(self.tables)
        finally:
            module.MAX_CANDIDATE_SETS = original_limit
        explanations = report["candidate_explanations"]
        self.assertTrue(explanations["truncated"])
        self.assertEqual(2, len(explanations["candidates"]))
        self.assertIn("enumeration_truncated",
                      [row["kind"] for row in report["advisories"]])
        # 恢复上限后完整枚举，不再有截断提示。
        full_report = diagnose(self.tables)
        self.assertFalse(full_report["candidate_explanations"]["truncated"])
        self.assertNotIn("enumeration_truncated",
                         [row["kind"] for row in full_report["advisories"]])

    def test_underdetermined_minimal_candidate_keeps_ambiguity(self):
        # 两条互不相交的冲突约束，任一单变量都无法同时解释；
        # 最小候选是跨约束二元组，且四个组合同等合理、全部保留为歧义。
        tables = copy.deepcopy(self.tables)
        tables["cells"] = [
            {"cell_id": cell_id, "form_id": "f1", "scope_id": "v1",
             "metric": "m", "value": 10, "unit": "person",
             "tolerance": 0, "source_ref": "src-" + cell_id}
            for cell_id in ("x", "y", "z", "w")
        ]
        tables["constraints"] = [
            {"constraint_id": "c1", "terms": {"x": 1, "y": 1},
             "rhs": 25, "tolerance": 0, "description": "欠定冲突一"},
            {"constraint_id": "c2", "terms": {"z": 1, "w": 1},
             "rhs": 30, "tolerance": 0, "description": "欠定冲突二"},
        ]
        report = diagnose(tables)
        candidates = report["candidate_explanations"]["candidates"]
        self.assertEqual(4, len(candidates))
        for candidate in candidates:
            self.assertEqual(2, len(candidate["cell_ids"]))
            self.assertEqual(["c1", "c2"], candidate["explains"])
        # 系统不选择唯一修正：每个候选仍只标注"若其他不变"的条件值，
        # 并在顶层给出歧义保留提示。
        self.assertTrue(any(advisory["kind"] == "ambiguity_preserved"
                            for advisory in report["advisories"]))

    def test_free_variable_returns_none_instead_of_invented_value(self):
        from warning_metrics.diagnosis import _unique_integer_solution
        # 单行两变量：dx + dy = 5，解不唯一，必须返回 None 而非编造单点值。
        matrix = [[Fraction(1), Fraction(1)]]
        self.assertIsNone(
            _unique_integer_solution(matrix, [Fraction(5)], 2))
        # 满秩唯一整数解正常返回；分数唯一解也返回 None（计数须为整数）。
        matrix2 = [[Fraction(1), Fraction(1)], [Fraction(1), Fraction(-1)]]
        self.assertEqual([Fraction(3), Fraction(0)],
                         _unique_integer_solution(matrix2, [Fraction(3), Fraction(3)], 2))
        matrix3 = [[Fraction(2)]]
        self.assertIsNone(
            _unique_integer_solution(matrix3, [Fraction(1)], 1))


if __name__ == "__main__":
    unittest.main()
