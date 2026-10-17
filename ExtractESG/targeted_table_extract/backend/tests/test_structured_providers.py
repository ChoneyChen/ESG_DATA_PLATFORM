from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from esg_targeted.models.capabilities import local_model_readiness
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.nuextract import NuExtractMlxModel
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.models.qwen import QwenMlxModel
from esg_targeted.models.structured_template import json_schema, nuextract_template
from esg_targeted.models.report_semantics import ObjectMapping, mapping_template
import pytest


def test_native_template_never_uses_description_as_field_type():
    shape = {
        "source_id": "unique R1, R2...",
        "group": "one supplied G alias",
        "rows": [{"value": [None, "original value"], "reason": "brief semantic reason", "match": ["match", "different", "uncertain"]}],
    }
    compiled = nuextract_template(shape)
    assert compiled["source_id"] == "string"
    assert compiled["group"] == "verbatim-string"
    assert compiled["rows"][0]["value"] == "verbatim-string"
    assert compiled["rows"][0]["reason"] == "string"
    assert compiled["rows"][0]["match"] == ["match", "different", "uncertain"]


def test_json_schema_nullable_and_native_schema_conversion():
    schema = {"type": "object", "properties": {"amount": {"type": ["number", "null"]}, "label": {"type": ["string", "null"]}}}
    assert json_schema(schema) is schema
    assert nuextract_template(schema) == {"amount": "number", "label": "verbatim-string"}
    assert json_schema({"rows": [{"reason": "string"}]})["properties"]["rows"]["type"] == "array"


def test_nuextract_small_task_compiles_template_and_respects_budget():
    model = NuExtractMlxModel(Path("/unused/model"), max_tokens=4096)
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return '{"row":"G1"}', {"generation_tokens": 8}

    model._generate = fake_generate
    raw, telemetry = model.generate_structured(context={"row": "G1"}, instructions="read", template={"row": [None, "visible source alias"]}, max_tokens=768)
    assert json.loads(captured["template"]) == {"row": "verbatim-string"}
    assert captured["output_token_budget"] == 768
    assert raw.startswith("{") and telemetry["provider"] == "local_nuextract"


def test_qwen_uses_regular_chat_not_nuextract_template(monkeypatch):
    captured = {}

    def fake_chat(*args, **kwargs):
        captured.update(kwargs)
        captured["prompt"] = args[2]
        return "rendered"

    monkeypatch.setitem(sys.modules, "mlx_vlm", types.ModuleType("mlx_vlm"))
    module = types.ModuleType("mlx_vlm.prompt_utils")
    module.apply_chat_template = fake_chat
    monkeypatch.setitem(sys.modules, "mlx_vlm.prompt_utils", module)
    model = QwenMlxModel(Path("/unused/qwen"))
    model._model = types.SimpleNamespace(config={})
    model._processor = object()
    assert model._render_prompt(context="source", template='{"row":"string"}', instructions="classify", images=["crop.png"]) == "rendered"
    assert "OUTPUT_SCHEMA=" in captured["prompt"]
    assert "template" not in captured and "instructions" not in captured
    assert captured["num_images"] == 1 and captured["enable_thinking"] is False


def test_qiniu_small_task_requests_json_schema_and_returns_telemetry():
    model = QiniuVlmModel(api_key="offline-test", base_url="https://offline.invalid", model_id="fake-vlm", max_output_tokens=4096)
    captured = {}

    def fake_chat(prompt, images, max_tokens, **kwargs):
        captured.update(prompt=prompt, images=images, max_tokens=max_tokens, **kwargs)
        return '{"match":"different"}', {"generation_tokens": 5}

    model._chat = fake_chat
    _, telemetry = model.generate_structured(context="source", instructions="classify", template={"match": ["match", "different"]}, image_path="crop.png", max_tokens=512)
    assert captured["max_tokens"] == 512 and captured["images"] == ["crop.png"]
    assert captured["response_format"]["type"] == "json_schema"
    assert telemetry["provider"] == "qiniu_vlm"


def test_qiniu_structured_task_uses_receiver_schema_and_reports_fallback():
    model = QiniuVlmModel(api_key="offline-test", base_url="https://offline.invalid", model_id="fake-vlm")
    captured = {}
    def fake_chat(prompt, images, max_tokens, **kwargs):
        captured.update(kwargs)
        return '{"matches":[],"coverage":[]}', {"response_format_accepted": "json_object", "finish_reason": "stop"}
    model._chat = fake_chat
    _, telemetry = model.generate_structured(context={}, instructions="Map", template=ObjectMapping)
    assert captured["response_format"]["json_schema"]["schema"] == mapping_template()
    assert telemetry["schema_enforcement"] == "post_generation"


def test_qiniu_structured_output_truncation_is_a_generation_failure():
    model = QiniuVlmModel(api_key="offline-test", base_url="https://offline.invalid", model_id="fake-vlm")
    model._chat = lambda *args, **kwargs: ('{"matches":[', {"finish_reason": "length"})
    with pytest.raises(ModelRunFailure) as failure:
        model.generate_structured(context={}, instructions="Map", template=mapping_template())
    assert failure.value.category == "generation_incomplete"
    assert failure.value.raw_output == '{"matches":['


def test_static_qwen_readiness_requires_registered_runtime_architecture(tmp_path, monkeypatch):
    import esg_targeted.models.capabilities as capabilities

    (tmp_path / "config.json").write_text('{"model_type":"qwen3_5"}', encoding="utf-8")
    (tmp_path / "model.safetensors").touch()
    package = tmp_path / "runtime" / "mlx_vlm" / "models" / "qwen3_5"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    distribution = types.SimpleNamespace(version="offline-test", locate_file=lambda relative: tmp_path / "runtime" / relative)
    monkeypatch.setattr(capabilities.importlib.metadata, "distribution", lambda name: distribution)
    assert local_model_readiness(tmp_path)["available"] is True
    (tmp_path / "config.json").write_text('{"model_type":"future_unknown_model"}', encoding="utf-8")
    failed = local_model_readiness(tmp_path)
    assert failed["available"] is False
    assert "future_unknown_model" in failed["error"]


def test_unknown_qwen_runtime_is_rejected_before_model_load(tmp_path, monkeypatch):
    import esg_targeted.models.qwen as qwen

    monkeypatch.setattr(qwen, "local_model_readiness", lambda path: {"available": False, "error": "unsupported architecture"})
    with pytest.raises(ModelRunFailure, match="unsupported architecture") as failure:
        QwenMlxModel(tmp_path)._ensure_loaded()
    assert failure.value.category == "request_config"
