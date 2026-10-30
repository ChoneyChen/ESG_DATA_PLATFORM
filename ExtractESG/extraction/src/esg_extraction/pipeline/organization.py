"""Project clause-owned result layouts over referenced source facts.

No domain names, clause identities, database fields, arithmetic aggregation or
source-reading cache belong here. Every produced row keeps its fact identity.
"""

from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from esg_data.hashing import digest
from esg_data.periods import normalize_period
from esg_data.organization import (
    OrganizationSpec,
    OrganizationField,
    FieldBinding,
    OrganizedCell,
    OrganizedRow,
    RowGroup,
    OrganizedTable,
    OrganizedDataset,
    EvidenceReference,
    AxisCoordinate,
    MatrixCell,
    MatrixView,
)

_MISSING = object()


def _reference(fact, container="fact", key=None, indices=None):
    return [
        EvidenceReference(
            source_fact_id=fact["source_fact_id"],
            container=container,
            field_key=key,
            index=index,
        )
        for index in (indices if indices is not None else range(len(fact["evidence"])))
    ]


def _get(source, binding):
    value = source.get(binding.key, _MISSING)
    if binding.attribute is not None:
        value = (
            value.get(binding.attribute, _MISSING)
            if isinstance(value, dict)
            else _MISSING
        )
    return value


def _cell(field, fact, mapping, clause, document):
    binding = field.binding
    origin = binding.source
    raw = None
    evidence = []
    reported_state = None
    flags = []
    classification = None
    if origin in {"identity", "dimension"}:
        identity = fact["identity"]
        reported = (
            identity.get("dimensions", {}) if origin == "dimension" else identity
        ).get(binding.key)
        if reported is None:
            return OrganizedCell(
                origin=origin, state="not_read", flags=field.identity_flags(None)
            )
        reported_state = reported["state"]
        state = {
            "stated": "available",
            "inherited": "available",
            "not_stated": "not_stated",
            "not_read": "not_read",
            "conflict": "conflict",
        }[reported_state]
        value = reported["value"]
        raw = reported.get("raw_text")
        if binding.attribute is not None:
            value = (
                value.get(binding.attribute, _MISSING)
                if isinstance(value, dict)
                else _MISSING
            )
        evidence = _reference(
            fact,
            "dimension" if origin == "dimension" else "identity",
            binding.key,
            range(len(reported["evidence"])),
        )
        flags = field.identity_flags(reported)
        classification = field.category_match(reported)
        if any(flag.startswith("output_vocabulary_conflict:") for flag in flags):
            state = "conflict"
    else:
        if origin == "value":
            value = _get(fact["value"], binding)
            role = "statement" if fact["value"]["kind"] == "assertion" else "value"
            if binding.key == "unit_raw":
                role = "unit"
            evidence = _reference(
                fact,
                indices=[
                    i for i, item in enumerate(fact["evidence"]) if item["role"] == role
                ],
            )
            raw = (
                (
                    fact["value"].get("statement_raw")
                    if fact["value"]["kind"] == "assertion"
                    else fact["value"].get("value_raw")
                )
                if binding.key != "unit_raw"
                else fact["value"].get("unit_raw")
            )
        elif origin == "resource":
            resource = fact.get("report_resource")
            if not resource and document.report_resource:
                resource = document.report_resource.model_dump(mode="json")
            value = _get(resource or {}, binding)
        elif origin == "target":
            value = _get(
                {
                    "clause_key": clause.clause_key,
                    "title": clause.title,
                    "definition": clause.definition,
                    "data_kind": clause.data_kind,
                    **clause.value.model_dump(mode="json"),
                },
                binding,
            )
        elif origin == "fact":
            value = _get(fact, binding)
            if binding.key == "source_row_label":
                raw = value if value is not _MISSING else None
                evidence = _reference(
                    fact,
                    indices=[
                        i
                        for i, item in enumerate(fact["evidence"])
                        if item["role"] == "row"
                    ],
                )
        elif origin == "mapping":
            value = _get(mapping or {}, binding)
        elif origin == "extension":
            value = _get(fact.get("extensions", {}), binding)
        else:
            source = {
                "pages": sorted(
                    {
                        item["page_index"] + 1
                        for item in fact["evidence"]
                        if item["page_index"] is not None
                    }
                ),
                "object_ids": list(
                    dict.fromkeys(item["object_id"] for item in fact["evidence"])
                ),
                "excerpts": [item["excerpt"] for item in fact["evidence"]],
            }
            value = _get(source, binding)
            evidence = _reference(fact)
        state = (
            "available" if value is not _MISSING and value is not None else "not_read"
        )
    if value is _MISSING:
        value = None
        state = "not_read"
        flags.append("binding_attribute_unavailable")
    if field.required_for_confirmation and state != "available":
        flags.append("required_output_field_missing:" + field.key)
    return OrganizedCell(
        origin=origin,
        state=state,
        value=value,
        raw_text=raw,
        reported_state=reported_state,
        classification=classification,
        evidence=evidence,
        flags=list(dict.fromkeys(flags)),
    )


