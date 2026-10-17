from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from esg_targeted.io import write_json


INVENTORY_ID_RE = re.compile(r"^inventory-[a-zA-Z0-9_-]+$")


class SemanticIndexAssetCatalog:
    """Manage semantic indexes independently from extraction result bundles."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict[str, Any]]:
        assets = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not INVENTORY_ID_RE.fullmatch(directory.name):
                continue
            descriptor = self._read_json(directory / "asset.json")
            metadata = self._read_json(directory / "metadata.json")
            matrix_path = directory / "embeddings.npy"
            if not descriptor and not metadata and not matrix_path.is_file():
                continue
            stat = directory.stat()
            assets.append(
                {
                    "inventory_id": directory.name,
                    "document_id": descriptor.get("document_id"),
                    "document_label": descriptor.get("document_label"),
                    "ir_run_id": descriptor.get("ir_run_id"),
                    "ir_revision": descriptor.get("ir_revision"),
                    "model_path": descriptor.get("model_path")
                    or metadata.get("model_path"),
                    "index_policy": descriptor.get("index_policy")
                    or metadata.get("index_policy"),
                    "shape": descriptor.get("shape") or metadata.get("shape") or [],
                    "retrieval_eligible_span_count": descriptor.get(
                        "retrieval_eligible_span_count"
                    )
                    or metadata.get("eligible_span_count", 0),
                    "semantic_indexed_span_count": descriptor.get(
                        "semantic_indexed_span_count"
                    )
                    or metadata.get(
                        "semantic_indexed_span_count",
                        metadata.get("eligible_span_count", 0),
                    ),
                    "size_bytes": self._size(directory),
                    "created_at": descriptor.get("created_at")
                    or datetime.fromtimestamp(stat.st_ctime, UTC).isoformat(),
                    "updated_at": descriptor.get("updated_at")
                    or datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "cache_ready": matrix_path.is_file() and bool(metadata),
                }
            )
        return sorted(
            assets,
            key=lambda item: (str(item.get("updated_at") or ""), item["inventory_id"]),
            reverse=True,
        )

    def record(self, inventory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        directory = self._directory(inventory_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "asset.json"
        existing = self._read_json(path)
        now = datetime.now(UTC).isoformat()
        descriptor = {
            **existing,
            **payload,
            "inventory_id": inventory_id,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        write_json(path, descriptor)
        return descriptor

    def delete(self, inventory_id: str) -> dict[str, Any]:
        directory = self._directory(inventory_id)
        if not directory.exists():
            raise KeyError(inventory_id)
        result = {
            "inventory_id": inventory_id,
            "file_count": sum(path.is_file() for path in directory.rglob("*")),
            "size_bytes": self._size(directory),
        }
        shutil.rmtree(directory)
        return {**result, "deleted": True}

    def _directory(self, inventory_id: str) -> Path:
        if not INVENTORY_ID_RE.fullmatch(inventory_id):
            raise ValueError("invalid semantic index inventory id")
        path = (self.root / inventory_id).resolve()
        if self.root not in path.parents:
            raise ValueError("semantic index path escapes cache root")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _size(directory: Path) -> int:
        return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
