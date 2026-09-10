from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esg_v2.document.identity import (
    DOCUMENT_ID_PATTERN,
    manifest_document_id,
    manifest_document_label,
    normalize_sha256,
)
from esg_v2.document.versioning import DocumentIrVersionManager


class DocumentIrCatalog:
    """Derived, read-only catalog over immutable OCR and Document IR packages."""

    def __init__(self, ir_output_root: Path, *, ocr_output_root: Path | None = None):
        self.ir_output_root = ir_output_root
        self.ocr_output_root = ocr_output_root
        self.version_manager = DocumentIrVersionManager(ir_output_root)

    def list_documents(self) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        ir_rows = self._ir_rows()
        linked_documents = {
            str(row["ocr_run_id"]): (str(row["document_id"]), str(row["document_label"]))
            for row in ir_rows
            if row.get("ocr_run_id")
        }
        for row in self._ocr_rows():
            linked = linked_documents.get(str(row["run_id"]))
            if linked and not row.get("source_pdf_sha256"):
                row["document_id"], row["document_label"] = linked
            group = self._group(groups, row)
            group["ocr_runs"].append(row)
        for row in ir_rows:
            group = self._group(groups, row)
            group["ir_runs"].append(row)
            if row.get("external_document_id"):
                group["external_document_ids"].add(str(row["external_document_id"]))

        documents = []
        for group in groups.values():
            ocr_runs = sorted(group["ocr_runs"], key=self._time_key, reverse=True)
            ir_runs = sorted(group["ir_runs"], key=self._time_key, reverse=True)
            lineage_groups: dict[str, list[dict[str, Any]]] = {}
            for row in ir_runs:
                lineage_groups.setdefault(str(row["lineage_id"]), []).append(row)
            lineages = []
            for lineage_id, revisions in lineage_groups.items():
                ordered = sorted(revisions, key=lambda item: (int(item["ir_revision"]), str(item["run_id"])))
                latest = max(revisions, key=self._time_key)
                root = next((item for item in ordered if not item.get("parent_ir_run_id")), ordered[0])
                lineages.append(
                    {
                        "lineage_id": lineage_id,
                        "root_ir_run_id": root["run_id"],
                        "revision_count": len(revisions),
                        "latest_ir_run_id": latest["run_id"],
                        "latest_revision": latest["ir_revision"],
                        "latest_readiness": latest["readiness"],
                        "latest_written_at": latest.get("written_at"),
                    }
                )
            lineages.sort(key=lambda item: str(item.get("latest_written_at") or item["latest_ir_run_id"]), reverse=True)
            latest_candidates = [*ocr_runs, *ir_runs]
            latest = max(latest_candidates, key=self._time_key) if latest_candidates else {}
            documents.append(
                {
                    "document_id": group["document_id"],
                    "document_label": group["document_label"],
                    "source_pdf_sha256": group["source_pdf_sha256"],
                    "external_document_ids": sorted(group["external_document_ids"]),
                    "status": "ir_available" if ir_runs else "ocr_only",
                    "ocr_run_count": len(ocr_runs),
                    "ir_run_count": len(ir_runs),
                    "lineage_count": len(lineages),
                    "ocr_run_ids": [item["run_id"] for item in ocr_runs],
                    "latest_ir_run_id": ir_runs[0]["run_id"] if ir_runs else None,
                    "latest_readiness": ir_runs[0]["readiness"] if ir_runs else None,
                    "latest_activity_at": latest.get("written_at"),
                    "lineages": lineages,
                }
            )
        return sorted(
            documents,
            key=lambda item: (str(item.get("latest_activity_at") or ""), str(item["document_id"])),
            reverse=True,
        )

    def list_document_revisions(self, document_id: str) -> list[dict[str, Any]]:
        self.require_document_id(document_id)
        return sorted(
            [item for item in self._ir_rows() if item["document_id"] == document_id],
            key=lambda item: (str(item["lineage_id"]), int(item["ir_revision"]), str(item["run_id"])),
        )

    def list_lineage_revisions(self, lineage_id: str) -> list[dict[str, Any]]:
        return sorted(
            [item for item in self._ir_rows() if item["lineage_id"] == lineage_id],
            key=lambda item: (int(item["ir_revision"]), str(item["run_id"])),
        )

    def list_latest_lineage_revisions(self) -> list[dict[str, Any]]:
        """Return one current immutable revision for every independent lineage."""

        latest: dict[str, dict[str, Any]] = {}
        for row in self._ir_rows():
            lineage_id = str(row["lineage_id"])
            current = latest.get(lineage_id)
            candidate_key = (int(row["ir_revision"]), *self._time_key(row))
            current_key = (int(current["ir_revision"]), *self._time_key(current)) if current else None
            if current_key is None or candidate_key > current_key:
                latest[lineage_id] = row
        return sorted(
            latest.values(),
            key=lambda item: (int(item["ir_revision"]), *self._time_key(item)),
            reverse=True,
        )

    @staticmethod
    def require_document_id(document_id: str) -> None:
        if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
            raise ValueError(f"Invalid document_id: {document_id}")

    def _ocr_rows(self) -> list[dict[str, Any]]:
        rows = []
        if not self.ocr_output_root or not self.ocr_output_root.exists():
            return rows
        for path in self.ocr_output_root.glob("*/manifest.json"):
            manifest = self._read(path)
            if manifest is None:
                continue
            source = manifest.get("source")
            source = source if isinstance(source, dict) else {}
            rows.append(
                {
                    "kind": "ocr",
                    "run_id": str(manifest.get("run_id") or path.parent.name),
                    "document_id": manifest_document_id(manifest),
                    "document_label": manifest_document_label(manifest),
                    "source_pdf_sha256": normalize_sha256(source.get("sha256")),
                    "model": manifest.get("model"),
                    "page_count": int(manifest.get("page_count") or 0),
                    "written_at": manifest.get("written_at"),
                }
            )
        return rows

    def _ir_rows(self) -> list[dict[str, Any]]:
        rows = []
        if not self.ir_output_root.exists():
            return rows
        for path in self.ir_output_root.glob("*/manifest.json"):
            manifest = self._read(path)
            if manifest is None:
                continue
            ocr_run_id = str(manifest.get("ocr_run_id") or "")
            ocr_manifest = self._ocr_manifest(ocr_run_id)
            source = manifest.get("source")
            source = source if isinstance(source, dict) else {}
            rows.append(
                {
                    "kind": "ir",
                    "run_id": str(manifest.get("run_id") or path.parent.name),
                    "ocr_run_id": ocr_run_id,
                    "document_id": manifest_document_id(manifest),
                    "document_label": manifest_document_label(manifest, ocr_manifest),
                    "source_pdf_sha256": normalize_sha256(source.get("pdf_sha256") or source.get("sha256")),
                    "external_document_id": manifest.get("external_document_id"),
                    "lineage_id": self.version_manager.lineage_id(manifest),
                    "ir_revision": int(manifest.get("ir_revision") or 1),
                    "parent_ir_run_id": manifest.get("parent_ir_run_id"),
                    "schema_version": manifest.get("schema_version"),
                    "pipeline_version": manifest.get("pipeline_version"),
                    "readiness": manifest.get("readiness", "unknown"),
                    "can_build_evidence": bool(manifest.get("can_build_evidence", False)),
                    "written_at": manifest.get("written_at"),
                }
            )
        return rows

    def _ocr_manifest(self, ocr_run_id: str) -> dict[str, Any] | None:
        if not self.ocr_output_root or not ocr_run_id:
            return None
        return self._read(self.ocr_output_root / ocr_run_id / "manifest.json")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _time_key(item: dict[str, Any]) -> tuple[str, str]:
        return str(item.get("written_at") or ""), str(item.get("run_id") or "")

    @staticmethod
    def _group(groups: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
        document_id = str(row["document_id"])
        group = groups.setdefault(
            document_id,
            {
                "document_id": document_id,
                "document_label": row["document_label"],
                "source_pdf_sha256": row.get("source_pdf_sha256"),
                "external_document_ids": set(),
                "ocr_runs": [],
                "ir_runs": [],
            },
        )
        if row.get("document_label"):
            group["document_label"] = row["document_label"]
        if row.get("source_pdf_sha256"):
            group["source_pdf_sha256"] = row["source_pdf_sha256"]
        return group
