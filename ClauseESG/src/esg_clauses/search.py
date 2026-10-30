"""Compile every selected clause's search fields once; consumers own execution."""
from .base import digest


def compile_search_plan(clause, *, limit=8):
    retrieval = clause.retrieval.model_dump(mode="json")
    common = {
        "target_id": clause.clause_key,
        "contract_digest": clause.clause_digest,
        "data_kind": clause.data_kind,
        "exact_terms": clause.exact_labels,
        **retrieval,
    }
    page = {**common, "supporting_terms": retrieval["context_terms"]}
    objects = {**common, "limit": limit}
    plan = {
        "schema_version": "clause-search-plan-1.0", "page": page, "objects": objects,
        "page_deferred_fields": {
            "excluded_object_terms": "Object exclusion is applied after page segmentation, never to a whole mixed-topic page.",
            "expected_candidate_types": "Object literal types are inspected after OCR; no semantic page-wide exclusion.",
            "source_priority": "Text-layer pages have no trusted table/text segmentation; object ranking owns priority.",
        },
        "context": {"terms": retrieval["context_terms"], "purpose": "enrichment_not_measurement"},
    }
    return {**plan, "digest": digest(plan)}
