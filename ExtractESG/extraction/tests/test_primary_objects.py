"""Capture independent entities within the existing bounded primary search."""

import pytest

from esg_contracts.evidence import ObjectReference, ObjectSearchPage
from esg_contracts.facts import RunRequest
from esg_extraction.pipeline.adapters.artifacts import FileArtifactPort
from esg_extraction.pipeline.engine import ExtractionBackbone, ExecutionYield
from esg_extraction.pipeline.result_store import ResultStore
from test_backbone import repository, quantitative, run_backbone
from test_structure_contract import document, frame


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_all_primary_entities_and_years_survive_yield_and_checkpoint_resume(
    repository, tmp_path, legacy_checkpoint
):
    first = frame({"a": "2023", "b": "2024"}, {"a": "11", "b": "12"})
    second = frame({"a": "2023", "b": "2024"}, {"a": "21", "b": "22"})
    third = frame({"a": "2023", "b": "2024"}, {"a": "31", "b": "32"})
    objects = {}
    for name, evidence in [("first", first), ("second", second), ("third", third)]:
        row = evidence.rows[0]
        objects[name] = evidence.model_copy(update={
            "object_id": name,
            "digest": name,
            "rows": [row.model_copy(update={
                "label": "综合能源消耗量 " + name,
                "cells": [cell.model_copy(update={"cell_id": name + cell.cell_id}) for cell in row.cells],
            })],
        })
    # A second representation of the first table keeps physical cell IDs.
    objects["duplicate"] = objects["first"].model_copy(update={"object_id": "duplicate", "digest": "duplicate"})
    objects["outside-bound"] = objects["third"].model_copy(update={"object_id": "outside-bound", "digest": "outside-bound"})
    references = [ObjectReference(object_id=name, kind="table", page_index=0, score=10-index)
        for index, name in enumerate(["first", "second", "duplicate", "third", "outside-bound"])]

    class Docs:
        def __init__(self):
            self.queries = []

        def document(self, _):
            return document()

        def search_page(self, _, intent):
            self.queries.append(intent)
            return ObjectSearchPage(objects=references, total=5, returned=5,
                query_digest="bounded-primary", scope={"available_pages": [0]})

        def read(self, _, reference, chunk=0):
            return objects[reference.object_id]

    class Reader:
        def __init__(self):
            self.objects = []

        def complete(self, **kwargs):
            evidence = kwargs["context"]["frame"]
            self.objects.append(evidence["object_id"])
            return {"matches": [{"row_id": row["row_id"],
                "columns": [cell["column_id"] for cell in row["cells"]]}
                for row in evidence["rows"]]}, {}

    clause = quantitative(repository)
    clause = clause.model_copy(update={"purposes": clause.purposes.model_copy(update={
        "enrichment": clause.purposes.enrichment.model_copy(update={"identity_axes": []})})}).seal()
    request = RunRequest(ir_run_id="ir", clause_keys=[clause.clause_key])
    artifacts = FileArtifactPort(tmp_path)
    results = ResultStore("entities", artifacts)
    documents, reader = Docs(), Reader()
    with pytest.raises(ExecutionYield):
        ExtractionBackbone(documents, reader, artifacts, results).run(
            "entities", request, {clause.clause_key: clause})
    prior_ids = {fact["source_fact_id"] for fact in results.collection()["facts"]}
    assert len(prior_ids) == 4
    if legacy_checkpoint:
        checkpoint = results.checkpoint()
        progress = checkpoint["reading_progress"][clause.clause_key]
        progress.pop("target_found")
        progress.update(selected_object=references[0].model_copy(update={
            "source_snapshot": document().snapshot_id}).model_dump(mode="json"), object_complete=True)
        results.checkpoint(checkpoint)

    result = run_backbone(documents, reader, artifacts, "entities", request, {clause.clause_key: clause})

    assert reader.objects == ["first", "second", "third"]
    assert len(result["facts"]) == 6
    assert prior_ids <= {fact["source_fact_id"] for fact in result["facts"]}
    assert {fact["value"]["value_raw"] for fact in result["facts"]} == {"11", "12", "21", "22", "31", "32"}
    assert result["summary"]["assessment"]["periods"] == {"2023": 3, "2024": 3}
    assert result["summary"]["model_calls"] == 3
    progress = results.checkpoint()["reading_progress"][clause.clause_key]
    assert progress["target_found"] and progress["page_rounds"] == 0
    assert "selected_object" not in progress and "object_complete" not in progress
    assert all(query.matching != "lexical" for query in documents.queries)
