"""Compile a small output contract for each provider's actual prompt protocol."""
from __future__ import annotations

from typing import Any
from copy import deepcopy

from pydantic import BaseModel


STRUCTURED_OUTPUT_RULES = (
    "Return one JSON object matching OUTPUT_SCHEMA, without reasoning or prose. "
    "Use null only for a property whose schema explicitly permits null. "
    "Required source, metric, object and physical reference IDs must be nonempty supplied IDs. "
    "Use [] for empty arrays, never null. Omit unsupported optional business fields; "
    "do not fabricate a source or a column for a negative decision."
)


NATIVE_TYPES = {
    "verbatim-string", "string", "integer", "number", "boolean", "date-time",
    "date", "time", "duration", "country", "currency", "language",
    "language-tag", "url", "email-address", "phone-number", "iban", "bic",
    "unit-code",
}
TYPE_FIELDS = {"reason", "rationale", "interpretation_note", "source_id", "measurement_kind"}
JSON_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def json_schema(template: Any) -> dict:
    """Accept a JSON Schema or translate a compact typed shape, not prose types."""
    if isinstance(template, type) and issubclass(template, BaseModel):
        return template.model_json_schema()
    if isinstance(template, BaseModel):
        return type(template).model_json_schema()
    if isinstance(template, dict):
        declared = template.get("type")
        if any(key in template for key in ("$ref", "anyOf", "oneOf", "allOf")) or (isinstance(declared, str) and declared in JSON_TYPES) or (
            isinstance(declared, list) and all(isinstance(v, str) and v in JSON_TYPES for v in declared)
        ):
            return template
        return {
            "type": "object", "properties": {k: json_schema(v) for k, v in template.items()},
            "required": list(template), "additionalProperties": False,
        }
    if isinstance(template, list):
        if template and all(isinstance(v, str) for v in template) and not any(v in NATIVE_TYPES for v in template):
            return {"type": "string", "enum": template}
        if len(template) == 2 and template[0] is None:
            return {"type": ["string", "null"]}
        return {"type": "array", "items": json_schema(template[0]) if template else {"type": "string"}}
    if isinstance(template, bool):
        return {"type": "boolean"}
    if isinstance(template, (float, int)):
        return {"type": "number"}
    if isinstance(template, str) and template in {"integer", "number", "boolean"}:
        return {"type": template}
    if isinstance(template, str) and template in NATIVE_TYPES:
        return {"type": "string"}
    return {"type": ["string", "null"]}


def _resolve_references(schema: Any, definitions: dict) -> Any:
    if isinstance(schema, list):
        return [_resolve_references(value, definitions) for value in schema]
    if not isinstance(schema, dict):
        return schema
    schema = dict(schema)
    if "$ref" in schema:
        reference = schema.pop("$ref")
        if not reference.startswith("#/$defs/") or reference.rsplit("/", 1)[-1] not in definitions:
            raise ValueError(f"Unknown structured contract reference {reference}")
        schema = {**definitions[reference.rsplit("/", 1)[-1]], **schema}
    return {key: _resolve_references(value, definitions) for key, value in schema.items() if key != "$defs"}


def nuextract_template(template: Any, *, field: str = "") -> Any:
    """NuExtract leaf values are native type names or enum values, never notes.

    Nullability is handled by NuExtract's documented absent-value behavior. The
    description belongs in instructions; it is not an additional enum choice.
    """
    if isinstance(template, type) and issubclass(template, BaseModel):
        template = template.model_json_schema()
    if isinstance(template, dict):
        if "$defs" in template:
            template = _resolve_references(deepcopy(template), template["$defs"])
        if "const" in template:
            return [template["const"]]
        if "enum" in template:
            return [v for v in template["enum"] if v is not None]
        if "anyOf" in template or "oneOf" in template:
            concrete = [v for v in template.get("oneOf", template.get("anyOf", [])) if v.get("type") != "null"]
            if concrete and all(v.get("type") == "object" for v in concrete):
                # NuExtract's native template has no tool-discriminated union.
                # A legacy brain gets the superset shape; the actual typed
                # action is still checked after generation. Direct specialist
                # templates never pass through this compatibility branch.
                properties = {}
                for variant in concrete:
                    for key, value in variant.get("properties", {}).items():
                        if key == "tool":
                            properties.setdefault(key, []).extend(value.get("enum", [value.get("const")]))
                        else:
                            properties[key] = nuextract_template(value, field=key)
                return properties
            return nuextract_template(concrete[0], field=field) if concrete else "string"
        kind = template.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "string")
        if kind == "object":
            return {k: nuextract_template(v, field=k) for k, v in template.get("properties", {}).items()}
        if kind == "array":
            return [nuextract_template(template.get("items", "string"), field=field)]
        if kind in {"integer", "number", "boolean"}:
            return kind
        if kind in {"string", "null"}:
            return "string" if field in TYPE_FIELDS else "verbatim-string"
        return {k: nuextract_template(v, field=k) for k, v in template.items()}
    if isinstance(template, list):
        if len(template) == 2 and template[0] is None:
            return "string" if field in TYPE_FIELDS else "verbatim-string"
        if template and all(isinstance(v, str) for v in template):
            return template
        return [nuextract_template(template[0], field=field)] if template else ["string"]
    if isinstance(template, str) and template in NATIVE_TYPES:
        return template
    if isinstance(template, bool):
        return "boolean"
    if isinstance(template, (float, int)):
        return "number"
    return "string" if field in TYPE_FIELDS else "verbatim-string"
