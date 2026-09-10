from pathlib import Path
import shutil
import pytest
from esg_standard_packages.compiler import StandardPackageCompiler

ROOT = Path(__file__).resolve().parents[1]


def test_successful_publish_retires_only_older_versions_of_same_package(tmp_path):
    compiler = StandardPackageCompiler()
    old = tmp_path / "dist/esrs.2023-set1.e1-6/1.0.0/package.json"
    old.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "dist/.retired/esrs.2023-set1.e1-6/1.0.0/package.json", old)
    unrelated = tmp_path / "dist/esrs.2023-set1.e2-4/1.0.0/package.json"
    unrelated.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "dist/esrs.2023-set1.e2-4/1.0.0/package.json", unrelated)
    new = tmp_path / "dist/esrs.2023-set1.e1-6/1.2.0/package.json"
    compiler.compile_to_path(core_path=ROOT / "core/1.1.0/core.json", module_dir=ROOT / "packages/esrs/2023-set1/e1-6/1.2.0", output_path=new)
    archived = tmp_path / "dist/.retired/esrs.2023-set1.e1-6/1.0.0/package.json"
    assert not old.exists()
    assert archived.is_file() and new.is_file() and unrelated.is_file()
    assert compiler.validate_compiled(archived).manifest.package_version == "1.0.0"


def test_invalid_new_package_never_retires_previous(tmp_path):
    old = tmp_path / "dist/esrs.2023-set1.e1-6/1.0.0/package.json"
    old.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "dist/.retired/esrs.2023-set1.e1-6/1.0.0/package.json", old)
    with pytest.raises(Exception):
        StandardPackageCompiler().compile_to_path(core_path=ROOT / "core/1.1.0/core.json", module_dir=tmp_path / "missing", output_path=tmp_path / "dist/esrs.2023-set1.e1-6/1.2.0/package.json")
    assert old.is_file()
