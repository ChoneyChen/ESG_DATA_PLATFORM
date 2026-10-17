from __future__ import annotations

from copy import deepcopy
import gc
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from esg_targeted.models.errors import ModelRunFailure, ModelCancelled
from esg_targeted.models.preflight import PromptBudgetExceeded
from esg_targeted.models.qwen import QwenMlxModel
from esg_targeted.models.schema_decoding import (
    SchemaDecodingError, build_decoding_constraint, decoding_projection,
)


def test_projection_defers_only_explicit_assertions_without_mutating_contract():
    schema = {"type": "object", "properties": {
        "field_refs": {"type": "object", "propertyNames": {"minLength": 1},
                       "additionalProperties": {"type": "array", "uniqueItems": True,
                                                "items": {"enum": ["S1", "S2"]}}},
        # These are names/instance literals, not schema keywords.
        "if": {"type": "string", "enum": ["then"], "default": {"if": "literal"}},
    }, "required": ["field_refs"], "additionalProperties": False,
        "allOf": [{"if": {"required": ["if"]}, "then": {"required": ["field_refs"]}}]}
    original = deepcopy(schema)
    projection = decoding_projection(schema)
    assert schema == original
    assert projection.schema["properties"]["if"] == original["properties"]["if"]
    assert projection.schema["properties"]["field_refs"]["additionalProperties"]["items"] == {"enum": ["S1", "S2"]}
    assert projection.schema["required"] == ["field_refs"]
    assert projection.schema["additionalProperties"] is False
    assert projection.telemetry()["deferred_constraints"] == [
        {"path": "/properties/field_refs/propertyNames", "keyword": "propertyNames", "enforcement": "task_receiver"},
        {"path": "/properties/field_refs/additionalProperties/uniqueItems", "keyword": "uniqueItems", "enforcement": "task_receiver"},
        {"path": "/allOf/0/if", "keyword": "if", "enforcement": "task_receiver"},
        {"path": "/allOf/0/then", "keyword": "then", "enforcement": "task_receiver"},
    ]
    assert projection.telemetry()["post_validation"] == "required_by_task_receiver"
    assert projection.telemetry()["lenient"] is False
    assert projection.original_schema_sha256 != projection.decoder_schema_sha256


@pytest.mark.parametrize("schema", [
    {"type": "object", "unexpectedAssertion": True},
    {"type": "object", "x-guidance": {"lenient": True}},
    {"$ref": "https://example.invalid/remote-schema"},
    {"type": "array", "contains": {"type": "integer"}},
    {"properties": {"field": {"not": {"type": "null"}}}},
])
def test_unknown_constraints_fail_closed(schema):
    with pytest.raises(SchemaDecodingError):
        decoding_projection(schema)


def test_tokenizer_identity_cache_reuses_only_live_objects(monkeypatch):
    pytest.importorskip("mlx.core")
    llguidance_hf = pytest.importorskip("llguidance.hf")
    calls = []
    def convert(tokenizer):
        result = object()
        calls.append(result)
        return result
    monkeypatch.setattr(llguidance_hf, "from_tokenizer", convert)
    class Tokenizer:
        pass
    tokenizer = Tokenizer()
    reference = weakref.ref(tokenizer)
    schema = {"type": "string"}
    first, _ = build_decoding_constraint(tokenizer, schema)
    second, _ = build_decoding_constraint(tokenizer, schema)
    assert len(calls) == 1 and first.llg_tokenizer is second.llg_tokenizer
    del tokenizer
    gc.collect()
    assert reference() is None  # cache must not retain retired model processors
    third, _ = build_decoding_constraint(Tokenizer(), schema)
    assert len(calls) == 2 and third.llg_tokenizer is not first.llg_tokenizer


def _fake_runtime(monkeypatch, output="{\"value\": \"111\"}", finish_reason="stop"):
    model = QwenMlxModel(Path("/offline/qwen"))
    model._model = SimpleNamespace(config={})
    model._processor = SimpleNamespace(encode=lambda prompt: list(prompt))
    model._render_prompt = lambda **_: "short prompt"
    captured, closed = {}, []
    module = ModuleType("mlx_vlm.generate")
    def stream(*args, **kwargs):
        captured.update(kwargs)
        try:
            # Streaming text is a delta, even when token metadata repeats.
            yield SimpleNamespace(text=output[:10], generation_tokens=5, finish_reason=None, token=16)
            yield SimpleNamespace(text=output[10:], generation_tokens=9, finish_reason=finish_reason, token=16)
            yield SimpleNamespace(text="", generation_tokens=9, finish_reason=finish_reason, token=16)
        finally:
            closed.append(True)
    module.stream_generate = stream
    monkeypatch.setitem(sys.modules, "mlx_vlm.generate", module)
    return model, captured, closed