def _expanded_definition(spec, facts):
    fields = list(spec.fields)
    known = {(f.binding.source, f.binding.key, f.binding.attribute) for f in fields}
    extra = []

    def add(source, key, attribute, label, role):
        if (source, key, attribute) in known:
            return
        name = "extra_" + digest([source, key, attribute])[:20]
        while name in {field.key for field in fields}:
            name += "-extra"
        fields.append(
            OrganizationField(
                key=name,
                label=label,
                role=role,
                binding=FieldBinding(source=source, key=key, attribute=attribute),
                type_hint="json",
            )
        )
        known.add((source, key, attribute))
        extra.append(name)

    if spec.include_additional_dimensions:
        for key in sorted(
            {key for fact in facts for key in fact["identity"]["dimensions"]}
        ):
            add("dimension", key, None, str(key) or "未命名维度", "reported_dimension")
    if spec.include_extensions:
        for key in sorted(
            {key for fact in facts for key in fact.get("extensions", {})}
        ):
            add("extension", key, None, "附加资料·" + str(key), "annotation")
        for key in sorted(
            {key for fact in facts for key in fact["value"].get("extensions", {})}
        ):
            add("value", "extensions", key, "值附加资料·" + str(key), "annotation")
    body = spec.model_dump(mode="json")
    body["fields"] = [field.model_dump(mode="json") for field in fields]
    if body["presentation"]["columns"]:
        body["presentation"]["columns"].extend(extra)
    return OrganizationSpec.model_validate(body)


def _sort_value(cell, field):
    value = cell.value
    if field.type_hint in {"decimal", "integer"}:
        try:
            number = Decimal(str(value))
            if number.is_finite():
                return (0, number)
        except (ValueError, InvalidOperation):
            pass
    return (1, json.dumps(value, ensure_ascii=False, sort_keys=True))


def _ordered(rows, spec):
    definitions = {field.key: field for field in spec.fields}
    for rule in reversed(spec.presentation.sort_by):
        known = []
        unknown = []
        for row in rows:
            (
                known
                if row.cells[rule.field].state == "available"
                and row.cells[rule.field].value is not None
                else unknown
            ).append(row)
        rows = (
            sorted(
                known,
                key=lambda row: _sort_value(
                    row.cells[rule.field], definitions[rule.field]
                ),
                reverse=rule.direction == "descending",
            )
            + unknown
        )
    return rows


def _groups(rows, spec):
    groups = {}
    for row in rows:
        keys = _coordinate_keys(row, spec.presentation.group_by)
        if not keys:
            continue
        identity = _coordinate_id(keys)
        if identity not in groups:
            groups[identity] = RowGroup(group_id=identity, keys=keys, row_ids=[])
        groups[identity].row_ids.append(row.row_id)
    return list(groups.values())


def _coordinate_keys(row, axes):
    result = {}
    for key in axes:
        cell = row.cells[key]
        value = (
            cell.classification.code
            if cell.classification and cell.classification.state == "recognized"
            else cell.value
        )
        result[key] = cell.model_copy(
            update={
                "value": value,
                "evidence": [],
                "raw_text": None,
                "extensions": {"grouping_key_only": True},
            }
        )
    return result


def _coordinate_id(keys):
    # A display coordinate is a value, not the history of how that value was read.
    def semantic(value):
        if isinstance(value, dict) and value.get("kind") in {"annual", "quarter", "fiscal_year", "instant", "range"}:
            period = normalize_period(value)
            if period and (period.year is not None or period.start or period.end):
                return period.model_dump(mode="json", exclude={"raw"}, exclude_none=True)
        return value
    return digest({key: {"state": cell.state, "value": semantic(cell.value)} for key, cell in keys.items()})


def _matrix(rows, definition):
    if definition is None:
        return None
    row_coordinates = {}
    columns = {}
    cells = {}
    for row in rows:
        row_keys = _coordinate_keys(row, definition.row_axes)
        column_keys = _coordinate_keys(row, definition.column_axes)

        row_id = _coordinate_id(row_keys)
        column_id = _coordinate_id(column_keys)
        row_coordinates.setdefault(
            row_id, AxisCoordinate(coordinate_id=row_id, keys=row_keys)
        )
        columns.setdefault(
            column_id, AxisCoordinate(coordinate_id=column_id, keys=column_keys)
        )
        cells.setdefault((row_id, column_id), []).append(row.row_id)
    return MatrixView(
        rows=list(row_coordinates.values()),
        columns=list(columns.values()),
        value_fields=definition.value_fields,
        cells=[
            MatrixCell(
                row_coordinate_id=key[0],
                column_coordinate_id=key[1],
                row_ids=ids,
                state="single" if len(ids) == 1 else "multiple",
            )
            for key, ids in cells.items()
        ],
    )


