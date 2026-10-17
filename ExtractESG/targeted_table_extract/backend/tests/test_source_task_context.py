from copy import deepcopy
from esg_targeted.models.task_context import compact_task_context, complete_read_groups
from esg_targeted.models.task_context import repair_diagnostics


def test_repair_diagnostics_keep_paths_without_repeating_entire_invalid_task():
    class Issue:
        def model_dump(self):
            return {'path':'rows[2].field_refs.reporting_period','message':'x'*5000,'input_value':'raw-evidence'*10000}
    issues=[Issue()]*50
    compact=repair_diagnostics(issues)
    assert len(compact)==24
    assert compact[0]['path']=='rows[2].field_refs.reporting_period'
    assert len(compact[0]['message']) < 1050 and 'input_value' not in compact[0]
    assert len(issues[0].model_dump()['message'])==5000


def test_prompt_view_preserves_numbers_refs_and_headers_without_storage_repetition():
    context = {"kind": "table", "rows": [{"group": "G1", "group_id": "row",
        "support_segments": [{"span_id": "s", "text": "原文", "locators": [{"bbox": "large"}]}],
        "cells": [{"target_cell": "T1", "cell_id": "cell", "raw": "1.780", "span_id": "s",
            "column": "c1", "column_headers": ["2025"], "value_forms": [{"value": "1.780", "huge": "x"*2000}],
            "locators": [{"source_path": "/local/internal/path"}]}]}]}
    before = deepcopy(context)
    compact = compact_task_context(context)
    assert context == before
    cell = compact["rows"][0]["cells"][0]
    assert cell["raw"] == "1.780" and cell["target_cell"] == "T1" and cell["column_headers"] == ["2025"]
    assert "value_forms" not in cell and "locators" not in cell
    assert compact["rows"][0]["support_segments"][0] == {"span_id": "s", "text": "原文"}


def test_one_bound_assertion_cannot_complete_its_unbound_siblings():
    rows = [{"group_id": "same", "kind": "assertion", "statement": "A", "quality_status": "grounded"},
            {"group_id": "same", "kind": "assertion", "statement": "B", "quality_status": "unbound"},
            {"group_id": "other", "kind": "context", "context_role": "header"}]
    assert complete_read_groups(rows) == {"other"}


def test_partially_bound_quantitative_row_remains_incomplete():
    rows = [{"group_id": "g", "kind": "quantitative", "cells": [
        {"physical_sources": [{"ref": "s"}], "quality_status": "grounded"},
        {"physical_sources": [], "quality_status": "unbound"}]}]
    assert complete_read_groups(rows) == set()
