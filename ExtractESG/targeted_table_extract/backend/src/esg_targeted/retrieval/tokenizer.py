from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[a-zA-Z]+(?:-[a-zA-Z0-9]+)*|\d+(?:\.\d+)?|[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text.lower()):
        value = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            tokens.extend(value)
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return tokens

