"""Clause lifecycle views over the immutable standard packages and the acceptance ledger.

The ledger is standards-owned: it records what real runs proved about a clause
contract. This service only projects it. It never records an acceptance by
declaration; the verdict comes from :func:`evaluate_run` over a frozen bundle.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esg_standard_packages.clause_lifecycle import (
    ClauseLifecycleLedger, ClauseRef, RetirementImpact, clause_version_diff, dependent_clauses,
    evaluate_run, extension_contract_digest, ledger_view, lifecycle_state, load_ledger,
    original_contract_digest, record_acceptance, retire_and_save,
)
from esg_standard_packages.errors import PackageIntegrityError

from .catalog import StandardPackageCatalog
from .runtime_snapshot import registered_extensions


def ledger_path(standard_dist_root: Path) -> Path:
    return Path(standard_dist_root).resolve().parent / "lifecycle/ledger.json"


class ClauseLifecycleService:
    def __init__(self, catalog: StandardPackageCatalog, *, dist_root: Path | None = None) -> None:
        self.catalog = catalog
        self.ledger_path = ledger_path(dist_root or catalog.dist_root)
        self.registry_path = Path(catalog.dist_root).resolve().parent / "registry/releases.json"
        self._cache: tuple[tuple[int, int], dict] | None = None

    # --- inputs -----------------------------------------------------------

    def _business_approvals(self) -> dict[tuple[str, str], str]:
        try:
            rows = json.loads(self.registry_path.read_text(encoding="utf-8"))["releases"]
        except (OSError, ValueError, KeyError):
            return {}
        return {(row["package_id"], row["package_version"]): row.get("business_approval", "not_assessed")
                for row in rows}

    def _extensions(self) -> tuple[list[Any], list[dict]]:
        try:
            releases = registered_extensions(self.catalog.dist_root.parent, self.catalog)
        except (OSError, ValueError) as exc:
            return [], [{"code": "extension_registry_unavailable", "message": str(exc)}]
        return releases, []

    @staticmethod
    def original_ref(package: Any, metric: dict) -> ClauseRef:
        return ClauseRef(kind="original", package_id=package.manifest.package_id,
                         package_version=package.manifest.package_version,
                         source_datapoint_id=metric["source_datapoint_id"])

    @staticmethod
    def extension_ref(release: Any) -> ClauseRef:
        definition = release.definition
        return ClauseRef(kind="extension", extension_id=definition.extension_id,
                         definition_version=definition.definition_version)

    # --- projection -------------------------------------------------------

    def package_states(self, package: Any, ledger: ClauseLifecycleLedger,
                       approvals: dict[tuple[str, str], str]) -> list[dict]:
        approved = approvals.get((package.manifest.package_id, package.manifest.package_version)) == "approved"
        rows = []
        for metric in package.metrics:
            body = metric.model_dump(mode="json")
            body["package_id"] = package.manifest.package_id
            ref = self.original_ref(package, body)
            rows.append(lifecycle_state(clause=ref, contract_digest_value=original_contract_digest(body),
                                        ledger=ledger, compiled=True, business_approved=approved))
        return rows

    def extension_states(self, releases: list[Any], ledger: ClauseLifecycleLedger) -> list[dict]:
        rows = []
        for release in releases:
            ref = self.extension_ref(release)
            rows.append(lifecycle_state(clause=ref, contract_digest_value=extension_contract_digest(release.definition),
                                        ledger=ledger, compiled=True,
                                        business_approved=release.approval.decision == "approved"))
        return rows

    def view(self) -> dict:
        signature = ((self.ledger_path.stat().st_mtime_ns, self.ledger_path.stat().st_size)
                     if self.ledger_path.exists() else (0, 0))
        if self._cache and self._cache[0] == signature:
            return self._cache[1]
        ledger = load_ledger(self.ledger_path)
        approvals = self._business_approvals()
        states, summaries, errors = [], [], []
        for record in self.catalog.list_releases():
            try:
                package = self.catalog.load(record["package_id"], record["package_version"])
                rows = self.package_states(package, ledger, approvals)
            except (FileNotFoundError, ValueError, KeyError) as exc:
                errors.append({"code": "package_lifecycle_unavailable", "package_id": record["package_id"],
                               "message": str(exc)})
                continue
            states.extend(rows)
            publishable = sum(1 for row in rows if row["publishable"])
            summaries.append({"package_id": record["package_id"], "package_version": record["package_version"],
                              "active": record.get("active", True),
                              "clause_count": len(rows), "publishable_count": publishable,
                              "accepted_count": sum(1 for row in rows if row["state"] in {"accepted", "publishable"}),
                              "unmeasured_count": sum(1 for row in rows if row["state"] in {"compiled", "draft"}),
                              "states": {state: sum(1 for row in rows if row["state"] == state)
                                         for state in ("draft", "compiled", "trialed", "accepted", "publishable", "retired")}})
        releases, extension_errors = self._extensions()
        errors.extend(extension_errors)
        try:
            extension_rows = self.extension_states(releases, ledger)
        except (ValueError, KeyError) as exc:
            extension_rows = []
            errors.append({"code": "extension_lifecycle_unavailable", "message": str(exc)})
        states.extend(extension_rows)
        body = ledger_view(ledger, states=states)
        body.update({"packages": summaries, "extensions": extension_rows, "diagnostics": errors,
                     "policy": {"publishable_requires": ["real_acceptance_on_current_contract", "business_approval"],
                                "absence_rule": "not_covered/unmapped/insufficient_evidence never establish absence"}})
        self._cache = (signature, body)
        return body

    def package_lifecycle(self, package_id: str, package_version: str, package: Any | None = None) -> list[dict]:
        package = package if package is not None else self.catalog.load(package_id, package_version)
        return self.package_states(package, load_ledger(self.ledger_path), self._business_approvals())

    # --- writes -----------------------------------------------------------

    def resolve_metric_ids(self, *, package_id: str | None, package_version: str | None,
                           source_datapoint_ids: list[str] | None, extension_id: str | None,
                           definition_version: str | None) -> tuple[ClauseRef, list[str], str]:
        """Resolve a clause identity and its executed metric IDs from authoritative definitions."""
        if extension_id:
            if definition_version is None or source_datapoint_ids:
                raise ValueError("An extension clause requires only its extension identity and version")
            releases, _ = self._extensions()
            release = next((row for row in releases
                            if row.definition.extension_id == extension_id
                            and row.definition.definition_version == definition_version), None)
            if release is None:
                raise PackageIntegrityError("Extension clause has no authoritative registered release")
            ref = self.extension_ref(release)
            return ref, [release.definition.extension_id], extension_contract_digest(release.definition)
        if not package_id or not package_version or not source_datapoint_ids:
            raise ValueError("An original clause requires its package identity and source datapoint IDs")
        package = self.catalog.load(package_id, package_version)
        by_source = {metric.source_datapoint_id: metric for metric in package.metrics}
        unknown = [item for item in source_datapoint_ids if item not in by_source]
        if unknown:
            raise ValueError(f"Unknown original clauses: {', '.join(unknown)}")
        metric = by_source[source_datapoint_ids[0]]
        body = metric.model_dump(mode="json")
        body["package_id"] = package_id
        ref = ClauseRef(kind="original", package_id=package_id, package_version=package_version,
                        source_datapoint_id=source_datapoint_ids[0])
        return ref, [by_source[item].metric_id for item in source_datapoint_ids], original_contract_digest(body)

    def record(self, *, bundle_path: str, allowed_roots: list[Path], package_id: str | None = None,
               package_version: str | None = None, source_datapoint_ids: list[str] | None = None,
               extension_id: str | None = None, definition_version: str | None = None,
               gold: dict | None = None, recorded_by: str = "", reason: str = "") -> dict:
        root = Path(bundle_path).resolve()
        if not any(root.is_relative_to(Path(allowed).resolve()) for allowed in allowed_roots):
            raise ValueError("Trial bundle is outside the configured result roots")
        result_path = root / "exports/extraction-result.json"
        if not result_path.is_file():
            raise ValueError("Trial bundle has no exported extraction result")
        ref, metric_ids, digest = self.resolve_metric_ids(
            package_id=package_id, package_version=package_version,
            source_datapoint_ids=source_datapoint_ids, extension_id=extension_id,
            definition_version=definition_version)
        if not recorded_by.strip() or not reason.strip():
            raise ValueError("Acceptance requires a recording actor and a substantive reason")
        acceptance = evaluate_run(json.loads(result_path.read_text(encoding="utf-8")), clause=ref,
                                  metric_ids=metric_ids, contract_digest_value=digest,
                                  run_ref=_relative_run_ref(root), recorded_by=recorded_by,
                                  reason=reason, gold=gold)
        record_acceptance(self.ledger_path, acceptance)
        self._cache = None
        return {"acceptance": acceptance.model_dump(mode="json"), "state": self._state_for(ref, digest)}

    def _state_for(self, ref: ClauseRef, digest: str) -> dict:
        ledger = load_ledger(self.ledger_path)
        approved = False
        if ref.kind == "original":
            approved = self._business_approvals().get((ref.package_id, ref.package_version)) == "approved"
        else:
            releases, _ = self._extensions()
            release = next((row for row in releases if row.definition.extension_id == ref.extension_id
                            and row.definition.definition_version == ref.definition_version), None)
            approved = bool(release and release.approval.decision == "approved")
        return lifecycle_state(clause=ref, contract_digest_value=digest, ledger=ledger,
                               business_approved=approved)

    def retire(self, *, package_id: str | None = None, package_version: str | None = None,
               source_datapoint_id: str | None = None, extension_id: str | None = None,
               definition_version: str | None = None, reason: str = "", retired_by: str = "",
               published_observations: int = 0, active_tasks: int = 0,
               dependent_extensions: list[str] | None = None, force: bool = False) -> dict:
        ref, _, digest = self.resolve_metric_ids(
            package_id=package_id, package_version=package_version,
            source_datapoint_ids=[source_datapoint_id] if source_datapoint_id else None,
            extension_id=extension_id, definition_version=definition_version)
        ledger = retire_and_save(self.ledger_path, clause=ref, contract_digest_value=digest, reason=reason,
                                 retired_by=retired_by, force=force,
                                 impact=RetirementImpact(published_observations=max(0, published_observations),
                                                         active_tasks=max(0, active_tasks),
                                                         dependent_extensions=dependent_extensions or []))
        self._cache = None
        return {"retirement": ledger.retirement(ref.key()).model_dump(mode="json"),
                "state": self._state_for(ref, digest)}

    # --- read-only analysis ----------------------------------------------

    def diff(self, package_id: str, old_version: str, new_version: str) -> dict:
        old, new = self.catalog.load(package_id, old_version), self.catalog.load(package_id, new_version)
        return clause_version_diff({metric.source_datapoint_id: metric.model_dump(mode="json") for metric in old.metrics},
                                   {metric.source_datapoint_id: metric.model_dump(mode="json") for metric in new.metrics},
                                   old_version=old_version, new_version=new_version)

    def dependents(self, package_id: str, package_version: str, source_datapoint_id: str) -> dict:
        package = self.catalog.load(package_id, package_version)
        metrics = [metric.model_dump(mode="json") for metric in package.metrics]
        releases, _ = self._extensions()
        rows = dependent_clauses(target_source_datapoint_id=source_datapoint_id, metrics=metrics,
                                 relations=[relation.model_dump(mode="json") for relation in package.relations],
                                 extensions=[release.model_dump(mode="json") for release in releases])
        ledger = load_ledger(self.ledger_path)
        involved = {(row.get("clauses") or [None])[0] for row in rows}
        states = {}
        approvals = self._business_approvals()
        for metric in package.metrics:
            body = metric.model_dump(mode="json")
            body["package_id"] = package_id
            if metric.source_datapoint_id in involved or metric.source_datapoint_id == source_datapoint_id:
                states[metric.source_datapoint_id] = lifecycle_state(
                    clause=self.original_ref(package, body), contract_digest_value=original_contract_digest(body),
                    ledger=ledger, business_approved=approvals.get((package_id, package_version)) == "approved")
        for release in releases:
            if release.definition.extension_id in involved:
                states[release.definition.extension_id] = lifecycle_state(
                    clause=self.extension_ref(release),
                    contract_digest_value=extension_contract_digest(release.definition), ledger=ledger,
                    business_approved=release.approval.decision == "approved")
        return {"package_id": package_id, "package_version": package_version,
                "source_datapoint_id": source_datapoint_id, "dependents": rows, "states": states}


def _relative_run_ref(root: Path) -> str:
    parts = root.parts
    for marker in ("targeted_extract_output", "report-sessions", "results"):
        if marker in parts:
            return str(Path(*parts[parts.index(marker):]))
    return root.name
