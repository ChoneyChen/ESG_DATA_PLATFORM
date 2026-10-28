"""Report-scoped immutable evidence and neutral interpretation cache."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ReportEvidenceStore:
    def __init__(self, root: Path, *, document_id: str, ir_run_id: str, ir_revision: int):
        self.root = root
        self.document_id = document_id
        self.ir_run_id = ir_run_id
        self.ir_revision = ir_revision
        self.identity = {"document_id": document_id, "ir_run_id": ir_run_id, "ir_revision": ir_revision}

    def _path(self, kind: str) -> Path:
        digest = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()[:20]
        return self.root / self.document_id / digest / f"{kind}.json"

    def save(self, kind: str, payload: dict) -> Path:
        if kind not in {"inventory", "neutral_facts", "interpretations"}:
            raise ValueError(f"Unsupported report evidence kind: {kind}")
        target = self._path(kind)
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {**self.identity, "kind": kind, "payload": payload}
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return target

    def load(self, kind: str) -> dict | None:
        target = self._path(kind)
        if not target.is_file():
            return None
        document = json.loads(target.read_text())
        if {key: document.get(key) for key in self.identity} != self.identity or document.get("kind") != kind:
            raise ValueError(f"Evidence identity mismatch: {kind}")
        return document["payload"]

    def neutral_facts(self, facts: list[dict]) -> list[dict]:
        cached = self.load("neutral_facts")
        if cached is not None:
            return cached["facts"] if isinstance(cached, dict) and "facts" in cached else cached
        self.save("neutral_facts", {"facts": facts})
        return facts

    def interpretation(self, *, object_id: str, clause_contract: dict, payload: dict | None) -> dict | None:
        cached = self.load("interpretations") or {}
        key = hashlib.sha256(json.dumps([object_id, clause_contract], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if payload is None:
            return cached.get(key)
        if key in cached and cached[key] != payload:
            raise ValueError(f"Conflicting neutral interpretation: {object_id}")
        cached[key] = payload
        self.save("interpretations", cached)
        return payload
