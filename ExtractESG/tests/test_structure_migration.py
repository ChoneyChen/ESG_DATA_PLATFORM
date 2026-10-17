"""Source layout compatibility and immutable standard registration regression."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from esg_runtime.layout import PROJECT_ROOT, DOCUMENT_APP_ROOT, EXTRACTION_APP_ROOT
from esg_standard_packages.build import build_authoring
from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.compiler import StandardPackageCompiler
from esg_targeted.standards.catalog import StandardPackageCatalog

ROOT = Path(__file__).resolve().parents[2]
STANDARDS = ROOT / "ExtractESG/standards"


def canonical_copy(tmp_path):
    root = tmp_path / "standards"
    for name in ("authoring", "catalog", "registry", "releases", "schemas"):
        shutil.copytree(STANDARDS / name, root / name)
    return root


def test_old_import_paths_resolve_to_one_canonical_source_and_keep_runtime_roots():
    assert PROJECT_ROOT == ROOT
    assert DOCUMENT_APP_ROOT == ROOT / "ExtractESG/document_ir_extract"
    assert EXTRACTION_APP_ROOT == ROOT / "ExtractESG/targeted_table_extract"
    receipt = json.loads((ROOT / "docs/migrations/2026-10-05-p6-python-domains.json").read_text())
    for move in receipt["moves"]:
        legacy, canonical = ROOT / move["old_path"], ROOT / move["canonical_path"]
        assert legacy.is_symlink()
        assert legacy.resolve() == canonical.resolve()
        assert not canonical.is_symlink()
    from esg_v2.config import DOCUMENT_IR_OUTPUT_ROOT, REPORT_ASSET_ROOT
    from esg_targeted.config import Settings
    assert DOCUMENT_IR_OUTPUT_ROOT == DOCUMENT_APP_ROOT / "document_ir_output"
    assert REPORT_ASSET_ROOT == ROOT.parent / "pdf"
    assert Settings.load().output_root == EXTRACTION_APP_ROOT / "targeted_extract_output"


def test_registered_current_sources_and_all_original_ids_and_history_survive():
    registry = json.loads((STANDARDS / "registry/esrs/2023-set1/registry.json").read_text())
    index = json.loads((STANDARDS / "registry/releases.json").read_text())["releases"]
    assert len([r for r in registry["modules"] if r["executable"]]) == 41
    assert len([r for r in registry["modules"] if not r["executable"]]) == 58
    assert len(index) == 95
    assert len([r for r in index if r["state"] == "retired"]) == 54
    for entry in registry["modules"]:
        if entry["executable"]:
            assert (STANDARDS / entry["source_directory"]).is_relative_to(STANDARDS / "authoring")
    catalog = StandardPackageCatalog(STANDARDS / "releases")
    for release in index:
        compiled = catalog.load(release["package_id"], release["package_version"])
        assert compiled.compilation.source_digest == release["source_digest"]
        assert hashlib.sha256((STANDARDS / release["path"]).read_bytes()).hexdigest() == release["sha256"]
    major = json.loads((STANDARDS / "profiles/esrs/2023-set1/platform-major.json").read_text())
    assert len(major["source_datapoint_ids"]) == 49
    assert "E1-5_01" in major["source_datapoint_ids"] and "G1-6_01" in major["source_datapoint_ids"]


def test_registered_catalog_never_selects_unregistered_newer_directory(tmp_path):
    root = canonical_copy(tmp_path)
    source = root / "releases/esrs.2023-set1.e1-6/2.1.1/package.json"
    injected = root / "releases/esrs.2023-set1.e1-6/999.0.0/package.json"
    injected.parent.mkdir(parents=True)
    shutil.copyfile(source, injected)
    catalog = StandardPackageCatalog(root / "releases")
    assert len(catalog.list()) == 41
    assert all(p.package_version == "2.1.1" for p in catalog.list())
    with pytest.raises(FileNotFoundError, match="Unregistered"):
        catalog.load("esrs.2023-set1.e1-6", "999.0.0")


def test_registered_release_checksum_changes_fail_closed_and_do_not_hide_others(tmp_path):
    root = canonical_copy(tmp_path)
    path = root / "releases/esrs.2023-set1.e1-6/2.1.1/package.json"
    path.write_text(path.read_text() + "\n")
    catalog = StandardPackageCatalog(root / "releases")
    assert catalog.diagnostics()["state"] == "degraded"
    assert catalog.diagnostics()["valid_package_count"] == 40
    with pytest.raises(PackageIntegrityError, match="checksum"):
        catalog.load("esrs.2023-set1.e1-6", "2.1.1")
    with pytest.raises(PackageIntegrityError, match="checksum"):
        build_authoring(root)


def test_rebuild_is_read_only_and_same_version_author_edit_is_rejected(tmp_path):
    root = canonical_copy(tmp_path)
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ("authoring", "releases", "registry") for p in (root / folder).rglob("*.json")}
    assert len(build_authoring(root)[0]) == 41
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ("authoring", "releases", "registry") for p in (root / folder).rglob("*.json")}
    assert before == after
    author = root / "authoring/esrs/2023-set1/e1-6/2.1.1/metrics.json"
    author.write_text(author.read_text() + "\n")
    with pytest.raises(PackageIntegrityError, match="digest"):
        build_authoring(root, publish=True)
    assert {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "releases").rglob("*.json")} == {
                key: value for key, value in before.items() if key.startswith("releases/")}


def test_china_petroleum_original_frozen_standard_identity_remains_resolvable():
    frozen = EXTRACTION_APP_ROOT / "targeted_extract_output/tx-20261002T182823Z-f5b3169b0c/input/standard-package.json"
    if not frozen.is_file():
        pytest.skip("Local historical China Petroleum artifact not distributed in this checkout")
    original = json.loads(frozen.read_text())
    package = StandardPackageCatalog(STANDARDS / "releases").load(
        original["manifest"]["package_id"], original["manifest"]["package_version"])
    assert package.compilation.source_digest == original["compilation"]["source_digest"]
    assert package.manifest.standard.disclosure_requirement == "E1-6"


def test_explicit_new_version_publish_registers_only_new_identity_and_preserves_old(tmp_path):
    root = canonical_copy(tmp_path)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "releases").rglob("*.json")}
    source = root / "authoring/esrs/2023-set1/e1-6/2.1.2"
    shutil.copytree(source.with_name("2.1.1"), source)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["package_version"] = "2.1.2"
    (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    compiled = StandardPackageCompiler().compile(core_path=root / "schemas/core/1.2.0/core.json", module_dir=source)
    registration_path = root / "registry/esrs/2023-set1/registry.json"
    registry = json.loads(registration_path.read_text())
    entry = next(r for r in registry["modules"] if r["disclosure_requirement"] == "E1-6")
    entry.update(package_version="2.1.2", source_directory="authoring/esrs/2023-set1/e1-6/2.1.2",
                 runtime_path="releases/esrs.2023-set1.e1-6/2.1.2/package.json",
                 source_digest=compiled.compilation.source_digest)
    registration_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2))
    with pytest.raises(PackageIntegrityError, match="not registered"):
        build_authoring(root)
    build_authoring(root, publish=True)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
    catalog = StandardPackageCatalog(root / "releases")
    assert len(catalog.list()) == 41
    assert next(p for p in catalog.list() if p.disclosure_requirement == "E1-6").package_version == "2.1.2"
    assert catalog.load("esrs.2023-set1.e1-6", "2.1.1").manifest.package_version == "2.1.1"
    assert len(catalog.snapshot()["historical"]) == 55
