from __future__ import annotations

import json

import pytest

from esg_targeted.retrieval.report_cache import ReportEvidenceStore


def test_report_cache_is_scoped_by_ir_and_reuses_neutral_facts(tmp_path):
    identity = {"document_id": "doc-a", "ir_run_id": "ir-a", "ir_revision": 1}
    store = ReportEvidenceStore(tmp_path, **identity)
    fact = {"source_fact_id": "fact-1", "identity_raw": {"reporting_period": "2021"}}
    assert store.neutral_facts([fact]) == [fact]
    assert store.neutral_facts([]) == [fact]
    assert store.load("neutral_facts") == {"facts": [fact]}
    other = ReportEvidenceStore(tmp_path, document_id="doc-a", ir_run_id="ir-b", ir_revision=1)
    assert other.load("neutral_facts") is None


def test_report_cache_interpretation_is_bound_to_clause_contract(tmp_path):
    store = ReportEvidenceStore(tmp_path, document_id="doc-a", ir_run_id="ir-a", ir_revision=1)
    contract = {"package_version": "1.0.0", "definition": "solar energy consumption"}
    payload = {"rows": [{"value": "12"}], "unmapped_context": ["2021"]}
    assert store.interpretation(object_id="table-1", clause_contract=contract, payload=payload) == payload
    assert store.interpretation(object_id="table-1", clause_contract=contract, payload=None) == payload
    with pytest.raises(ValueError, match="Conflicting neutral interpretation"):
            store.interpretation(object_id="table-1", clause_contract=contract, payload={"rows": []})
    other = dict(contract, package_version="1.1.0")
    assert store.interpretation(object_id="table-1", clause_contract=other, payload=None) is None
