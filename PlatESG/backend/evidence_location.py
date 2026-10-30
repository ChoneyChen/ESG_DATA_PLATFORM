"""Read-only locator availability, separate from evidence quality and review."""
from __future__ import annotations

import re
from pathlib import Path

from .public_pdf import published_pdf


def candidate_locations(rows: list[dict], pdf_roots: list[Path], resolver=None) -> list[dict]:
    pdfs, versions = {}, {}
    for row in rows:
        location = {"state": "unknown", "asset_id": None, "ir_run_id": None,
                    "ir_revision": row.get("source_ir_revision"), "pdf_available": False,
                    "ir_available": False, "reason": "来源定位身份尚未完整确认",
                    "scope": "locator_availability_only"}
        row["evidence_location"] = location
        # Explicit ingestion provenance, never guessed from an ID or filename.
        if row.get("upstream_system") == "synthetic-demo":
            location.update(state="synthetic", reason="合成演示来源；保留模拟片段，不存在真实报告PDF或IR")
            continue
        digest, ref = row.get("source_sha256"), row.get("source_artifact_ref")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        if digest not in pdfs:
            try:
                published_pdf({"source_sha256": digest}, pdf_roots)
                pdfs[digest] = True
            except (OSError, ValueError, KeyError):
                pdfs[digest] = False
        location["pdf_available"] = pdfs[digest]
        # The asset identity follows the immutable catalog contract, validated
        # against the complete source digest above, rather than a prefix test.
        if pdfs[digest] and ref == "pdf-" + digest[:24]:
            location["asset_id"] = ref
        run, revision = row.get("source_ir_run_id"), row.get("source_ir_revision")
        key = (run, revision, digest)
        if key not in versions:
            valid = False
            if isinstance(run, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", run):
                if resolver is not None:
                    try:
                        reference = resolver(run)
                        resource = reference.get("report_resource") or {}
                        valid = reference.get("ir_run_id") == run and reference.get("ir_revision") == revision and (reference.get("source_sha256") or resource.get("sha256")) == digest
                    except (OSError, ValueError, TypeError, KeyError):
                        valid = False
            versions[key] = bool(valid)
        location["ir_available"] = versions[key]
        if versions[key]:
            location["ir_run_id"] = run
        if location["asset_id"] or versions[key]:
            location.update(state="verified", reason="来源定位与固定指纹、修订及证据服务一致；证据质量仍须独立核对")
        else:
            location.update(state="unavailable", reason="固定来源暂时无法通过资源或证据接口读取")
    return rows
