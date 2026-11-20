"""Compile a small output contract for each provider's actual prompt protocol."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


STRUCTURED_OUTPUT_RULES = (
    "Return one JSON object matching OUTPUT_SCHEMA, without reasoning or prose. "
    "Use null only for a property whose schema explicitly permits null. "
    "Required source, metric, object and physical reference IDs must be nonempty supplied IDs. "
    "Use [] for empty arrays, never null. Omit unsupported optional business fields; "
    "do not fabricate a source or a column for a negative decision."
)


def output_properties(pairs):
    """Accept padding inside JSON keys while retaining their values verbatim."""
    result = {}
    for key, value in pairs:
        field = key.strip()
        if field not in result or field == key:
            result[field] = value
    return result


def structured_prompt(instructions: str, context: str, schema: dict) -> str:
    return "\n\n".join((
        instructions,
        STRUCTURED_OUTPUT_RULES,
        "OUTPUT_SCHEMA=" + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        "EVIDENCE=" + context,
    ))


NATIVE_TYPES = {
    "verbatim-string",
    "string",
    "integer",
    "number",
    "boolean",
    "date-time",
    "date",
    "time",
    "duration",
    "country",
    "currency",
    "language",
    "language-tag",
    "url",
    "email-address",
    "phone-number",
    "iban",
    "bic",
    "unit-code",
}
JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def json_schema(template: Any) -> dict:
    """Accept a JSON Schema or translate a compact typed shape, not prose types."""
    if isinstance(template, type) and issubclass(template, BaseModel):
        return template.model_json_schema()
    if isinstance(template, BaseModel):
        return type(template).model_json_schema()
    if isinstance(template, dict):
        declared = template.get("type")
        if (
            any(key in template for key in ("$ref", "anyOf", "oneOf", "allOf"))
            or (isinstance(declared, str) and declared in JSON_TYPES)
            or (
                isinstance(declared, list)
                and all(isinstance(v, str) and v in JSON_TYPES for v in declared)
            )
        ):
            return template
        return {
            "type": "object",
            "properties": {k: json_schema(v) for k, v in template.items()},
            "required": list(template),
            "additionalProperties": False,
        }
    if isinstance(template, list):
        if (
            template
            and all(isinstance(v, str) for v in template)
            and not any(v in NATIVE_TYPES for v in template)
        ):
            return {"type": "string", "enum": template}
        if len(template) == 2 and template[0] is None:
            return {"type": ["string", "null"]}
        return {
            "type": "array",
            "items": json_schema(template[0]) if template else {"type": "string"},
        }
    if isinstance(template, bool):
        return {"type": "boolean"}
    if isinstance(template, (float, int)):
        return {"type": "number"}
    if isinstance(template, str) and template in {"integer", "number", "boolean"}:
        return {"type": template}
    if isinstance(template, str) and template in NATIVE_TYPES:
        return {"type": "string"}
    return {"type": ["string", "null"]}
