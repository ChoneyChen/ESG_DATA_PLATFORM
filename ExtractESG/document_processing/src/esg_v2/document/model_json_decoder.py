from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecodedJsonObject:
    value: dict[str, Any]
    repair_codes: tuple[str, ...] = ()


class ModelJsonObjectDecoder:
    """Strict JSON decoder with narrowly scoped, lossless container repairs.

    The repair never changes strings, numbers, keys, or values. It only closes an
    object when the next object clearly starts as a sibling in the enclosing array.
    All repaired output must still pass the standard JSON decoder.
    """

    MISSING_ARRAY_ITEM_OBJECT_CLOSER = "adapter_json_missing_array_item_object_closer_repaired"
    MISSING_ARRAY_CLOSER_BEFORE_SIBLING = "adapter_json_missing_array_closer_before_sibling_repaired"

    @classmethod
    def decode(cls, text: str) -> DecodedJsonObject:
        candidates = [text]
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            bounded = text[start : end + 1]
            if bounded != text:
                candidates.append(bounded)

        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_error = exc
                repairs = (
                    (
                        cls._close_array_item_object(candidate, exc),
                        cls.MISSING_ARRAY_ITEM_OBJECT_CLOSER,
                    ),
                    (
                        cls._close_array_before_sibling_property(candidate),
                        cls.MISSING_ARRAY_CLOSER_BEFORE_SIBLING,
                    ),
                )
                repaired_value = None
                repair_code = None
                for repaired, code in repairs:
                    if repaired is None:
                        continue
                    try:
                        repaired_value = json.loads(repaired)
                    except json.JSONDecodeError as repaired_exc:
                        last_error = repaired_exc
                        continue
                    repair_code = code
                    break
                if repaired_value is None:
                    continue
                if not isinstance(repaired_value, dict):
                    raise ValueError("Response JSON must be an object")
                return DecodedJsonObject(value=repaired_value, repair_codes=(str(repair_code),))
            if not isinstance(value, dict):
                raise ValueError("Response JSON must be an object")
            return DecodedJsonObject(value=value)

        if last_error is not None:
            raise ValueError(f"Response content is not valid JSON: {last_error}") from last_error
        raise ValueError("Response content is not valid JSON")

    @classmethod
    def _close_array_item_object(
        cls,
        text: str,
        error: json.JSONDecodeError,
    ) -> str | None:
        if "expecting property name enclosed in double quotes" not in error.msg.casefold():
            return None
        next_index = cls._next_non_whitespace(text, error.pos)
        if next_index is None or text[next_index] != "{":
            return None
        comma_index = cls._previous_non_whitespace(text, next_index - 1)
        if comma_index is None or text[comma_index] != ",":
            return None
        stack = cls._container_stack(text[:comma_index])
        if stack is None or len(stack) < 2 or stack[-2:] != ["[", "{"]:
            return None
        return f"{text[:comma_index]}}}{text[comma_index:]}"

    @classmethod
    def _close_array_before_sibling_property(cls, text: str) -> str | None:
        in_string = False
        escaped = False
        for index, character in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
                continue
            if character != ",":
                continue
            key_start = cls._next_non_whitespace(text, index + 1)
            if key_start is None or text[key_start] != '"':
                continue
            key_end = key_start + 1
            while key_end < len(text):
                if text[key_end] == '"' and text[key_end - 1] != "\\":
                    break
                key_end += 1
            colon = cls._next_non_whitespace(text, key_end + 1)
            if colon is None or text[colon] != ":":
                continue
            stack = cls._container_stack(text[:index])
            if stack and stack[-1] == "[":
                return f"{text[:index]}]{text[index:]}"
        return None

    @staticmethod
    def _next_non_whitespace(text: str, start: int) -> int | None:
        for index in range(max(0, start), len(text)):
            if not text[index].isspace():
                return index
        return None

    @staticmethod
    def _previous_non_whitespace(text: str, start: int) -> int | None:
        for index in range(min(start, len(text) - 1), -1, -1):
            if not text[index].isspace():
                return index
        return None

    @staticmethod
    def _container_stack(text: str) -> list[str] | None:
        stack: list[str] = []
        in_string = False
        escaped = False
        for character in text:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
                continue
            if character in "[{":
                stack.append(character)
                continue
            if character in "]}":
                expected = "[" if character == "]" else "{"
                if not stack or stack[-1] != expected:
                    return None
                stack.pop()
        return None if in_string else stack
