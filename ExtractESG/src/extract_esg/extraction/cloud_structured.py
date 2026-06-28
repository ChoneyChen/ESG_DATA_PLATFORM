from __future__ import annotations

import json
import re
from typing import Any

from extract_esg.contracts.base import SourceTrace
from extract_esg.contracts.cloud import CloudTaskResult
from extract_esg.contracts.extraction import DisclosureObject


DISCLOSURE_TYPES = {
    "quantitative",
    "qualitative",
    "target",
    "policy",
    "risk",
    "governance",
    "methodology",
}


def parse_structured_disclosures(
    result: CloudTaskResult,
    *,
    report_id: str,
    allowed_evidence_ids: set[str],
) -> tuple[list[DisclosureObject], dict[str, Any]]:
    """Parse a Qiniu chat completion into disclosure candidates.

    The parser is deliberately strict about publishability but tolerant about
    model formatting. It extracts JSON from plain or fenced responses, then
    converts only evidence-backed items into candidate DisclosureObjects.
    """
    content = _message_content(result)
    parsed = _loads_json_object(content)
    raw_items = parsed.get("disclosures", parsed if isinstance(parsed, list) else [])
    if not isinstance(raw_items, list):
        raw_items = []

    source = SourceTrace(
        run_id=result.request_id,
        producer="QiniuModelAdapter",
        version=result.model_id,
        metadata={"parser": "structured-disclosure-parser-v0.1"},
    )
    disclosures: list[DisclosureObject] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        evidence_ids = [str(value) for value in item.get("evidence_ids", []) if str(value) in allowed_evidence_ids]
        quality_flags = ["cloud_candidate"]
        if not evidence_ids:
            quality_flags.append("missing_valid_evidence_id")

        raw_text = str(item.get("raw_text") or item.get("evidence_text") or item.get("summary") or "").strip()
        raw_label = str(item.get("raw_label") or item.get("label") or f"cloud_disclosure_{index + 1}").strip()
        disclosure_type = str(item.get("disclosure_type") or "qualitative").strip().lower()
        if disclosure_type not in DISCLOSURE_TYPES:
            quality_flags.append(f"normalized_disclosure_type:{disclosure_type}")
            disclosure_type = "qualitative"
        if not raw_text:
            quality_flags.append("missing_raw_text")
            raw_text = raw_label

        disclosures.append(
            DisclosureObject(
                report_id=report_id,
                raw_label=raw_label[:500],
                raw_text=raw_text[:4000],
                evidence_ids=evidence_ids,
                disclosure_type=disclosure_type,  # type: ignore[arg-type]
                concept_candidates=[str(value)[:200] for value in item.get("concept_candidates", []) if value],
                quality_flags=quality_flags,
                source=source,
            )
        )

    parsed_payload = parsed if isinstance(parsed, dict) else {"disclosures": parsed}
    return disclosures, parsed_payload


def _message_content(result: CloudTaskResult) -> str:
    choices = result.raw_response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        return "\n".join(str(part.get("text", part)) for part in content)
    return str(content)


def _loads_json_object(content: str) -> dict[str, Any] | list[Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not start_candidates:
            return {"disclosures": []}
        start = min(start_candidates)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            return {"disclosures": []}
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"disclosures": []}

