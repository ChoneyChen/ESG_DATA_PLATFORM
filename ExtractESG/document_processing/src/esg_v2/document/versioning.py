from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from esg_v2.document.identity import (
    LINEAGE_ID_PATTERN,
    lineage_id_from_root_run,
    manifest_document_id,
    manifest_document_label,
)
from esg_v2.storage.package_layout import package_dir, require_package_dir_name


@dataclass(frozen=True)
class RevisionInfo:
    revision: int
    parent_ir_run_id: str | None
    document_id: str
    lineage_id: str


class DocumentIrVersionManager:
    def __init__(self, output_root: Path):
        self.output_root = output_root

    def next_revision(
        self,
        ocr_run_id: str,
        parent_ir_run_id: str | None = None,
        *,
        document_id: str | None = None,
        root_ir_run_id: str | None = None,
    ) -> RevisionInfo:
        require_package_dir_name(ocr_run_id)
        if parent_ir_run_id:
            parent_manifest = package_dir(self.output_root, parent_ir_run_id) / "manifest.json"
            if not parent_manifest.exists():
                raise FileNotFoundError(f"Parent Document IR run not found: {parent_ir_run_id}")
            payload = json.loads(parent_manifest.read_text(encoding="utf-8"))
            if payload.get("ocr_run_id") != ocr_run_id:
                raise ValueError("Parent Document IR belongs to a different OCR run")
            parent_document_id = manifest_document_id(payload)
            if document_id and parent_document_id != document_id:
                raise ValueError("Parent Document IR belongs to a different source PDF")
            lineage_id = self.lineage_id(payload)
            highest = self._highest_revision(lineage_id)
            parent_revision = int(payload.get("ir_revision") or 1)
            return RevisionInfo(
                max(highest, parent_revision) + 1,
                parent_ir_run_id,
                parent_document_id,
                lineage_id,
            )
        root_ir_run_id = root_ir_run_id or f"ir-root-for-{ocr_run_id}"
        resolved_document_id = document_id or manifest_document_id({"ocr_run_id": ocr_run_id})
        return RevisionInfo(1, None, resolved_document_id, lineage_id_from_root_run(root_ir_run_id))

    def _highest_revision(self, lineage_id: str) -> int:
        highest = 0
        if self.output_root.exists():
            for manifest_path in self.output_root.glob("*/manifest.json"):
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if self.lineage_id(payload) == lineage_id:
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
                    "document_id": manifest_document_id(payload),
                    "document_label": manifest_document_label(payload),
                    "lineage_id": self.lineage_id(payload),
                    "ir_revision": int(payload.get("ir_revision") or 1),
                    "parent_ir_run_id": payload.get("parent_ir_run_id"),
                    "schema_version": payload.get("schema_version"),
                    "readiness": payload.get("readiness", "unknown"),
                    "written_at": payload.get("written_at"),
                }
            )
        return sorted(
            revisions,
            key=lambda item: (str(item["lineage_id"]), int(item["ir_revision"]), str(item["run_id"])),
        )

    def lineage_id(self, manifest: dict[str, object]) -> str:
        candidate = str(manifest.get("lineage_id") or "")
        if LINEAGE_ID_PATTERN.fullmatch(candidate):
            return candidate
        root_run_id = str(manifest.get("run_id") or manifest.get("ocr_run_id") or "unknown")
        current = manifest
        visited: set[str] = set()
        while current.get("parent_ir_run_id"):
            parent_run_id = str(current["parent_ir_run_id"])
            if parent_run_id in visited:
                break
            visited.add(parent_run_id)
            parent_manifest = self.output_root / parent_run_id / "manifest.json"
            if not parent_manifest.exists():
                break
            try:
                current = json.loads(parent_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                break
            root_run_id = str(current.get("run_id") or parent_run_id)
            direct = str(current.get("lineage_id") or "")
            if LINEAGE_ID_PATTERN.fullmatch(direct):
                return direct
        return lineage_id_from_root_run(root_run_id)
