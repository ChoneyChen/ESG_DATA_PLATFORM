from __future__ import annotations

from pathlib import Path
import hashlib
import json
from threading import RLock

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.contracts import CompiledStandardPackage

from esg_targeted.contracts import CatalogStandardPackage


class StandardPackageCatalog:
    def __init__(self, dist_root: Path) -> None:
        self.dist_root = dist_root.resolve()
        self.compiler = StandardPackageCompiler()
        self._scan_signature = None
        self._scan_result = None
        self._lock = RLock()

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
        paths = sorted(self.dist_root.glob("*/*/package.json"))
        signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
        if signature == self._scan_signature:
            return ([record.model_copy(deep=True) for record in self._scan_result[0]],
                    [dict(issue) for issue in self._scan_result[1]])
        for path in paths:
            try:
                package = self.compiler.validate_compiled(path)
            except Exception as exc:
                issues.append(
                    {
                        "path": str(path.resolve()),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            packages.append(self._catalog_record(path, package))
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
        registration_path = self.dist_root.parent / "catalogs/esrs/2023-set1/registry.json"
        planned, registration_error = [], None
        try:
            registration = json.loads(registration_path.read_text(encoding="utf-8"))
            planned = [{key: m.get(key) for key in ("package_id", "disclosure_requirement",
                "state", "source_datapoint_ids", "core_analysis_state", "major_source_datapoint_ids", "next_steps")}
                for m in registration["modules"] if not m["executable"]]
        except (OSError, ValueError, KeyError) as exc:
            registration_error = str(exc)
        return {"packages": packages, "diagnostics": self.diagnostics(), "planned": planned,
            "registration_error": registration_error,
            "historical": [{"package_id": path.parent.parent.name, "package_version": path.parent.name,
                            "state": "retired", "selectable_for_new_tasks": False}
                           for path in sorted((self.dist_root / ".retired").glob("*/*/package.json"))]}

    def load(self, package_id: str, package_version: str) -> CompiledStandardPackage:
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

    @staticmethod
    def _catalog_record(
        path: Path, package: CompiledStandardPackage
    ) -> CatalogStandardPackage:
        return CatalogStandardPackage(
            package_id=package.manifest.package_id,
            package_version=package.manifest.package_version,
            status=package.manifest.status,
            disclosure_requirement=package.manifest.standard.disclosure_requirement,
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
        )
