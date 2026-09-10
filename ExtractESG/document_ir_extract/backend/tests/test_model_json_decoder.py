from __future__ import annotations

import pytest

from esg_v2.document.convergence_engine import ConvergenceEngine
from esg_v2.document.model_json_decoder import ModelJsonObjectDecoder
from esg_v2.document.review_response_adapter import ReviewResponseAdapter


def test_decoder_closes_missing_patch_object_without_changing_values() -> None:
    content = (
        '{"verdict":"confirm","patches":['
        '{"target_id":"figure-1","operation":"upsert_chart_spec",'
        '"proposed_value":{"chart_type":"stacked_bar","categories":["A"],'
        '"series":[{"name":"S","points":[{"category":"A","value":30}]}]}, '
        '{"target_id":"figure-1","operation":"set_figure_legend_text",'
        '"proposed_value":["S"]}],"confidence":0.95}<|im_end|>'
    )
    raw_response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ]
    }

    payload = ReviewResponseAdapter.message_json(raw_response)

    assert len(payload["patches"]) == 2
    assert payload["patches"][0]["proposed_value"]["series"][0]["points"][0]["value"] == 30
    assert ModelJsonObjectDecoder.MISSING_ARRAY_ITEM_OBJECT_CLOSER in payload["quality_flags"]


def test_decoder_rejects_non_lossless_json_repair() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        ModelJsonObjectDecoder.decode('{"verdict": confirm, "confidence": 0.9}')


def test_decoder_closes_array_before_sibling_property_without_changing_chart_values() -> None:
    content = (
        '{"verdict":"propose_patch","patches":[{"target_id":"figure-1",'
        '"operation":"upsert_chart_spec","proposed_value":{"chart_type":"other",'
        '"series":[{"name":"Series","points":[{"category":"A","value":42},'
        '"visual_evidence_refs":["crop-1"]}]},"confidence":0.94}]}'
    )
    payload = ReviewResponseAdapter.message_json(
        {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    )

    chart = payload["patches"][0]["proposed_value"]
    assert chart["series"][0]["points"][0]["value"] == 42
    assert chart["series"][0]["visual_evidence_refs"] == ["crop-1"]
    assert ModelJsonObjectDecoder.MISSING_ARRAY_CLOSER_BEFORE_SIBLING in payload["quality_flags"]


def test_invalid_model_json_is_classified_as_model_protocol() -> None:
    classification = ConvergenceEngine.reason_failure(
        "all_reviewer_models_failed: local/model: invalid_response: "
        "Response content is not valid JSON"
    )

    assert classification == {
        "failure_class": "model_protocol",
        "failure_owner": "model",
        "retryable": True,
    }


def test_internal_review_error_is_classified_as_system_contract() -> None:
    classification = ConvergenceEngine.reason_failure(
        "agent_review_internal_error: AttributeError: missing field"
    )

    assert classification == {
        "failure_class": "system_contract",
        "failure_owner": "system",
        "retryable": False,
    }
