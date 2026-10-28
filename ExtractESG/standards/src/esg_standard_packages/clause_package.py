"""Read per-clause directory packages, verifying each clause against its digest."""
from __future__ import annotations

import json
from pathlib import Path

from .clause_profile import ClauseExtractionProfile, profile_digest, validate_profile

PROFILE_FIELDS = ("schema_version", "clause_id", "clause_key", "clause_digest", "package_id", "package_version",
                  "source_datapoint_id", "metric_id", "labels", "description", "display_label", "display_definition", "positive_definition",
                  "distinguish_from", "exclusions", "data_kind", "value_type", "group", "text_group", "group_terms", "group_unit_terms", "relationships",
                  "exact_labels", "reading", "canonical_unit", "unit_dimension", "value_origin",
                  "output_scopes", "publication", "contract_digest")
PART_FILES = ("clause", "retrieval", "evidence", "acceptance", "provenance")


class ClausePackageError(ValueError):
    """A clause directory is incomplete or no longer matches its digest."""


class ClausePackageCatalog:
    """One clause is one directory; the catalog is only an index over them."""

    def __init__(self, standards_root: Path) -> None:
        self.root = Path(standards_root).resolve() / "clauses"
        self._catalog: dict | None = None
        self._profiles: dict[tuple[str, str], dict[str, ClauseExtractionProfile]] = {}

    # --- index ------------------------------------------------------------

    def catalog(self) -> dict:
        if self._catalog is None:
            path = self.root / "catalog.json"
            self._catalog = (json.loads(path.read_text(encoding="utf-8"))
                             if path.is_file() else {"schema_version": "clause-catalog-1.0", "packages": []})
        return self._catalog

    def package_entry(self, package_id: str, package_version: str) -> dict | None:
        return next((row for row in self.catalog().get("packages", [])
                     if row["package_id"] == package_id and row["package_version"] == package_version), None)

    def list_packages(self) -> list[dict]:
        return list(self.catalog().get("packages", []))

    # --- reading ----------------------------------------------------------

    def clause_parts(self, package_id: str, package_version: str, source_datapoint_id: str) -> dict:
        entry = self.package_entry(package_id, package_version)
        if entry is None:
            raise ClausePackageError(f"Clause catalog has no package {package_id}@{package_version}")
        clause = next((row for row in entry["clauses"] if row["source_datapoint_id"] == source_datapoint_id), None)
        if clause is None:
            raise ClausePackageError(f"Clause catalog has no clause {source_datapoint_id}")
        directory = self.root / clause["path"]
        parts = {}
        for name in PART_FILES:
            path = directory / f"{name}.json"
            if not path.is_file():
                raise ClausePackageError(f"Clause {source_datapoint_id} is missing {path.name}")
            parts[name] = json.loads(path.read_text(encoding="utf-8"))
        return parts

    def profile(self, package_id: str, package_version: str, source_datapoint_id: str) -> ClauseExtractionProfile:
        parts = self.clause_parts(package_id, package_version, source_datapoint_id)
        clause, retrieval, evidence, acceptance = (parts["clause"], parts["retrieval"],
                                                   parts["evidence"], parts["acceptance"])
        body = {key: clause[key] for key in PROFILE_FIELDS if key in clause}
        body.update({
            "retrieval": {key: retrieval.get(key, [] if key != "positive_terms" else [])
                          for key in ("positive_terms", "negative_terms", "structural_anchors")},
            "evidence": evidence.get("requirements") or [],
            "acceptance": {"minimum_reports": acceptance.get("minimum_reports", 1),
                           "samples": acceptance.get("samples") or []},
        })
        profile = ClauseExtractionProfile.model_validate(body)
        try:
            validate_profile(profile)
        except ValueError as error:
            raise ClausePackageError(
                f"Clause {source_datapoint_id} content differs from its digest") from error
        if profile_digest(profile) != profile.contract_digest:
            raise ClausePackageError(f"Clause {source_datapoint_id} content differs from its digest")
        return profile

    def for_package(self, package_id: str, package_version: str) -> dict[str, ClauseExtractionProfile]:
        key = (package_id, package_version)
        if key in self._profiles:
            return self._profiles[key]
        entry = self.package_entry(package_id, package_version)
        if entry is None:
            return {}
        profiles = {row["metric_id"]: self.profile(package_id, package_version, row["source_datapoint_id"])
                    for row in entry["clauses"]}
        self._profiles[key] = profiles
        return profiles

    def for_metric(self, package_id: str, package_version: str, metric_id: str) -> ClauseExtractionProfile | None:
        return self.for_package(package_id, package_version).get(metric_id)

    def skipped(self, package_id: str, package_version: str) -> list[dict]:
        entry = self.package_entry(package_id, package_version)
        return list(entry.get("skipped") or []) if entry else []

    # --- authoring --------------------------------------------------------

    def write_clause(self, package_id: str, package_version: str, source_datapoint_id: str,
                     parts: dict) -> Path:
        """Write one authored clause; the caller re-runs the builder to refresh the index."""
        entry = self.package_entry(package_id, package_version)
        directory = self.root / package_id / package_version / source_datapoint_id
        if entry is not None:
            existing = next((row for row in entry["clauses"]
                             if row["source_datapoint_id"] == source_datapoint_id), None)
            if existing:
                directory = self.root / existing["path"]
        directory.mkdir(parents=True, exist_ok=True)
        for name in PART_FILES:
            if name in parts:
                (directory / f"{name}.json").write_text(
                    json.dumps(parts[name], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
        return directory