def test_qwen_installs_fresh_processor_and_returns_unmodified_raw_for_receiver(monkeypatch):
    model, captured, closed = _fake_runtime(monkeypatch, output='{"rows":[{"invalid":true}]}')
    processors = []
    def build(tokenizer, schema):
        processors.append(object())
        return processors[-1], decoding_projection(schema).telemetry()
    monkeypatch.setattr("esg_targeted.models.qwen.build_decoding_constraint", build)
    template = {"type": "object", "propertyNames": {"minLength": 1}}
    for _ in range(2):
        raw, telemetry = model.generate_structured(context={}, instructions="read", template=template)
        assert raw == '{"rows":[{"invalid":true}]}'  # no all-batch schema rejection or silent rewrite
        assert captured["logits_processors"] == [processors[-1]]
        assert captured["enable_thinking"] is False
        assert telemetry["schema_enforcement"] == "grammar_structure_with_deferred_constraints"
        assert telemetry["schema_decoding"]["post_validation"] == "required_by_task_receiver"
    assert processors[0] is not processors[1]
    assert closed == [True, True]


def test_compile_failure_prevents_generation_and_never_falls_back(monkeypatch):
    model, captured, closed = _fake_runtime(monkeypatch)
    def fail(*args):
        raise SchemaDecodingError("unsupported keyword")
    monkeypatch.setattr("esg_targeted.models.qwen.build_decoding_constraint", fail)
    with pytest.raises(ModelRunFailure) as failure:
        model.generate_structured(context={}, instructions="read", template={"value": "string"})
    assert failure.value.category == "request_config"
    assert failure.value.telemetry["schema_enforcement"] == "constraint_not_installed"
    assert captured == {} and closed == []


def test_prompt_budget_is_checked_before_constraint_build(monkeypatch):
    model, captured, _ = _fake_runtime(monkeypatch)
    model.max_input_tokens = 2
    def never(*args):
        pytest.fail("grammar must not be prepared for a rejected oversized prompt")
    monkeypatch.setattr("esg_targeted.models.qwen.build_decoding_constraint", never)
    with pytest.raises(PromptBudgetExceeded):
        model.generate_structured(context={}, instructions="read", template={"value": "string"})
    assert captured == {}


def test_cancel_and_truncation_preserve_constraint_telemetry_and_partial_raw(monkeypatch):
    model, _, closed = _fake_runtime(monkeypatch, output='{"value":', finish_reason="length")
    monkeypatch.setattr("esg_targeted.models.qwen.build_decoding_constraint",
                        lambda tokenizer, schema: (object(), decoding_projection(schema).telemetry()))
    with pytest.raises(ModelRunFailure) as failure:
        model.generate_structured(context={}, instructions="read", template={"value": "string"})
    assert failure.value.category == "generation_incomplete"
    assert failure.value.raw_output == '{"value":'
    assert failure.value.telemetry["schema_enforcement"] == "grammar_schema"
    assert closed == [True]
    model, _, closed = _fake_runtime(monkeypatch)
    checks = iter([False, False, True])
    with pytest.raises(ModelCancelled) as cancelled:
        model.generate_structured(context={}, instructions="read", template={"value": "string"},
                                  cancel_check=lambda: next(checks))
    assert cancelled.value.raw_output
    assert cancelled.value.telemetry["schema_decoding"]["status"] == "installed"
    assert closed == [True]


@pytest.fixture(scope="module")
def local_qwen_tokenizer():
    """Explicit offline integration opt-in; never fetch or load model weights."""
    model_path = os.environ.get("ESG_QWEN_TOKENIZER_TEST_PATH")
    if not model_path:
        pytest.skip("set ESG_QWEN_TOKENIZER_TEST_PATH for the offline local MLX mask check")
    pytest.importorskip("mlx.core")
    pytest.importorskip("llguidance")
    transformers = pytest.importorskip("transformers")
    return transformers.AutoTokenizer.from_pretrained(model_path, local_files_only=True)


