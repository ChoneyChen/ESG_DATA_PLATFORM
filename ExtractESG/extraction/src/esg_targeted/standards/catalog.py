from __future__ import annotations

from pathlib import Path
import hashlib
import json
from threading import RLock

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.contracts import CompiledStandardPackage
from esg_standard_packages.errors import PackageIntegrityError

from esg_targeted.contracts import CatalogStandardPackage


class StandardPackageCatalog:
    def __init__(self, dist_root: Path) -> None:
        self.dist_root = dist_root.resolve()
        self.compiler = StandardPackageCompiler()
        self._scan_signature = None
        self._scan_result = None
        self._lock = RLock()

    @property
    def _titles_path(self) -> Path:
        return self.dist_root.parent / "profiles/esrs/2023-set1/package-titles.json"

    def _titles(self) -> dict[str, str]:
        """Short human names for the catalogue index; the package keeps the formal name."""
        try:
            payload = json.loads(self._titles_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(key): str(value) for key, value in (payload.get("titles") or {}).items()}

    def _registered_releases(self):
        """Canonical catalogues use explicit identities; loose roots are fixtures/legacy."""
        root = self.dist_root.parent
        index_path = root / "registry/releases.json"
        registration_path = root / "registry/esrs/2023-set1/registry.json"
        if not index_path.exists() and not registration_path.exists():
            return None
        index = json.loads(index_path.read_text(encoding="utf-8"))
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        releases = {}
        for entry in index["releases"]:
            key = (entry["package_id"], entry["package_version"])
            if key in releases:
                raise PackageIntegrityError("Duplicate registered release identity")
            path = (root / entry["path"]).resolve()
            if not path.is_relative_to(self.dist_root):
                raise PackageIntegrityError("Registered release path escapes configured root")
            releases[key] = {**entry, "resolved_path": path}
        active = []
        for entry in registration["modules"]:
            if not entry["executable"]:
                continue
            release = releases[(entry["package_id"], entry["package_version"])]
            if release["state"] == "retired" or release["resolved_path"] != (root / entry["runtime_path"]).resolve():
                raise PackageIntegrityError("Current module does not pin an active release")
            if release["source_digest"] != entry["source_digest"]:
                raise PackageIntegrityError("Current module/release source digests disagree")
            active.append(release)
        return registration, releases, active, (index_path, registration_path)

    def _validate_release(self, release):
        path = release["resolved_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != release["sha256"]:
            raise PackageIntegrityError("Immutable release differs from its registered checksum")
        package = self.compiler.validate_compiled(path)
        if (package.manifest.package_id, package.manifest.package_version, package.compilation.source_digest) != (
                release["package_id"], release["package_version"], release["source_digest"]):
            raise PackageIntegrityError("Release content differs from its registered identity")
        return package

    def list_releases(self) -> list[dict]:
        """Every registered, non-retired release — active pins and pilot versions alike."""
        registered = self._registered_releases()
        if registered is None:
            return [{"package_id": record.package_id, "package_version": record.package_version,
                     "state": record.status, "active": True} for record in self.list()]
        active = {(entry["package_id"], entry["package_version"]) for entry in registered[2]}
        return [{"package_id": entry["package_id"], "package_version": entry["package_version"],
                 "state": entry["state"], "active": (entry["package_id"], entry["package_version"]) in active}
                for entry in registered[1].values() if entry["state"] != "retired"]

    def list(self) -> list[CatalogStandardPackage]:
        return self.scan()[0]

    def scan(self) -> tuple[list[CatalogStandardPackage], list[dict[str, str]]]:
        with self._lock:
            return self._scan()

    def _scan(self) -> tuple[list[CatalogStandardPackage], list[dict[str, str]]]:
        packages: list[CatalogStandardPackage] = []
        issues: list[dict[str, str]] = []
        if not self.dist_root.is_dir():
            return packages, issues
        try:
            registered = self._registered_releases()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return [], [{"path": str(self.dist_root.parent / "registry"),
                         "error_type": type(exc).__name__, "error": str(exc)}]
        releases = {entry["resolved_path"]: entry for entry in registered[2]} if registered else {}
        paths = sorted(releases) if registered else sorted(self.dist_root.glob("*/*/package.json"))
        signature_paths = [*paths, *(registered[3] if registered else []), self._titles_path]
        signature = tuple((str(path), path.stat().st_mtime_ns if path.is_file() else None,
                           path.stat().st_size if path.is_file() else None) for path in signature_paths)
        if signature == self._scan_signature:
            return ([record.model_copy(deep=True) for record in self._scan_result[0]],
                    [dict(issue) for issue in self._scan_result[1]])
        titles = self._titles()
        for path in paths:
            try:
                package = self._validate_release(releases[path]) if registered else self.compiler.validate_compiled(path)
            except Exception as exc:
                issues.append(
                    {
                        "path": str(path.resolve()),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            packages.append(self._catalog_record(path, package, titles))
        self._scan_signature = signature
        self._scan_result = (packages, issues)
        return ([record.model_copy(deep=True) for record in packages], [dict(issue) for issue in issues])

    def diagnostics(self) -> dict:
        packages, issues = self.scan()
        return {
            "dist_root": str(self.dist_root),
            "discovered_package_count": len(packages) + len(issues),
            "valid_package_count": len(packages),
            "invalid_package_count": len(issues),
            "issues": issues,
            "state": "unavailable" if not packages else "degraded" if issues else "ready",
            "validation_scope": "structural_only",
            "real_extraction_validation": "not_assessed",
            "business_approval": "not_assessed",
            "catalog_digest": hashlib.sha256(json.dumps([
                [p.package_id, p.package_version, p.source_digest] for p in packages
            ], sort_keys=True).encode()).hexdigest(),
        }

    def snapshot(self) -> dict:
        """Lightweight executable catalogue plus explicit non-executable states."""
        packages = [p.model_dump(mode="json") for p in self.list()]
        registration_path = self.dist_root.parent / "registry/esrs/2023-set1/registry.json"
        if not registration_path.is_file():
            registration_path = self.dist_root.parent / "catalogs/esrs/2023-set1/registry.json"
        planned, registration_error = [], None
        try:
            registration = json.loads(registration_path.read_text(encoding="utf-8"))
            planned = [{key: m.get(key) for key in ("package_id", "disclosure_requirement",
                "state", "source_datapoint_ids", "core_analysis_state", "major_source_datapoint_ids", "next_steps")}
                for m in registration["modules"] if not m["executable"]]
        except (OSError, ValueError, KeyError) as exc:
            registration_error = str(exc)
        try:
            registered = self._registered_releases()
            historical = [{"package_id": e["package_id"], "package_version": e["package_version"],
                           "state": "retired", "selectable_for_new_tasks": False}
                          for e in registered[1].values() if e["state"] == "retired"] if registered else [
                           {"package_id": path.parent.parent.name, "package_version": path.parent.name,
                            "state": "retired", "selectable_for_new_tasks": False}
                           for path in sorted((self.dist_root / ".retired").glob("*/*/package.json"))]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            historical = []
            registration_error = str(exc)
        try:
            releases = self.list_releases()
        except (OSError, ValueError, KeyError, TypeError):
            releases = []
        return {"packages": packages, "diagnostics": self.diagnostics(), "planned": planned,
            "registration_error": registration_error, "releases": releases,
            "historical": historical}

    def load(self, package_id: str, package_version: str) -> CompiledStandardPackage:
        registered = self._registered_releases()
        if registered is not None:
            release = registered[1].get((package_id, package_version))
            if release is None:
                raise FileNotFoundError(f"Unregistered standard release: {package_id}@{package_version}")
            return self._validate_release(release)
        path = (self.dist_root / package_id / package_version / "package.json").resolve()
        if self.dist_root not in path.parents:
            raise ValueError("standard package path escapes configured root")
        if not path.is_file():
            path = (self.dist_root / ".retired" / package_id / package_version / "package.json").resolve()
            if self.dist_root / ".retired" not in path.parents:
                raise ValueError("retired standard package path escapes configured root")
        if not path.is_file():
            raise FileNotFoundError(f"compiled standard package not found: {package_id}@{package_version}")
        return self.compiler.validate_compiled(path)

    def _catalog_record(
        self, path: Path, package: CompiledStandardPackage, titles: dict[str, str] | None = None
    ) -> CatalogStandardPackage:
        requirement = package.manifest.standard.disclosure_requirement
        return CatalogStandardPackage(
            package_id=package.manifest.package_id,
            package_version=package.manifest.package_version,
            status=package.manifest.status,
            disclosure_requirement=requirement,
            title=(titles or {}).get(requirement, ""),
            path=str(path.resolve()),
            metric_count=len(package.metrics),
            element_count=len(package.elements),
            concept_count=len(package.concepts),
            source_digest=package.compilation.source_digest,
            format_version=package.format_version,
            core_schema_version=package.manifest.core_schema_version,
            core_source_datapoint_ids=package.manifest.core_source_datapoint_ids,
            metric_summaries=[{key: metric.model_dump(mode="json").get(key)
                for key in ("metric_id", "source_datapoint_id", "labels", "disclosure_role", "disclosure_topic", "obligation_class", "paragraph")}
                for metric in package.metrics],
            relation_count=len(package.relations),
            relation_types=sorted({relation.relation_type for relation in package.relations}),
        )
