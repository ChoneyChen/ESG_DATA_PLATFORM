from __future__ import annotations

from pathlib import Path
import re

from esg_targeted.contracts import CatalogIrRun
from esg_targeted.io import read_json


class DocumentIrCatalog:
    def __init__(self, roots: tuple[Path, ...]) -> None:
        self.roots = tuple(path.resolve() for path in roots)

    def list(self) -> list[CatalogIrRun]:
        records: dict[str, CatalogIrRun] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for manifest_path in sorted(root.glob("ir-*/manifest.json")):
                try:
                    payload = read_json(manifest_path)
                    source = payload.get("source")
                    source = source if isinstance(source, dict) else {}
                    policy = payload.get("evidence_policy")
                    policy = policy if isinstance(policy, dict) else {}
                    source_hash = source.get("pdf_sha256") or source.get("sha256")
                    source_hash = source_hash.strip().lower() if isinstance(source_hash, str) else None
                    source_hash = source_hash if source_hash and re.fullmatch(r"[a-f0-9]{64}", source_hash) else None
                    record = CatalogIrRun(
                        run_id=payload["run_id"],
                        path=str(manifest_path.parent.resolve()),
                        document_id=payload.get("document_id"),
                        document_label=payload.get("document_label")
                        or source.get("display_name"),
                        ir_revision=int(payload.get("ir_revision", 1)),
                        schema_version=payload["schema_version"],
                        readiness=payload.get("readiness", "unknown"),
                        can_build_evidence=bool(payload.get("can_build_evidence", False) or payload.get("can_build_limited_evidence", False)),
                        can_build_full_evidence=payload.get("can_build_evidence") is True,
                        can_build_limited_evidence=payload.get("can_build_limited_evidence") is True,
                        evidence_policy_mode=policy.get("mode") if isinstance(policy.get("mode"), str) else None,
                        ocr_run_id=payload.get("ocr_run_id") or source.get("ocr_run_id") or None,
                        source_pdf_sha256=source_hash,
                        page_count=int(payload.get("page_count", payload.get("counts", {}).get("page", 0))),
                        manifest_mtime=manifest_path.stat().st_mtime,
                    )
                except Exception:
                    continue
                existing = records.get(record.run_id)
                if existing is None or record.manifest_mtime > existing.manifest_mtime:
                    records[record.run_id] = record
        return sorted(records.values(), key=lambda item: item.manifest_mtime, reverse=True)

    def resolve(self, run_id: str) -> Path:
        for record in self.list():
            if record.run_id == run_id:
                return Path(record.path)
        raise FileNotFoundError(f"Document IR run not found: {run_id}")
