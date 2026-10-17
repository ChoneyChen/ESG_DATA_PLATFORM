"""Synthetic executable sources only; no report, runtime DB or model fixtures."""
import importlib.util
import json
from pathlib import Path

import pytest


TOOL_PATH = Path(__file__).resolve().parents[1] / "preserve_release_sources.py"
spec = importlib.util.spec_from_file_location("preserve_sources_test_module", TOOL_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    repo = tmp_path / "synthetic-repo"
    files = [repo / "ExtractESG/module.py", repo / "ConsoleESG/view.tsx"]
    for path, data in zip(files, [b"VALUE = 1\n", b"export const View = 1;\n"]):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    groups = {"backend": files[:1], "frontend": files[1:], "shared": files[:1], "empty": []}

    # Exercise the actual fingerprint implementation on synthetic paths only.
    # No production reports/results, service clients or model fixtures loaded.
    provider = tool._load_provider(Path(__file__).resolve().parents[2])
    provider.release_files = lambda _: groups
    monkeypatch.setattr(tool, "_load_provider", lambda _: provider)
    return repo, tmp_path / "preservation", files, provider


def manifest_path(result):
    return Path(result["manifest_path"])


def test_capture_keeps_exact_bytes_and_verify_never_reads_current_source(synthetic):
    repo, output, files, _ = synthetic
    result = tool.capture(repo, output)
    m = manifest_path(result)
    raw = json.loads(m.read_text())
    assert raw["file_count"] == 2
    assert raw["files"][1]["groups"] == ["backend", "shared"]
    inventory = {str(p): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    for p in files: p.unlink()
    verified = tool.verify(m, expected_sha256=result["manifest_sha256"])
    assert verified["validation_level"] == "release_components_and_exact_source_bytes"
    assert verified["mode"] == "read_only"
    assert inventory == {str(p): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_component_order_matches_provider_for_file_and_same_stem_directory(synthetic):
    repo, output, _, provider = synthetic
    directory = repo / "ExtractESG/shared_runtime/src/esg_runtime"
    files = [directory / "lifecycle.py", directory / "lifecycle/records.py"]
    for path, raw in zip(files, [b"SYNTHETIC_LIFECYCLE = 1\n", b"SYNTHETIC_RECORD = 2\n"]):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    # The authoritative provider canonicalizes every group as sorted Paths.
    provider.release_files = lambda _: {"standard_contracts": sorted(set(files)), "empty": []}
    assert sorted(files) != sorted(files, key=str)
    expected = provider.release_identity(repo / "ExtractESG")
    result = tool.capture(repo, output)
    manifest = manifest_path(result)
    frozen = json.loads(manifest.read_text())
    assert frozen["release_identity"] == expected
    assert frozen["release_id"] == expected["build_fingerprint"]
    # Verification must reconstruct the same order solely from frozen bytes.
    for path in files: path.unlink()
    assert tool.verify(manifest)["release_id"] == expected["build_fingerprint"]


def test_immutable_manifest_and_blob_are_never_overwritten(synthetic):
    repo, output, _, _ = synthetic
    first = tool.capture(repo, output)
    before = manifest_path(first).read_bytes()
    with pytest.raises(tool.PreservationError, match="already exists"):
        tool.capture(repo, output)
    assert manifest_path(first).read_bytes() == before


def test_current_release_parent_and_receipt_hashes_are_verified(synthetic, tmp_path):
    repo, output, files, _ = synthetic
    receipt = tmp_path / "synthetic-migration.json"; receipt.write_text('{"synthetic":true}')
    log = tmp_path / "synthetic-test.log"; log.write_text("synthetic unit passed\n")
    parent = tool.capture(repo, output)
    files[0].write_text("VALUE = 2\n")
    child = tool.capture(repo, output, parent_manifest=manifest_path(parent), migration_receipts=[receipt], test_receipts=[log])
    assert child["parent"]["manifest_sha256"] == parent["manifest_sha256"]
    log.write_text("changed")
    with pytest.raises(tool.PreservationError, match="receipt changed"):
        tool.verify(manifest_path(child))


def test_legacy_parent_exact_bytes_require_explicit_base(synthetic, tmp_path):
    repo, output, _, _ = synthetic
    base = tmp_path / "legacy-source-store"; sha = tool.digest(b"old synthetic source\n")
    blob = base / "backups/release-source-blobs" / sha
    blob.parent.mkdir(parents=True); blob.write_bytes(b"old synthetic source\n")
    legacy = tmp_path / "synthetic-legacy-manifest.json"
    legacy.write_text(json.dumps({"schema_version":tool.LEGACY_SCHEMA, "release_id":"a"*64,
        "source_manifest_sha256":"b"*64, "files":[{"path":"ExtractESG/old.py","sha256":sha,
        "blob":f"backups/release-source-blobs/{sha}","size":len(b"old synthetic source\n")}],
        "expected_files":1,"missing_exact_sources":[],"all_expected_sources_preserved":True}))
    with pytest.raises(tool.PreservationError, match="explicit"):
        tool.verify(legacy)
    result = tool.capture(repo, output, parent_manifest=legacy, parent_legacy_base=base)
    assert result["parent"]["validation_level"] == "legacy_exact_source_bytes_only"
    blob.write_text("corrupt")
    with pytest.raises(tool.PreservationError, match="source bytes changed"):
        tool.verify(manifest_path(result))


def test_source_drift_during_capture_writes_no_manifest(synthetic):
    repo, output, files, provider = synthetic
    original = provider.release_files
    calls = 0

    def drifting(root):
        nonlocal calls
        calls += 1
        if calls == 3: files[0].write_text("VALUE = 100\n")
        return original(root)

    provider.release_files = drifting
    with pytest.raises(tool.PreservationError, match="drifted"):
        tool.capture(repo, output)
    assert not list(output.rglob("*.json"))


@pytest.mark.parametrize("relative", [".local/token.json", "ExtractESG/ocr_output/source.py", ".env.local", "credentials.json", "private.key"])
def test_runtime_or_credential_selection_is_rejected(synthetic, relative):
    repo, output, files, provider = synthetic
    forbidden = repo / relative; forbidden.parent.mkdir(parents=True, exist_ok=True); forbidden.write_text("secret")
    provider.release_files = lambda _: {"backend":[forbidden]}
    with pytest.raises(tool.PreservationError, match="Runtime or credential"):
        tool.capture(repo, output)
    assert not output.exists()


def test_repository_internal_output_and_source_escape_are_rejected(synthetic, tmp_path):
    repo, output, _, provider = synthetic
    with pytest.raises(tool.PreservationError, match="external"):
        tool.capture(repo, repo / "capture")
    outside = tmp_path / "unrelated.py"; outside.write_text("unrelated")
    provider.release_files = lambda _: {"backend":[outside]}
    with pytest.raises(tool.PreservationError, match="outside repository"):
        tool.capture(repo, output)


def test_cas_corruption_is_not_replaced(synthetic):
    repo, output, _, _ = synthetic
    first = tool.capture(repo, output)
    row = json.loads(manifest_path(first).read_text())["files"][0]
    blob = output / row["blob"]; blob.chmod(0o600); blob.write_text("corrupt")
    with pytest.raises(tool.PreservationError, match="source bytes changed"):
        tool.verify(manifest_path(first))
    assert blob.read_text() == "corrupt"


def test_missing_authoritative_source_is_not_silently_omitted(synthetic):
    repo, output, files, _ = synthetic
    files[0].unlink()
    with pytest.raises(tool.PreservationError, match="Missing"):
        tool.capture(repo, output)
    assert not output.exists()


def test_symlinked_store_and_credential_receipt_are_rejected(synthetic, tmp_path):
    repo, output, _, _ = synthetic
    actual = tmp_path / "other-store"; actual.mkdir()
    output.symlink_to(actual, target_is_directory=True)
    with pytest.raises(tool.PreservationError, match="external"):
        tool.capture(repo, output)
    output.unlink()
    credential = tmp_path / "credentials.json"; credential.write_text('{"synthetic_secret":true}')
    with pytest.raises(tool.PreservationError, match="Runtime or credential"):
        tool.capture(repo, output, test_receipts=[credential])
    assert not output.exists()


def test_parent_manifest_mutation_is_detected(synthetic):
    repo, output, files, _ = synthetic
    parent = tool.capture(repo, output)
    files[0].write_text("VALUE = 3\n")
    child = tool.capture(repo, output, parent_manifest=manifest_path(parent))
    p = manifest_path(parent); p.chmod(0o600); p.write_bytes(p.read_bytes()+b" ")
    with pytest.raises(tool.PreservationError, match="pinned reference"):
        tool.verify(manifest_path(child))


def test_default_cli_is_read_only_verify(synthetic, capsys):
    repo, output, _, _ = synthetic
    first = tool.capture(repo, output)
    assert tool.main(["--manifest", str(manifest_path(first))]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "read_only"


def test_duplicate_json_key_and_incomplete_group_membership_fail_closed(synthetic):
    repo, output, _, _ = synthetic
    first = tool.capture(repo, output)
    p = manifest_path(first); original = p.read_text(); p.chmod(0o600)
    p.write_text(original[:-2] + ', "release_id":"'+'c'*64+'"}\n')
    with pytest.raises(tool.PreservationError, match="Duplicate"):
        tool.verify(p)
    m = json.loads(original); m["files"][0]["groups"] = ["unknown"]
    m["manifest_payload_sha256"] = tool.digest(tool.canonical({k:v for k,v in m.items() if k!="manifest_payload_sha256"}))
    p.write_text(json.dumps(m))
    with pytest.raises(tool.PreservationError, match="membership"):
        tool.verify(p)
