"""Anonymous replay of real protocol failure classes; no model/API calls."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from esg_targeted.models.report_semantics import (
    AgentPlan, ObjectMapping, TableUnderstanding, expand_understanding,
    mapping_template, plan_template, reference_catalog, understanding_template,
    validate_mapping_payload, validate_understanding_payload,
)
from esg_targeted.models.structured_template import json_schema, nuextract_template


@pytest.fixture
def failures():
    return json.loads((Path(__file__).parent / "fixtures/protocol_failures.json").read_text())


def mapping_context():
    return {"metrics": [{"metric_id": "metric.a"}, {"metric_id": "metric.b"}], "source_rows": [
        {"source_id": "R1", "kind": "assertion", "statement": "A supported policy.", "cells": []},
        {"source_id": "R2", "kind": "quantitative", "cells": [{"column": "c2024"}]},
    ]}


def reading_context():
    locator = {"page_index": 0, "object_id": "block-1", "bbox": [0, 0, 100, 30]}
    return {"rows": [{"group": "G1", "group_id": "block-1", "label": "Annual saving", "text": "2024 Example entity saved 1.70 tonnes.",
        "support_segments": [{"span_id": "span-1", "text": "2024 Example entity saved 1.70 tonnes.", "locators": [locator]}],
        "cells": [{"target_cell": "T1", "cell_id": "occurrence-1", "column": "narrative-1", "raw": "1.70", "unit": "tonnes",
                   "span_id": "span-1", "column_headers": ["2024", "Example entity"], "locators": [locator]}]}]}


def test_templates_come_from_the_exact_receiver_contracts():
    for model, template in ((AgentPlan, plan_template), (ObjectMapping, mapping_template), (TableUnderstanding, understanding_template)):
        assert template() == model.model_json_schema()
        assert json_schema(model) == template()
        assert json_schema(template()) is not None
    mapping_schema = mapping_template()["$defs"]["RowMapping"]["properties"]
    assert mapping_schema["source_id"]["type"] == "string"
    assert mapping_schema["source_id"]["minLength"] == 1
    assert mapping_schema["metric_id"]["type"] == "string"
    assert mapping_schema["columns"]["type"] == "array"
    assert "anyOf" not in mapping_schema["columns"]


def test_tools_generate_different_action_fields_and_accept_old_flat_payload(failures):
    defs = plan_template()["$defs"]
    assert "object_id" not in defs["SearchReportAction"]["properties"]
    assert "query" not in defs["InspectObjectAction"]["properties"]
    assert "context_kind" not in defs["RequestLocalRepairAction"]["properties"]
    assert "object_id" not in defs["FinishAction"]["properties"]
    action = AgentPlan.model_validate(failures["legacy_flat_action"]).actions[0]
    assert action.query == "accounting method" and action.object_id is None
    assert "object_id" not in action.model_dump()
    with pytest.raises(ValidationError):
        AgentPlan.model_validate(failures["invalid_action"])
    with pytest.raises(ValidationError):
        AgentPlan.model_validate({"actions": [{"tool": "inspect_object", "object_id": None}]})
    with pytest.raises(ValidationError):
        AgentPlan.model_validate({"actions": [{"tool": "request_local_repair", "object_id": "object-1", "reason": " "}]})
    identity_read = AgentPlan.model_validate({"actions": [{"tool": "read_object_context", "object_id": "object-1",
                                                         "context_kind": "identity"}]}).actions[0]
    assert identity_read.context_kind == "identity"
    assert "identity" in defs["ReadObjectContextAction"]["properties"]["context_kind"]["enum"]


@pytest.mark.parametrize("field,value", [("source_id", None), ("source_id", ""), ("metric_id", None), ("metric_id", ""), ("columns", None)])
def test_mapping_null_and_empty_reference_contract_is_consistent(field, value):
    match = {"source_id": "R1", "metric_id": "metric.a", "columns": [], "match": "uncertain", "reason": "Method is unknown"}
    match[field] = value
    with pytest.raises(ValidationError):
        ObjectMapping.model_validate({"matches": [match]})


def test_bad_narrative_column_keeps_valid_sibling_and_localizes_failure(failures):
    mapping, issues = validate_mapping_payload(failures["narrative_fake_column"], mapping_context())
    assert [(match.source_id, match.metric_id) for match in mapping.matches] == [("R2", "metric.b")]
    assert [(item.metric_id, item.status) for item in mapping.coverage] == [("metric.a", "uncertain"), ("metric.b", "reviewed")]
    assert len(issues) == 1 and issues[0].stage == "reference" and issues[0].metric_id == "metric.a"


def test_real_assertion_needs_no_column_and_negative_coverage_needs_no_source(failures):
    payload = failures["negative_object"]
    mapping, issues = validate_mapping_payload(payload, mapping_context())
    assert not issues and mapping.matches == []
    payload["matches"].append({"source_id": "R1", "metric_id": "metric.a", "columns": [], "match": "match", "reason": "The exact assertion applies"})
    mapping, issues = validate_mapping_payload(payload, mapping_context())
    assert not issues and mapping.matches[0].columns == []


def test_schema_failure_and_truncation_do_not_become_not_found(failures):
    mapping, issues = validate_mapping_payload(failures["null_identifiers"], mapping_context())
    assert issues[0].stage == "schema" and mapping.coverage[0].status == "uncertain"
    mapping, issues = validate_mapping_payload(failures["truncated_mapping"], mapping_context())
    assert issues[0].stage == "syntax" and all(item.status == "uncertain" for item in mapping.coverage)


def test_dynamic_reference_schema_uses_real_columns_and_bounded_source_menu():
    schema = mapping_template(mapping_context())
    props = schema["$defs"]["RowMapping"]["properties"]
    assert props["source_id"]["enum"] == ["R1", "R2"]
    assert props["columns"]["items"]["enum"] == ["c2024"]
    assert schema["$defs"]["RowMapping"]["allOf"][0]["then"]["properties"]["columns"]["items"]["enum"] == []
    context = reading_context()
    schema = understanding_template(context=context)
    assert schema["$defs"]["StatementFragment"]["properties"]["ref"]["enum"] == ["span-1"]
    assert schema["$defs"]["SemanticRow"]["properties"]["target_cells"]["items"]["enum"] == ["T1"]
    assert reference_catalog(context)["columns"] == ["narrative-1"]


def test_unknown_source_and_bad_target_do_not_delete_good_reading(failures):
    understanding, issues = validate_understanding_payload(failures["unknown_reference"], reading_context())
    assert len(understanding.rows) == 1 and understanding.rows[0].target_cells == ["T1"]
    assert all(issue.stage == "reference" for issue in issues)
    rows, errors = expand_understanding(understanding, reading_context())
    assert not errors and rows[0]["cells"][0]["value"] == "1.70"


def test_body_quantities_expand_exact_source_values_and_context_is_not_a_fact():
    understanding = TableUnderstanding.model_validate({"rows": [{"group": "G1", "kind": "quantitative", "target_cells": ["T1"],
        "statement": "The generated sentence has a wrong scaling factor.",
        "fields": [{"name": "reporting_entity", "value": "Example entity"}]}]})
    rows, errors = expand_understanding(understanding, reading_context())
    assert not errors and rows[0]["cells"][0]["value"] == "1.70" and rows[0]["cells"][0]["unit"] == "tonnes"
    assert rows[0]["cells"][0]["physical_sources"]
    context = TableUnderstanding.model_validate({"rows": [{"group": "G1", "kind": "context", "context_role": "header"}]})
    rows, errors = expand_understanding(context, reading_context())
    assert not errors and rows[0]["kind"] == "context" and rows[0]["cells"] == []


def test_canonical_identity_axes_reject_year_keys_but_extensions_are_open():
    with pytest.raises(ValidationError):
        TableUnderstanding.model_validate({"rows": [{"group": "G1", "kind": "quantitative", "fields": [{"name": "2024", "value": "1.70"}]}]})
    result = TableUnderstanding.model_validate({"rows": [{"group": "G1", "kind": "quantitative", "extensions": [{"name": "activity_type", "value": "saved"}]}]})
    assert result.rows[0].extensions == {"activity_type": "saved"}


def test_nuextract_compatibility_resolves_refs_without_changing_direct_templates():
    native = nuextract_template(plan_template())
    assert native["actions"][0]["tool"] == ["inspect_object", "map_object", "search_report", "read_object_context", "request_local_repair", "finish"]
    assert "$defs" not in native
    assert nuextract_template({"G": [{"period": "verbatim-string", "amount": "number"}]}) == {"G": [{"period": "verbatim-string", "amount": "number"}]}
