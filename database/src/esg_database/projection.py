"""Deterministic organization of admitted facts, never another extraction or model call."""
from collections import Counter, defaultdict
from esg_contracts.facts import SourceFact
from esg_data.hashing import digest
from esg_data.periods import normalize_period, period_label, ReportedPeriod
from .products_contracts import Observation, ObservationSource, ComparisonStatus, ProjectionSummary, ProductDefinition

TRANSFORM_VERSION = "capture-observation-1.0"


def period_coordinate(period):
    body = period.model_dump(mode="json", exclude_none=True)
    if period.kind in {"annual", "quarter", "instant"} and period.year:
        body.pop("raw", None)
    return body


def _reported(fact, axis):
    return getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)


def _known(field):
    return field is not None and field.state in {"stated", "inherited"}


def _occurrence(fact, document):
    values = []
    for item in fact.evidence:
        if item.role not in {"value", "statement"}:
            continue
        location = {"page": item.page_index, "role": item.role, "excerpt": item.excerpt}
        if item.bbox and any(value is not None for value in item.bbox.values()):
            location["bbox"] = item.bbox
        else:
            # This is an object-level candidate identity, not a promise across IR revisions.
            location.update(object_id=item.object_id, row=item.row_id, column=item.column_id)
        if item.char_start is not None:
            location.update(char_start=item.char_start, char_end=item.char_end)
        values.append(location)
    source = document.get("source_sha256") or {"document_id": fact.document_id, "ir_run_id": fact.ir_run_id, "revision": fact.ir_revision}
    return "occurrence-" + digest([source, values])


def compile_projection(payload):
    """Input is an immutable database export snapshot; return a sealed projection."""
    export_id, input_digest = payload["export_id"], payload["input_digest"]
    document = payload["manifest"]["document"]
    targets = {row["clause_key"]: row["contract"] for row in payload["targets"]}
    coverage = {row["clause_key"]: row for row in payload["coverage"]}
    definitions = []
    for key, contract in sorted(targets.items()):
        title = contract.get("labels", {}).get("zh") or contract.get("labels", {}).get("en") or key
        definitions.append(ProductDefinition(clause_key=key, clause_digest=contract["clause_digest"], title=title, contract=contract).model_dump(mode="json"))
    by_fact = defaultdict(list)
    for row in payload["relations"]:
        by_fact[row["source_fact_id"]].append(row)
    observations = []
    for row in sorted(payload["facts"], key=lambda row: row["record"]["source_fact_id"]):
        fact = SourceFact.model_validate(row["record"])
        reported_period = fact.identity.reporting_period
        period = normalize_period(reported_period.value) if _known(reported_period) else None
        period = period or ReportedPeriod(kind="unknown", raw=reported_period.raw_text or "期间未陈述")
        resource = fact.report_resource
        source = ObservationSource(document_id=fact.document_id, pdf_sha256=document.get("source_sha256"),
            pdf_id=resource.pdf_id if resource else None, title=resource.title if resource else document.get("label"),
            report_company=resource.company.name if resource and resource.company else None,
            publication_year=resource.publication_year if resource else None,
            resource_revision=resource.revision if resource else None,
            report_company_id=resource.company.company_id if resource and resource.company else None,
            industry=resource.industry.name if resource and resource.industry else None,
            reporting_year=resource.reporting_year if resource else None)
        for link in sorted(by_fact[fact.source_fact_id] or [{"clause_key": None, "relation": "uncertain", "mapping": {}}], key=lambda row: row["clause_key"] or ""):
            key, relation = link["clause_key"], link["relation"]
            contract = targets.get(key)
            reasons = []
            if relation != "match": reasons.append("target_relation:" + relation)
            if period.kind == "unknown": reasons.append("period_unknown")
            if fact.quality != "grounded": reasons.append("source_confirmation_pending")
            if fact.value.kind == "quantitative" and fact.value.parse_status != "parsed": reasons.append("structured_value_not_available")
            if contract:
                purposes = contract.get("purposes") or {}
                policy = purposes.get("comparison") or {"required_axes": contract["value"].get("comparison_axes", []), "require_complete_scopes": True}
                for axis in policy.get("required_axes", []):
                    if not _known(_reported(fact, axis)): reasons.append("identity_unconfirmed:" + axis)
                if policy.get("require_complete_scopes", True) and coverage.get(key, {}).get("state") != "covered":
                    reasons.append("declared_scopes_unconfirmed")
            else:
                reasons.append("no_frozen_target")
            identity = {axis: field.value for axis in type(fact.identity).model_fields if axis != "dimensions" and _known(field := _reported(fact, axis))}
            identity["dimensions"] = {axis: field.value for axis, field in fact.identity.dimensions.items() if _known(field)}
            identity["reporting_period"] = period_coordinate(period)
            # Display never requires these fields. Source selection needs stronger identity.
            selection_complete = not reasons and all(_known(_reported(fact, axis)) for axis in ("reporting_entity", "reporting_boundary"))
            if fact.value.kind == "quantitative" and not fact.value.unit_raw:
                selection_complete = False
            comparison_key = digest([key, contract["clause_digest"], identity, fact.source_row_label,
                fact.value.unit_raw if fact.value.kind == "quantitative" else "statement"]) if selection_complete else None
            title = contract.get("labels", {}).get("zh") or contract.get("labels", {}).get("en") or key if contract else "来源原值"
            observation_id = "observation-" + digest([TRANSFORM_VERSION, fact.model_dump(mode="json"),
                contract["clause_digest"] if contract else None, link["mapping"], source.model_dump(mode="json")])
            observations.append(Observation(observation_id=observation_id, source_fact_id=fact.source_fact_id,
                occurrence_id=_occurrence(fact, document), clause_key=key, clause_digest=contract["clause_digest"] if contract else None,
                title=title, relation=relation, mapping=link["mapping"] or None, period=period, source_row_label=fact.source_row_label,
                value=fact.value, identity=fact.identity, evidence=fact.evidence, source=source,
                quality=fact.quality, flags=fact.flags, extensions=fact.extensions,
                comparison=ComparisonStatus(eligible=not reasons, reasons=reasons), comparison_key=comparison_key).model_dump(mode="json"))
    observations.sort(key=lambda row: row["observation_id"])
    content = digest([definitions, observations])
    occurrences = Counter(row["occurrence_id"] for row in observations)
    summary = ProjectionSummary(projection_id="projection-" + digest([export_id, input_digest, TRANSFORM_VERSION]),
        export_id=export_id, input_digest=input_digest, transform_version=TRANSFORM_VERSION, content_digest=content,
        facts=len(payload["facts"]), observations=len(observations), periods=dict(sorted(Counter(period_label(row["period"]) for row in observations).items())),
        relations=dict(sorted(Counter(row["relation"] for row in observations).items())),
        repeated_occurrences=sum(count > 1 for count in occurrences.values()),
        diagnostics=["Repeated physical occurrences are candidates for review, never automatically summed or deleted."] if any(count > 1 for count in occurrences.values()) else []).model_dump(mode="json")
    return {"summary": summary, "definitions": definitions, "observations": observations}


def project_export(bridge, export_id):
    payload = bridge.call("projection_input", {"export_id": export_id})
    compiled = compile_projection(payload)
    return bridge.call("store_projection", compiled)
