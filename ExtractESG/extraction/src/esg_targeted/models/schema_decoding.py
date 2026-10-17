"""Explicit decoding projection; the task receiver retains the full contract.

MLX-VLM 0.6.16 / llguidance 1.8 cannot compile every JSON Schema assertion.
Only the named assertions below may be deferred to the receiving validator.
No lenient compiler mode, output conversion, or mutable matcher cache is used.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary


PROJECTION_VERSION = "mlx-llguidance-structure.v1"
DEFERRED_ASSERTIONS = frozenset({"propertyNames", "if", "then", "else", "uniqueItems"})
SCHEMA_MAPS = frozenset({"$defs", "definitions", "properties", "patternProperties"})
SCHEMA_VALUES = frozenset({"items", "additionalProperties"})
SCHEMA_ARRAYS = frozenset({"anyOf", "oneOf", "allOf", "prefixItems"})
# JSON Schema annotations do not assert anything about an instance. Pydantic's
# discriminator is also an annotation; oneOf/const carry its actual constraints.
ANNOTATIONS = frozenset({
    "$schema", "$id", "$anchor", "$comment", "title", "description", "default",
    "examples", "deprecated", "readOnly", "writeOnly", "discriminator",
})
SUPPORTED_ASSERTIONS = frozenset({
    "$ref", "type", "const", "enum", "required", "minItems", "maxItems",
    "minProperties", "maxProperties", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
}) | SCHEMA_MAPS | SCHEMA_VALUES | SCHEMA_ARRAYS
_TOKENIZERS: WeakKeyDictionary = WeakKeyDictionary()
_TOKENIZER_LOCK = RLock()


class SchemaDecodingError(ValueError):
    """A contract cannot safely be installed as a decoding constraint."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _pointer(path: str, key: str) -> str:
    return path + "/" + key.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class DecodingProjection:
    schema: dict | bool
    original_schema_sha256: str
    decoder_schema_sha256: str
    deferred_constraints: tuple[tuple[str, str], ...]

    def telemetry(self) -> dict:
        return {
            "projection_version": PROJECTION_VERSION,
            "original_schema_sha256": self.original_schema_sha256,
            "decoder_schema_sha256": self.decoder_schema_sha256,
            "deferred_constraints": [
                {"path": path, "keyword": keyword, "enforcement": "task_receiver"}
                for path, keyword in self.deferred_constraints
            ],
            "post_validation": "required_by_task_receiver",
            "lenient": False,
            "fresh_matcher_per_request": True,
            "property_order": "schema_order",
            "dynamic_property_unique_keys": "task_receiver",
        }


def decoding_projection(schema: dict | bool) -> DecodingProjection:
    """Preserve known structural assertions and record every deferred assertion.

    Recurse only through schema positions. A business property called `if`, or
    a literal inside an enum/default, must never be interpreted as a keyword.
    Unknown assertion keywords fail before token generation, rather than being
    silently discarded by an implementation that treats them as annotations.
    """
    deferred: list[tuple[str, str]] = []

    def visit(node: Any, path: str) -> dict | bool:
        if isinstance(node, bool):
            return node
        if not isinstance(node, dict):
            raise SchemaDecodingError(f"Expected a schema object or boolean at {path or '/'}")
        projected = {}
        for key, value in node.items():
            location = _pointer(path, key)
            if key in DEFERRED_ASSERTIONS:
                deferred.append((location, key))
                continue
            if key not in ANNOTATIONS | SUPPORTED_ASSERTIONS:
                raise SchemaDecodingError(f"Unknown or unsupported decoding keyword at {location}")
            if key == "$ref" and (not isinstance(value, str) or not value.startswith("#")):
                raise SchemaDecodingError(f"Only local schema references are allowed at {location}")
            if key in SCHEMA_MAPS:
                if not isinstance(value, dict):
                    raise SchemaDecodingError(f"Expected a schema map at {location}")
                projected[key] = {name: visit(child, _pointer(location, name))
                                  for name, child in value.items()}
            elif key in SCHEMA_VALUES:
                projected[key] = visit(value, location)
            elif key in SCHEMA_ARRAYS:
                if not isinstance(value, list):
                    raise SchemaDecodingError(f"Expected a schema array at {location}")
                projected[key] = [visit(child, _pointer(location, str(i)))
                                  for i, child in enumerate(value)]
            else:
                projected[key] = deepcopy(value)
        return projected

    projected = visit(schema, "")
    return DecodingProjection(projected, _digest(schema), _digest(projected), tuple(deferred))


def build_decoding_constraint(tokenizer, schema: dict | bool):
    """Build a new official MLX processor; matcher state is request-local."""
    projection = decoding_projection(schema)
    try:
        import llguidance
        import llguidance.hf
        from mlx_vlm.structured import LLGuidanceLogitsProcessor

        # The installed helper's id(tokenizer) cache can outlive an unloaded
        # model and collide with a later Python object ID. A weak identity key
        # preserves tokenizer reuse while releasing retired model tokenizers.
        with _TOKENIZER_LOCK:
            llg_tokenizer = _TOKENIZERS.get(tokenizer)
            if llg_tokenizer is None:
                llg_tokenizer = llguidance.hf.from_tokenizer(tokenizer)
                _TOKENIZERS[tokenizer] = llg_tokenizer
        grammar = llguidance.JsonCompiler(
            separators=(", ", ": "), whitespace_pattern="",
        ).compile(json.dumps(projection.schema), check=True)
        processor = LLGuidanceLogitsProcessor(grammar, llg_tokenizer)
    except Exception as exc:
        raise SchemaDecodingError(f"Strict decoding constraint compilation failed: {exc}") from exc
    return processor, projection.telemetry()
