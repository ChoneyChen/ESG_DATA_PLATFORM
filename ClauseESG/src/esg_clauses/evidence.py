"""Evaluate clause confirmation requirements against value-only source proofs.

The clause SDK does not import extraction DTOs. Failure affects the mapping,
never admission of the independent raw fact.
"""


def check_evidence(clause, fields):
    flags = []
    for requirement in clause.evidence:
        if not requirement.required:
            continue

        def supports(source):
            if requirement.field == "unit" and source.get("role") == "unit" and source.get("object_type") != "report":
                # A bound table title/column/footnote unit is as usable as a row
                # unit. Missing units still leave comparison context incomplete.
                return True
            levels = {source.get("object_type")}
            role = source.get("role")
            if role in {"row", "column_header", "table", "footnote", "report"}:
                levels.add(role)
            if role == "unit" and source.get("row_id"):
                levels.add("row")
            if source.get("object_type") == "text_block":
                levels.add("text")
            return requirement.level in levels

        if not any(supports(source) for source in fields.get(requirement.field, [])):
            flags.append(
                "clause_evidence_missing:" + requirement.field + ":" + requirement.level
            )
    return flags
