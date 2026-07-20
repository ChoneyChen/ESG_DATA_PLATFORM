from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from esg_v2.storage.package_layout import package_dir, require_package_dir_name


@dataclass(frozen=True)
class RevisionInfo:
    revision: int
    parent_ir_run_id: str | None


class DocumentIrVersionManager:
    def __init__(self, output_root: Path):
        self.output_root = output_root

    def next_revision(self, ocr_run_id: str, parent_ir_run_id: str | None = None) -> RevisionInfo:
        require_package_dir_name(ocr_run_id)
        highest = self._highest_revision(ocr_run_id)
        if parent_ir_run_id:
            parent_manifest = package_dir(self.output_root, parent_ir_run_id) / "manifest.json"
            if not parent_manifest.exists():
                raise FileNotFoundError(f"Parent Document IR run not found: {parent_ir_run_id}")
            payload = json.loads(parent_manifest.read_text(encoding="utf-8"))
            if payload.get("ocr_run_id") != ocr_run_id:
                raise ValueError("Parent Document IR belongs to a different OCR run")
            parent_revision = int(payload.get("ir_revision") or 1)
            return RevisionInfo(max(highest, parent_revision) + 1, parent_ir_run_id)
        return RevisionInfo(highest + 1, None)

    def _highest_revision(self, ocr_run_id: str) -> int:
        highest = 0
        if self.output_root.exists():
            for manifest_path in self.output_root.glob("*/manifest.json"):
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if payload.get("ocr_run_id") == ocr_run_id:
                        highest = max(highest, int(payload.get("ir_revision") or 1))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        return highest

    def list_revisions(self, ocr_run_id: str) -> list[dict[str, object]]:
        require_package_dir_name(ocr_run_id)
        revisions = []
        if not self.output_root.exists():
            return revisions
        for manifest_path in self.output_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("ocr_run_id") != ocr_run_id:
                continue
            revisions.append(
                {
                    "run_id": payload.get("run_id"),
                    "ocr_run_id": ocr_run_id,
                    "ir_revision": int(payload.get("ir_revision") or 1),
                    "parent_ir_run_id": payload.get("parent_ir_run_id"),
                    "schema_version": payload.get("schema_version"),
                    "readiness": payload.get("readiness", "unknown"),
                    "written_at": payload.get("written_at"),
                }
            )
        return sorted(revisions, key=lambda item: int(item["ir_revision"]))
