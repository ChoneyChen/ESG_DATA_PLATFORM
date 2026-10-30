"""Canonical JSON content identity shared by module interfaces."""

import hashlib, json


def digest(value):
    data = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
