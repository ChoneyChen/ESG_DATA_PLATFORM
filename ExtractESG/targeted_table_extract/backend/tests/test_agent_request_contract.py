import pytest

from esg_targeted.contracts import ExtractionRequest


def test_agent_request_accepts_local_qwen_and_explicit_budget():
    fields = dict(ir_run_id="ir-fixture", package_id="esrs.e1-6", package_version="2.0.0")
    request = ExtractionRequest(**fields, semantic_provider="local_qwen", agent_max_steps=12)
    assert request.model_dump(mode="json")["agent_max_steps"] == 12
    assert ExtractionRequest(**fields).agent_max_steps == 8
    for invalid in (0, 25):
        with pytest.raises(ValueError):
            ExtractionRequest(**fields, agent_max_steps=invalid)
