from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil

import pytest

from esg_v2.storage import copy_on_write as copying


def _assert_independent(source: Path, child: Path):
    assert child.read_bytes() == source.read_bytes()
    assert (child.stat().st_dev, child.stat().st_ino) != (source.stat().st_dev, source.stat().st_ino)
    original = source.read_bytes()
    with child.open("r+b") as output:
        output.write(b"CHILD")
    assert source.read_bytes() == original
    source.write_bytes(b"PARENT changed")
    assert child.read_bytes().startswith(b"CHILD")


def test_filesystem_clone_or_fallback_keeps_parent_and_child_independent(tmp_path):
    source = tmp_path / "parent.bin"
    source.write_bytes(b"unchanged parent contents" * 20)
    source.chmod(0o640)
    os.utime(source, (1_700_000_000, 1_700_000_001))
    target = tmp_path / "child.bin"
    assert copying.copy_on_write(source, target) == str(target)
    assert target.stat().st_mode == source.stat().st_mode
    assert target.stat().st_mtime_ns == source.stat().st_mtime_ns
    _assert_independent(source, target)


@pytest.mark.parametrize("partial_destination", [False, True])
def test_unsupported_clone_and_partial_clone_use_complete_copy2_fallback(tmp_path, monkeypatch, partial_destination):
    source = tmp_path / "parent.bin"
    source.write_bytes(b"unchanged parent contents")
    target = tmp_path / "child.bin"
    calls = []
    def unsupported_clone(original, destination):
        if partial_destination:
            Path(destination).write_bytes(b"partial")
        raise OSError(errno.EOPNOTSUPP, "fixture filesystem does not support cloning")
    monkeypatch.setattr(copying, "_clone_file", unsupported_clone)
    original_copy2 = shutil.copy2
    monkeypatch.setattr(copying.shutil, "copy2", lambda original, destination, **options:
        (calls.append((original, destination)), original_copy2(original, destination, **options))[1])
    copying.copy_on_write(source, target)
    assert len(calls) == 1
    _assert_independent(source, target)


def test_successful_clone_does_not_copy_file_bytes_again(tmp_path, monkeypatch):
    source = tmp_path / "parent.bin"
    source.write_bytes(b"unchanged parent contents")
    target = tmp_path / "child.bin"
    calls = []
    def clone(original, destination):
        calls.append((original, destination))
        shutil.copyfile(original, destination)
    monkeypatch.setattr(copying, "_clone_file", clone)
    monkeypatch.setattr(copying.shutil, "copy2", lambda *args, **kwargs: pytest.fail("Successful cloning must not perform copy2"))
    copying.copy_on_write(source, target)
    assert len(calls) == 1
    _assert_independent(source, target)


def test_copytree_uses_independent_assets_for_nested_paths(tmp_path):
    parent = tmp_path / "parent"
    (parent / "nested").mkdir(parents=True)
    source = parent / "nested" / "crop.png"
    source.write_bytes(b"parent crop")
    child = tmp_path / "child"
    shutil.copytree(parent, child, copy_function=copying.copy_on_write)
    _assert_independent(source, child / "nested" / "crop.png")
