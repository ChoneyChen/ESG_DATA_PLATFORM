from __future__ import annotations

from pathlib import Path

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.contracts import CompiledStandardPackage

from esg_targeted.contracts import CatalogStandardPackage


class StandardPackageCatalog:
    def __init__(self, dist_root: Path) -> None:
        self.dist_root = dist_root.resolve()
        self.compiler = StandardPackageCompiler()

    def list(self) -> list[CatalogStandardPackage]:
        return self.scan()[0]

    def scan(self) -> tuple[list[CatalogStandardPackage], list[dict[str, str]]]:
        packages: list[CatalogStandardPackage] = []
        issues: list[dict[str, str]] = []
        if not self.dist_root.is_dir():
            return packages, issues
        for path in sorted(self.dist_root.glob("*/*/package.json")):
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
        return packages, issues

    def diagnostics(self) -> dict:
        packages, issues = self.scan()
        return {
            "dist_root": str(self.dist_root),
            "discovered_package_count": len(packages) + len(issues),
            "valid_package_count": len(packages),
            "invalid_package_count": len(issues),
            "issues": issues,
        }

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
        )
