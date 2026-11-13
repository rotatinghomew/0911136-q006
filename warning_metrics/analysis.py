"""对完整同量纲数值计算单条跨表等式残差。"""


def residuals(tables):
    cells = {row["cell_id"]: row for row in tables["cells"]}
    output = []
    for rule in tables["constraints"]:
        if any(key not in cells for key in rule["terms"]):
            raise ValueError("约束引用未知单元格")
        selected = [cells[key] for key in rule["terms"]]
        if len({row["unit"] for row in selected}) > 1:
            raise ValueError("约束包含不同量纲")
        if any(row["value"] is None for row in selected):
            output.append({"constraint_id": rule["constraint_id"], "status": "unknown", "residual": None})
            continue
        residual = sum(cells[key]["value"] * coefficient for key, coefficient in rule["terms"].items()) - rule["rhs"]
        tolerance = rule["tolerance"] + sum(abs(coefficient) * cells[key]["tolerance"] for key, coefficient in rule["terms"].items())
        output.append({"constraint_id": rule["constraint_id"], "status": "balanced" if abs(residual) <= tolerance else "conflict", "residual": residual})
    return output


def summarize(tables):
    return {"residuals": residuals(tables), "form_count": len(tables["forms"]),
            "scope": "单条等式检查，不修改源表、不选择全局修正集合"}
