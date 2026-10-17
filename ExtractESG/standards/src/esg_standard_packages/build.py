"""The single official build path: registered authoring files → immutable releases.

Historical generators are audit material. No semantic labels, selections or
requirements are synthesized by this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile

from .compiler import StandardPackageCompiler
from .errors import PackageIntegrityError
from .io import canonical_json_bytes, load_json, pretty_json_bytes, sha256_file, write_json
from .scope import validate_registry


def standard_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_path(root: Path) -> Path:
    canonical = root / "registry/esrs/2023-set1/registry.json"
    return canonical if canonical.is_file() else root / "catalogs/esrs/2023-set1/registry.json"


def build_authoring(root: Path | None = None, *, publish: bool = False):
    root = (root or standard_root()).resolve()
    registry = load_json(registry_path(root))
    catalog = load_json(root / "catalog/esrs/2023-set1/ig3-original.json")
    compiler, packages = StandardPackageCompiler(), {}
    index_path = root / "registry/releases.json"
    index = load_json(index_path)
    registered = {}
    for entry in index["releases"]:
        key = (entry["package_id"], entry["package_version"])
        if key in registered:
            raise PackageIntegrityError("Duplicate immutable release identity")
        registered[key] = entry
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root / "releases") or sha256_file(path) != entry["sha256"]:
            raise PackageIntegrityError("Immutable release differs from its registered checksum")
    active = set()
    for entry in registry["modules"]:
        if not entry["executable"]:
            continue
        source = (root / entry["source_directory"]).resolve()
        if not source.is_relative_to(root / "authoring"):
            raise PackageIntegrityError("Official builds read only registered current authoring sources")
        manifest = load_json(source / "manifest.json")
        core = root / "schemas/core" / manifest["core_schema_version"] / "core.json"
        package = compiler.compile(core_path=core, module_dir=source)
        if (package.manifest.package_id, package.manifest.package_version, package.compilation.source_digest) != (
                entry["package_id"], entry["package_version"], entry["source_digest"]):
            raise PackageIntegrityError("Authoring identity/digest differs from registration; author a new explicit version")
        packages[entry["disclosure_requirement"]] = package
    validate_registry(registry, catalog, packages)
    for entry in registry["modules"]:
        if not entry["executable"]:
            continue
        package = packages[entry["disclosure_requirement"]]
        key = (entry["package_id"], entry["package_version"])
        active.add(key)
        if key not in registered and not publish:
            raise PackageIntegrityError("Release identity is not registered; build --publish is required")
        target = (root / entry["runtime_path"]).resolve()
        if not target.is_relative_to(root / "releases"):
            raise PackageIntegrityError("Release path escapes the canonical release root")
        if key in registered and ((root / registered[key]["path"]).resolve() != target
                or registered[key]["source_digest"] != package.compilation.source_digest):
            raise PackageIntegrityError("An immutable release identity cannot change path or source digest")
        if target.exists():
            existing = compiler.validate_compiled(target)
            if canonical_json_bytes(existing.model_dump(mode="json")) != canonical_json_bytes(package.model_dump(mode="json")):
                raise PackageIntegrityError("Published content is immutable; publish a new package version")
        elif publish:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".compile-", suffix=".json", dir=target.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(pretty_json_bytes(package.model_dump(mode="json")))
                    handle.flush()
                    os.fsync(handle.fileno())
                compiler.validate_compiled(temporary)
                os.link(temporary, target)  # Never overwrite an existing immutable version.
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise PackageIntegrityError("Registered release is missing; build --publish is required")
        if key not in registered:
            registered[key] = {"package_id": key[0], "package_version": key[1],
                "path": str(target.relative_to(root)), "sha256": sha256_file(target),
                "source_digest": package.compilation.source_digest,
                "format_version": package.format_version, "core_schema_version": package.manifest.core_schema_version,
                "real_extraction_validation": "not_assessed", "business_approval": "not_assessed"}
    if publish:
        for key, entry in registered.items():
            entry["state"] = "compiled_draft" if key in active else "retired"
        updated = {**index, "releases": [registered[key] for key in sorted(registered)]}
        # Only generated registration metadata is replaced; release bytes never are.
        fd, name = tempfile.mkstemp(prefix=".release-registry-", suffix=".json", dir=index_path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(pretty_json_bytes(updated))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, index_path)
        finally:
            Path(name).unlink(missing_ok=True)
    return packages, registry


def export_authoring(destination: Path, root: Path | None = None):
    """Compatibility export for historical generator callers, from authoring only."""
    root = (root or standard_root()).resolve()
    packages, registry = build_authoring(root)
    for entry in registry["modules"]:
        if not entry["executable"]:
            continue
        source = root / entry["source_directory"]
        target = destination / "packages" / source.relative_to(root / "authoring")
        shutil.copytree(source, target, dirs_exist_ok=True)
    write_json(destination / "catalogs/esrs/2023-set1/registry.json", registry)
    shutil.copytree(root / "profiles", destination / "profiles", dirs_exist_ok=True)
    return packages, registry


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Build registered standards from the unique authoring source")
    parser.add_argument("--root", type=Path, default=standard_root())
    parser.add_argument("--publish", action="store_true", help="Create missing immutable versions; never replace existing ones")
    args = parser.parse_args(argv)
    packages, registry = build_authoring(args.root, publish=args.publish)
    print(json.dumps({"status": "validated", "source": "authoring", "compiled_modules": len(packages),
        "metrics": sum(len(p.metrics) for p in packages.values()),
        "planned_modules": sum(not r["executable"] for r in registry["modules"]), "published": args.publish}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
