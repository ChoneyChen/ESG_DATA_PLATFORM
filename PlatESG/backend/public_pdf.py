"""Resolve a frozen published source within registered local PDF roots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def published_pdf(descriptor: dict, roots: list[Path]) -> Path:
    digest = descriptor["source_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise FileNotFoundError("Published PDF identity is unavailable")
    for directory in roots:
        root = directory.expanduser().resolve()
        catalog = root / "report-catalog.json"
        if not catalog.is_file():
            continue
        records = json.loads(catalog.read_text(encoding="utf-8")).get("assets", [])
        for record in records:
            if record.get("sha256") != digest:
                continue
            relative = record.get("relative_path")
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError("Registered source path is invalid")
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root) or candidate.suffix.lower() != ".pdf":
                raise ValueError("Registered source path is outside the PDF collection")
            if not candidate.is_file():
                continue
            actual = hashlib.sha256()
            with candidate.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    actual.update(block)
            if actual.hexdigest() != digest:
                raise ValueError("Registered PDF differs from the published source")
            return candidate
    raise FileNotFoundError("The published PDF is not available in the registered collection")
