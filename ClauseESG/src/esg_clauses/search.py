"""Compile every selected clause's search fields once; consumers own execution."""
from .base import digest


def compile_search_plan(clause, *, limit=8):
    retrieval = clause.retrieval.model_dump(mode="json")
    references = retrieval.pop("vocabulary_refs", [])
    dictionary_terms = [term for name in references for term in clause.reading.vocabulary[name]]
    common = {
        "target_id": clause.clause_key,
        "contract_digest": clause.clause_digest,
        "data_kind": clause.data_kind,
        "exact_terms": clause.exact_labels,
        **retrieval,
        "fallback_terms": list(dict.fromkeys([*retrieval["positive_terms"], *dictionary_terms])),
    }
    page = {**common, "matching":"lexical", "supporting_terms": retrieval["context_terms"]}
    unit_dimensions = list(dict.fromkeys(dimension for rule in clause.reading.rules
        if rule.rule == "unit_dimension" for dimension in rule.allowed))
    objects = {**common, "limit": limit,
        "unit_dimensions":unit_dimensions or ([clause.value.unit_dimension] if clause.value.unit_dimension else [])}
    plan = {
        "schema_version": "clause-search-plan-1.2", "page": page, "objects": objects,
        "page_deferred_fields": {
            "excluded_object_terms": "Object exclusion is applied after page segmentation, never to a whole mixed-topic page.",
            "expected_candidate_types": "Object literal types are inspected after OCR; no semantic page-wide exclusion.",
            "source_priority": "Text-layer pages have no trusted table/text segmentation; object ranking owns priority.",
        },
        "context": {"attributes": {axis: attribute.context_terms for axis,attribute in clause.attributes.items()},
            "levels": {axis: attribute.context_levels for axis,attribute in clause.attributes.items()},
            "purpose": "enrichment_not_measurement"},
    }
    return {**plan, "digest": digest(plan)}
