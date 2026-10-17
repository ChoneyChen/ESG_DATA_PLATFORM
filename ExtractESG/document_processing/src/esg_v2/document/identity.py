from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esg_v2.utils.hashing import sha256_file


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DOCUMENT_ID_PATTERN = re.compile(r"^(?:doc-sha256-[0-9a-f]{64}|doc-legacy-[0-9a-f]{24})$")
LINEAGE_ID_PATTERN = re.compile(r"^irl-[0-9a-f]{24}$")


@dataclass(frozen=True)
class ResolvedDocumentIdentity:
    document_id: str
    document_label: str
    source_pdf_sha256: str | None


def normalize_sha256(value: object) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if SHA256_PATTERN.fullmatch(candidate) else None


def document_id_from_sha256(value: str) -> str:
    digest = normalize_sha256(value)
    if digest is None:
        raise ValueError("A valid lowercase SHA-256 digest is required")
    return f"doc-sha256-{digest}"


def legacy_document_id(stable_key: str) -> str:
    digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
    return f"doc-legacy-{digest}"


def lineage_id_from_root_run(root_ir_run_id: str) -> str:
    digest = hashlib.sha256(root_ir_run_id.encode("utf-8")).hexdigest()[:24]
    return f"irl-{digest}"


def resolve_document_identity(
    *,
    ocr_run_id: str,
    ocr_manifest: dict[str, Any],
    pdf_path: str | None,
    document_label: str | None = None,
) -> ResolvedDocumentIdentity:
    source = ocr_manifest.get("source")
    source = source if isinstance(source, dict) else {}
    manifest_sha = normalize_sha256(source.get("sha256"))
    local_sha = sha256_file(pdf_path) if pdf_path and Path(pdf_path).is_file() else None
    local_sha = normalize_sha256(local_sha)
    if manifest_sha and local_sha and manifest_sha != local_sha:
        raise ValueError(
            "The supplied PDF does not match the immutable OCR source: "
            f"OCR sha256={manifest_sha}, PDF sha256={local_sha}"
        )
    source_sha = manifest_sha or local_sha
    display_name = _clean_label(document_label)
    if not display_name:
        display_name = _clean_label(source.get("display_name"))
    if not display_name and pdf_path:
        display_name = Path(pdf_path).name
    if not display_name:
        display_name = f"Document {ocr_run_id}"
    document_id = document_id_from_sha256(source_sha) if source_sha else legacy_document_id(ocr_run_id)
    return ResolvedDocumentIdentity(document_id, display_name, source_sha)


def ensure_metadata_identity(metadata: Any) -> None:
    source_sha = normalize_sha256(getattr(metadata, "source_pdf_sha256", None))
    if not getattr(metadata, "document_id", None):
        metadata.document_id = (
            document_id_from_sha256(source_sha)
            if source_sha
            else legacy_document_id(str(getattr(metadata, "ocr_run_id", "unknown")))
        )
    if not getattr(metadata, "document_label", None):
        source_path = getattr(metadata, "source_pdf_path", None)
        metadata.document_label = Path(source_path).name if source_path else f"Document {metadata.document_id[-12:]}"
    if not getattr(metadata, "lineage_id", None):
        metadata.lineage_id = lineage_id_from_root_run(str(getattr(metadata, "run_id", "unknown")))


def manifest_document_id(manifest: dict[str, Any]) -> str:
    candidate = str(manifest.get("document_id") or "")
    if DOCUMENT_ID_PATTERN.fullmatch(candidate):
        return candidate
    source = manifest.get("source")
    if isinstance(source, str) and Path(source).is_file():
        return document_id_from_sha256(sha256_file(source))
    source = source if isinstance(source, dict) else {}
    source_sha = normalize_sha256(source.get("pdf_sha256") or source.get("sha256"))
    if source_sha:
        return document_id_from_sha256(source_sha)
    return legacy_document_id(str(manifest.get("ocr_run_id") or manifest.get("run_id") or "unknown"))


def manifest_document_label(manifest: dict[str, Any], ocr_manifest: dict[str, Any] | None = None) -> str:
    label = _clean_label(manifest.get("document_label"))
    if label:
        return label
    source = manifest.get("source")
    if isinstance(source, str) and source:
        return Path(source).name[:240]
    if isinstance(source, dict):
        label = _clean_label(source.get("display_name"))
        if label:
            return label
    ocr_source = (ocr_manifest or {}).get("source")
    if isinstance(ocr_source, dict):
        label = _clean_label(ocr_source.get("display_name"))
        if label:
            return label
    return f"Document {manifest_document_id(manifest)[-12:]}"


def _clean_label(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text).name[:240]