def _accepts_text(processor, tokenizer, text):
    import llguidance
    matcher = llguidance.LLMatcher(processor.llg_tokenizer, processor.grammar)
    tokens = tokenizer.encode(text, add_special_tokens=False)
    return matcher.consume_tokens(tokens) and matcher.is_accepting()


def test_real_tokenizer_mlx_mask_repeated_tokens_eos_and_independent_requests(local_qwen_tokenizer):
    import mlx.core as mx
    tokenizer = local_qwen_tokenizer
    schema = {"type": "object", "properties": {"value": {"type": "string", "enum": ["111"]}},
              "required": ["value"], "additionalProperties": False}
    first, details = build_decoding_constraint(tokenizer, schema)
    second, _ = build_decoding_constraint(tokenizer, schema)
    assert first is not second and first.is_first_token and second.is_first_token
    tokens = tokenizer.encode('{"value": "111"}', add_special_tokens=False)
    assert any(tokens[i] == tokens[i + 1] == tokens[i + 2] for i in range(len(tokens) - 2))
    logits = mx.zeros((len(tokenizer),))
    last = tokenizer.eos_token_id
    for token in tokens:
        masked = first.process_last_token(last, logits)
        assert bool(mx.isfinite(masked[token]).item())
        assert not bool(mx.isfinite(masked[tokenizer.eos_token_id]).item())
        last = token
    final_mask = first.process_last_token(last, logits)
    assert first.ll_matchers[0].is_accepting()
    assert bool(mx.isfinite(final_mask[tokenizer.eos_token_id]).item())
    assert second.is_first_token  # the immutable tokenizer can be shared, matcher state cannot
    assert not _accepts_text(second, tokenizer, '{"value": "wrong"}')
    assert details["fresh_matcher_per_request"] is True


def test_real_receiver_schemas_compile_and_bad_ref_direction_is_masked(local_qwen_tokenizer):
    from esg_targeted.models.report_semantics import understanding_template, mapping_template, plan_template
    context = {"rows": [{"group": "G1", "source_id": "R1", "cells": [{"target_cell": "T1", "column": "C1"}]}],
               "allowed_references": {"segments": ["S1"], "metrics": ["M1"]}}
    schema = understanding_template(context=context)
    processor, details = build_decoding_constraint(local_qwen_tokenizer, schema)
    valid = {"rows": [{"field_refs": {"period_year": ["S1"]}, "group": "G1", "kind": "quantitative", "target_cells": ["T1"]}]}
    assert _accepts_text(processor, local_qwen_tokenizer, json.dumps(valid))
    for bad_refs in ({"S1": [{"name": "period_year", "value": 2025}]},
                     {"S1": "period_year"}, {"period_year": ["unknown-span"]}):
        invalid = deepcopy(valid)
        invalid["rows"][0]["field_refs"] = bad_refs
        assert not _accepts_text(processor, local_qwen_tokenizer, json.dumps(invalid))
    assert any(item["keyword"] == "propertyNames" for item in details["deferred_constraints"])
    for schema in (mapping_template(metrics=[{"metric_id": "M1"}], source_ids=["R1", "R2"],
                                    columns={"R1": ["C1"], "R2": ["C2"]}), plan_template(context)):
        processor, details = build_decoding_constraint(local_qwen_tokenizer, schema)
        assert processor.is_first_token


def test_deferred_source_column_condition_is_still_rejected_by_task_receiver(local_qwen_tokenizer):
    from esg_targeted.models.report_semantics import mapping_template, validate_mapping_payload
    context = {"rows": [
        {"source_id": "R1", "group": "G1", "cells": [{"column": "C1"}]},
        {"source_id": "R2", "group": "G2", "cells": [{"column": "C2"}]},
    ], "metrics": [{"metric_id": "M1"}]}
    schema = mapping_template(context=context)
    processor, details = build_decoding_constraint(local_qwen_tokenizer, schema)
    raw = {"matches": [{"source_id": "R1", "metric_id": "M1", "columns": ["C2"]}],
           "coverage": [{"metric_id": "M1", "status": "reviewed", "reason": "checked"}]}
    # C2 exists in this task, so the structural projection can accept it. The
    # full receiver must reject its attribution to R1, independently of grammar.
    assert _accepts_text(processor, local_qwen_tokenizer, json.dumps(raw))
    mapping, issues = validate_mapping_payload(raw, context)
    assert mapping.matches == []
    assert any(issue.stage == "reference" and "column" in issue.path for issue in issues)
    assert {item["keyword"] for item in details["deferred_constraints"]} == {"if", "then"}