def build_organized_dataset(run_id, document, clauses, facts, mappings):
    raw = {}
    conflicted = set()
    for fact in facts:
        body = fact.model_dump(mode="json")
        if body["source_fact_id"] in raw and raw[body["source_fact_id"]] != body:
            conflicted.add(body["source_fact_id"])
        raw[body["source_fact_id"]] = body
    by_clause = defaultdict(list)
    for mapping in mappings:
        by_clause[mapping.clause_key].append(mapping.model_dump(mode="json"))
    tables = []
    used = set()
    failures = []
    for key, clause in clauses.items():
        table_id = digest([run_id, key, clause.clause_digest])
        selected = []
        mapping_by_id = {}
        flags = []
        for mapping in by_clause[key]:
            fact_id = mapping["source_fact_id"]
            fact = raw.get(fact_id)
            if fact is None or fact_id in conflicted:
                flags.append("unavailable_or_conflicting_source_fact:" + fact_id)
                continue
            if (
                fact["document_id"] != document.document_id
                or fact["ir_run_id"] != document.ir_run_id
                or fact["ir_revision"] != document.ir_revision
            ):
                flags.append("source_document_mismatch:" + fact_id)
                continue
            expected = (
                "quantitative" if clause.data_kind == "quantitative" else "assertion"
            )
            if fact["value"]["kind"] != expected:
                flags.append("source_value_kind_mismatch:" + fact_id)
                continue
            if fact_id in mapping_by_id and mapping_by_id[fact_id] != mapping:
                mapping = {
                    **mapping,
                    "match": "uncertain",
                    "flags": [*mapping["flags"], "mapping_conflict"],
                }
            mapping_by_id[fact_id] = mapping
        selected = [raw[fact_id] for fact_id in mapping_by_id]
        try:
            spec = _expanded_definition(clause.organization, selected).validate_kind(
                clause.data_kind
            )
            rows = []
            for fact in selected:
                fact_id = fact["source_fact_id"]
                mapping = mapping_by_id[fact_id]
                cells = {
                    field.key: _cell(field, fact, mapping, clause, document)
                    for field in spec.fields
                }
                row_flags = list(
                    dict.fromkeys(
                        [
                            *fact["flags"],
                            *mapping["flags"],
                            *[flag for cell in cells.values() for flag in cell.flags],
                        ]
                    )
                )
                rows.append(
                    OrganizedRow(
                        row_id=digest([table_id, fact_id]),
                        source_fact_id=fact_id,
                        match=mapping["match"],
                        cells=cells,
                        flags=row_flags,
                        result_class={"match": "target", "different": "other"}.get(mapping["match"], "unresolved"),
                    )
                )
            rows = _ordered(rows, spec)
            status = "organized" if rows else "empty"
            if flags:
                status = "failed"
                failures.extend(flags)
            tables.append(
                OrganizedTable(
                    table_id=table_id,
                    clause_key=key,
                    clause_digest=clause.clause_digest,
                    data_kind=clause.data_kind,
                    definition=spec,
                    rows=rows,
                    groups=_groups(rows, spec),
                    matrix=_matrix(rows, spec.presentation.matrix),
                    status=status,
                    flags=flags,
                    extensions={"result_counts": {name: sum(row.result_class == name for row in rows) for name in ("target", "other", "unresolved")}},
                )
            )
            used.update(row.source_fact_id for row in rows)
        except (ValueError, TypeError, KeyError) as exc:
            failures.append("organization_failed:" + key + ":" + str(exc))
            tables.append(
                OrganizedTable(
                    table_id=table_id,
                    clause_key=key,
                    clause_digest=clause.clause_digest,
                    data_kind=clause.data_kind,
                    definition=clause.organization,
                    status="failed",
                    flags=[failures[-1]],
                )
            )
    missing = sorted(set(raw) - used | conflicted)
    status = (
        "partial"
        if missing or failures
        else "needs_confirmation"
        if any(t.status == "needs_confirmation" for t in tables)
        else "organized"
    )
    return OrganizedDataset(
        run_id=run_id,
        document_id=document.document_id,
        ir_run_id=document.ir_run_id,
        ir_revision=document.ir_revision,
        report_resource=document.report_resource,
        tables=tables,
        unorganized_fact_ids=missing,
        status=status,
        extensions={
            "errors": failures,
            "source_fact_count": len(raw),
            "aggregation_performed": False,
        },
    )
