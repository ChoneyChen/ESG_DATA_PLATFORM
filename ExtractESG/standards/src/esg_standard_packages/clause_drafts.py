"""Authored clause drafts: edit one clause without touching an immutable package.

A draft is the five clause files plus a manifest. Saving validates the change and
recomputes the contract digest. Promotion into an executable package stays a
package build step, so no draft can silently change what older tasks read.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .clause_package import PART_FILES, ClausePackageCatalog, ClausePackageError
from .clause_profile import ClauseExtractionProfile, profile_digest, validate_profile

DRAFT_MANIFEST = "draft.json"
EDITABLE_FIELDS = (
    "positive_definition", "distinguish_from", "exclusions", "output_scopes", "publication",
    "retrieval", "evidence", "acceptance",
)


class ClauseDraftError(ValueError):
    """A draft is invalid, stale or no longer exists."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClauseDraftStore:
    def __init__(self, standards_root: Path, *, drafts_root: Path | None = None) -> None:
        self.root = Path(drafts_root or (Path(standards_root).resolve() / "clause-drafts"))
        self.packages = ClausePackageCatalog(standards_root)

    # --- reading ----------------------------------------------------------

    def _dir(self, draft_id: str) -> Path:
        if not draft_id or "/" in draft_id or ".." in draft_id or not draft_id.startswith("draft-"):
            raise ClauseDraftError("Draft identity is invalid")
        return self.root / draft_id

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        drafts = []
        for path in sorted(self.root.iterdir(), reverse=True):
            manifest = path / DRAFT_MANIFEST
            if not manifest.is_file():
                continue
            drafts.append(json.loads(manifest.read_text(encoding="utf-8")))
        return drafts

    def load(self, draft_id: str) -> dict:
        directory = self._dir(draft_id)
        manifest = directory / DRAFT_MANIFEST
        if not manifest.is_file():
            raise ClauseDraftError("Draft does not exist")
        body = {"manifest": json.loads(manifest.read_text(encoding="utf-8"))}
        for name in PART_FILES:
            path = directory / f"{name}.json"
            if not path.is_file():
                raise ClauseDraftError(f"Draft is missing {path.name}")
            body[name] = json.loads(path.read_text(encoding="utf-8"))
        return body

    # --- writing ----------------------------------------------------------

    def create(self, *, package_id: str, package_version: str, source_datapoint_id: str,
               author: str, note: str) -> dict:
        if not author.strip() or not note.strip():
            raise ClauseDraftError("A draft needs its author and a change note")
        parts = self.packages.clause_parts(package_id, package_version, source_datapoint_id)
        draft_id = f"draft-{uuid4().hex[:12]}"
        directory = self._dir(draft_id)
        directory.mkdir(parents=True, exist_ok=False)
        for name in PART_FILES:
            (directory / f"{name}.json").write_text(
                json.dumps(parts[name], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": "clause-draft-1.0", "draft_id": draft_id, "status": "draft",
            "base": {"package_id": package_id, "package_version": package_version,
                     "source_datapoint_id": source_datapoint_id,
                     "contract_digest": parts["clause"]["contract_digest"]},
            "author": author.strip(), "note": note.strip(), "created_at": _now(), "updated_at": _now(),
            "digest": parts["clause"]["contract_digest"], "validation": self.validate_parts(parts),
        }
        (directory / DRAFT_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest

    def save(self, draft_id: str, edits: dict, *, author: str, note: str, expected_digest: str | None) -> dict:
        current = self.load(draft_id)
        manifest = current["manifest"]
        if expected_digest and manifest["digest"] != expected_digest:
            raise ClauseDraftError("Draft changed since it was opened; reload before saving")
        parts = {name: current[name] for name in PART_FILES}
        for field in EDITABLE_FIELDS:
            if field in edits:
                target = "retrieval" if field == "retrieval" else "evidence" if field == "evidence" \
                    else "acceptance" if field == "acceptance" else "clause"
                if target == "clause":
                    parts["clause"][field] = edits[field]
                elif target == "evidence":
                    parts["evidence"]["requirements"] = edits[field]
                else:
                    parts[target].update(edits[field])
        draft_profile = self._profile(parts)
        new_digest = profile_digest(draft_profile)
        parts["clause"]["contract_digest"] = new_digest
        parts["provenance"]["contract_digest"] = new_digest
        validation = self.validate_parts(parts)
        if validation["errors"]:
            raise ClauseDraftError("；".join(error["message"] for error in validation["errors"]))
        directory = self._dir(draft_id)
        for name in PART_FILES:
            (directory / f"{name}.json").write_text(
                json.dumps(parts[name], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest.update({"updated_at": _now(), "digest": new_digest, "author": author.strip(),
                         "note": note.strip() or manifest["note"], "validation": validation,
                         "status": "validated"})
        (directory / DRAFT_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest

    def discard(self, draft_id: str) -> dict:
        directory = self._dir(draft_id)
        if not (directory / DRAFT_MANIFEST).is_file():
            raise ClauseDraftError("Draft does not exist")
        shutil.rmtree(directory)
        return {"draft_id": draft_id, "discarded": True}

    # --- validation -------------------------------------------------------

    def _profile(self, parts: dict) -> ClauseExtractionProfile:
        clause, retrieval, evidence, acceptance = (parts["clause"], parts["retrieval"],
                                                   parts["evidence"], parts["acceptance"])
        from .clause_package import PROFILE_FIELDS
        body = {key: clause[key] for key in PROFILE_FIELDS if key in clause}
        body.update({
            "retrieval": {key: retrieval.get(key, [] if key != "positive_terms" else [])
                          for key in ("positive_terms", "negative_terms", "structural_anchors")},
            "evidence": evidence.get("requirements") or [],
            "acceptance": {"minimum_reports": acceptance.get("minimum_reports", 1),
                           "samples": acceptance.get("samples") or []},
        })
        return ClauseExtractionProfile.model_validate(body)

    def validate_parts(self, parts: dict) -> dict:
        errors: list[dict] = []
        warnings: list[dict] = []
        try:
            profile = self._profile(parts)
            validate_profile(profile)
        except Exception as exc:  # pydantic and package errors share one report
            return {"ok": False, "errors": [{"field": "clause", "message": str(exc)[:400]}], "warnings": []}
        if not profile.retrieval.positive_terms:
            errors.append({"field": "retrieval", "message": "至少需要一个检索正向词"})
        required_scopes = [scope for scope in profile.output_scopes if scope.required]
        if not required_scopes:
            errors.append({"field": "output_scopes", "message": "至少需要一个必需口径"})
        if profile.value_type == "numeric" and not profile.canonical_unit:
            errors.append({"field": "canonical_unit", "message": "数值条款需要规范单位"})
        if not any(item.field == "value" for item in profile.evidence):
            warnings.append({"field": "evidence", "message": "证据要求里没有 value，链路将无法核对原值来源"})
        if not profile.acceptance.samples:
            warnings.append({"field": "acceptance", "message": "没有验收样例，条款无法通过真实验收进入可发布"})
        base = parts.get("clause", {}).get("clause_id")
        try:
            base_parts = self._base_parts(base)
        except (ClauseDraftError, ClausePackageError, FileNotFoundError):
            base_parts = None
        if base_parts is not None and profile.unit_dimension != base_parts["clause"].get("unit_dimension"):
            errors.append({"field": "unit_dimension", "message": "单位维度与父条款不一致，不能作为同一科学量"})
        return {"ok": not errors, "errors": errors, "warnings": warnings,
                "contract_digest": profile.contract_digest}

    def _base_parts(self, clause_id: str | None) -> dict | None:
        if not clause_id or ":" not in clause_id or "@" not in clause_id:
            return None
        package, source_id = clause_id.rsplit(":", 1)
        package_id, package_version = package.split("@", 1)
        return self.packages.clause_parts(package_id, package_version, source_id)
