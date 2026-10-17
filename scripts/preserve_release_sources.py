"""Preserve current executable release bytes; verification is read-only by default.

Capture writes only an explicitly selected repository-external CAS. It never
restores source, edits historical migration receipts, or replaces a manifest.
The old release-source-preservation-v1 format is supported as a read-only parent
with an explicit blob base; it cannot prove its original component fingerprint.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile


SCHEMA = "release-source-cas-v1"
LEGACY_SCHEMA = "release-source-preservation-v1"
HEX = re.compile(r"^[0-9a-f]{64}$")
EXCLUDED = {".local", ".git", "node_modules", "__pycache__", ".venv", "venv",
            "ocr_output", "document_ir_output", "targeted_extract_output", "pdf"}
FIELDS = {"schema_version", "created_at_utc", "release_id", "release_identity",
          "source_scope", "file_count", "files", "parent_manifest_sha256", "parent",
          "migration_receipts", "test_receipts", "source_restoration_performed",
          "manifest_payload_sha256"}


class PreservationError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreservationError("Duplicate manifest JSON key")
        result[key] = value
    return result


def _hex(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise PreservationError("Invalid SHA-256")
    return value


def _integer(value):
    if type(value) is not int or value < 0:
        raise PreservationError("Invalid byte count")
    return value


def _relative(value, *, source=False):
    if not isinstance(value, str) or not value or "\\" in value:
        raise PreservationError("Invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {".", ".."} for p in value.split("/")) or str(path) != value:
        raise PreservationError("Path escapes preservation scope")
    name = path.name.lower()
    if source and (EXCLUDED.intersection(path.parts) or name.startswith(".env")
                   or name in {"review-token", "lifecycle-token", "credentials.json", "secrets.json"}
                   or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
        raise PreservationError("Runtime or credential path is not executable source")
    return path


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PreservationError("Missing or symlinked preservation file: " + str(path))
    return path.read_bytes()


def _inside(base, relative):
    base = Path(base).resolve()
    relative = _relative(relative)
    path = base.joinpath(*relative.parts)
    for parent in [path, *path.parents]:
        if parent == base:
            break
        if parent.is_symlink():
            raise PreservationError("Symlinked preservation path")
    if base not in path.resolve().parents:
        raise PreservationError("Preservation file escapes its store")
    return path


def _load_provider(repo):
    path = repo / "ExtractESG/scripts/workbench_release.py"
    spec = importlib.util.spec_from_file_location("preservation_release_provider", path)
    if spec is None or spec.loader is None:
        raise PreservationError("Workbench release provider is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _components(rows, contents, names):
    groups = {}
    for group in names:
        h = hashlib.sha256()
        # release_files sorts Path objects by path components. Sorting the
        # complete string differs when a file and a same-stem directory coexist
        # (e.g. lifecycle.py and lifecycle/records.py), changing the digest.
        for row in sorted((r for r in rows if group in r["groups"]), key=lambda r: Path(r["path"])):
            h.update(row["path"].encode()); h.update(b"\0")
            h.update(contents[row["path"]]); h.update(b"\0")
        groups[group] = h.hexdigest()
    return groups


def _fingerprint(components):
    # Match workbench_release.release_identity, including its JSON separators.
    return digest(json.dumps(components, sort_keys=True).encode())


def _write_once(path, data, *, reusable_blob=False):
    path = Path(path)
    if path.exists() or path.is_symlink():
        if reusable_blob and _read(path) == data:
            return
        raise PreservationError("Existing preservation file is never overwritten: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Hard-link publication is atomic and cannot replace an existing name.
    fd, name = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        temporary.chmod(0o444)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not reusable_blob or _read(path) != data:
                raise PreservationError("Concurrent preservation conflict: " + str(path))
    finally:
        temporary.unlink(missing_ok=True)


def _reference(path):
    path = Path(path).expanduser().absolute()
    _relative(path.name, source=True)
    data = _read(path)
    return {"path": str(path), "sha256": digest(data), "size": len(data)}


def _verify_references(refs):
    if not isinstance(refs, list):
        raise PreservationError("Receipt references must be a list")
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "size"}:
            raise PreservationError("Malformed receipt reference")
        if not isinstance(ref["path"], str) or not Path(ref["path"]).is_absolute() or ref["path"] in seen:
            raise PreservationError("Invalid or duplicate receipt path")
        _relative(Path(ref["path"]).name, source=True)
        seen.add(ref["path"])
        data = _read(ref["path"])
        if len(data) != _integer(ref["size"]) or digest(data) != _hex(ref["sha256"]):
            raise PreservationError("Referenced receipt changed")


def verify(manifest_path, *, legacy_base=None, expected_sha256=None, _seen=None):
    """Validate preserved bytes and complete parent chain; never read live source."""
    path = Path(manifest_path).expanduser().absolute()
    data = _read(path)
    sha = digest(data)
    if expected_sha256 is not None and sha != _hex(expected_sha256):
        raise PreservationError("Manifest SHA differs from the pinned reference")
    seen = set() if _seen is None else set(_seen)
    if sha in seen or len(seen) >= 128:
        raise PreservationError("Cyclic or excessively deep parent chain")
    seen.add(sha)
    try:
        manifest = json.loads(data, object_pairs_hook=_object)
    except PreservationError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise PreservationError("Malformed preservation manifest") from exc
    if not isinstance(manifest, dict):
        raise PreservationError("Manifest must be an object")
    schema = manifest.get("schema_version")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise PreservationError("Manifest has no executable sources")
    _hex(manifest.get("release_id"))
    names, contents = set(), {}
    if schema == LEGACY_SCHEMA:
        if legacy_base is None:
            raise PreservationError("Legacy manifest requires explicit --legacy-base")
        if manifest.get("all_expected_sources_preserved") is not True or manifest.get("missing_exact_sources") != [] \
                or _integer(manifest.get("expected_files")) != len(rows):
            raise PreservationError("Legacy preservation is incomplete")
        _hex(manifest.get("source_manifest_sha256"))
        store = Path(legacy_base).resolve()
        level = "legacy_exact_source_bytes_only"
    elif schema == SCHEMA:
        if set(manifest) != FIELDS or manifest.get("source_scope") != "workbench_release.release_files" \
                or manifest.get("source_restoration_performed") is not False:
            raise PreservationError("Unexpected preservation manifest contract")
        payload = {k: v for k, v in manifest.items() if k != "manifest_payload_sha256"}
        if digest(canonical(payload)) != _hex(manifest["manifest_payload_sha256"]):
            raise PreservationError("Manifest payload changed")
        if _integer(manifest["file_count"]) != len(rows):
            raise PreservationError("Manifest file count is incomplete")
        timestamp = manifest["created_at_utc"]
        if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).utcoffset() is None \
                or datetime.fromisoformat(timestamp).utcoffset().total_seconds() != 0:
            raise PreservationError("Manifest capture time must be UTC")
        store = path.parent.parent.resolve()
        level = "release_components_and_exact_source_bytes"
    else:
        raise PreservationError("Unsupported preservation manifest schema")
    for row in rows:
        keys = {"path", "sha256", "blob", "size"} | ({"groups"} if schema == SCHEMA else set())
        if not isinstance(row, dict) or set(row) != keys:
            raise PreservationError("Malformed source entry")
        relative = str(_relative(row["path"], source=True))
        if relative in names:
            raise PreservationError("Duplicate canonical source path")
        names.add(relative)
        source_sha = _hex(row["sha256"])
        expected_blob = f"blobs/sha256/{source_sha[:2]}/{source_sha}" if schema == SCHEMA else f"backups/release-source-blobs/{source_sha}"
        if row["blob"] != expected_blob:
            raise PreservationError("Blob path is not content-addressed")
        raw = _read(_inside(store, row["blob"]))
        if digest(raw) != source_sha or len(raw) != _integer(row["size"]):
            raise PreservationError("Preserved source bytes changed")
        contents[relative] = raw
    parent_result = None
    if schema == SCHEMA:
        identity = manifest["release_identity"]
        if not isinstance(identity, dict) or set(identity) != {"protocol_version", "components", "build_fingerprint", "contracts"} \
                or identity["protocol_version"] != "workbench-compatibility-v1" or not isinstance(identity["contracts"], dict) \
                or not isinstance(identity["components"], dict) or not identity["components"]:
            raise PreservationError("Malformed release identity")
        for group, group_sha in identity["components"].items():
            if not isinstance(group, str) or not group:
                raise PreservationError("Invalid release component")
            _hex(group_sha)
        for row in rows:
            groups = row["groups"]
            if not isinstance(groups, list) or not groups or any(not isinstance(g, str) for g in groups) \
                    or groups != sorted(set(groups)) or not set(groups).issubset(identity["components"]):
                raise PreservationError("Source component membership is invalid")
        components = _components(rows, contents, identity["components"])
        if components != identity["components"] or _fingerprint(components) != identity["build_fingerprint"] \
                or identity["build_fingerprint"] != manifest["release_id"]:
            raise PreservationError("Preserved sources do not reproduce the release fingerprint")
        _verify_references(manifest["migration_receipts"])
        _verify_references(manifest["test_receipts"])
        parent = manifest["parent"]
        if parent is None:
            if manifest["parent_manifest_sha256"] is not None:
                raise PreservationError("Parent SHA has no manifest reference")
        else:
            if not isinstance(parent, dict) or set(parent) != {"path", "sha256", "legacy_base"} \
                    or not isinstance(parent["path"], str) or not Path(parent["path"]).is_absolute() \
                    or parent["legacy_base"] is not None and (not isinstance(parent["legacy_base"], str) or not Path(parent["legacy_base"]).is_absolute()) \
                    or _hex(parent["sha256"]) != manifest["parent_manifest_sha256"]:
                raise PreservationError("Invalid parent manifest reference")
            parent_result = verify(parent["path"], legacy_base=parent["legacy_base"],
                expected_sha256=parent["sha256"], _seen=seen)
    return {"schema_version": schema, "manifest_path": str(path), "manifest_sha256": sha,
            "release_id": manifest["release_id"], "file_count": len(rows), "validation_level": level,
            "parent": parent_result, "mode": "read_only", "source_restoration_performed": False}


def capture(repo_root, output_root, *, parent_manifest=None, parent_legacy_base=None,
            migration_receipts=(), test_receipts=()):
    """Explicit capture; existing CAS blobs may be reused only byte-for-byte."""
    repo = Path(repo_root).expanduser().resolve()
    output_argument = Path(output_root).expanduser().absolute()
    output = output_argument.resolve()
    if output_argument.is_symlink() or output == repo or repo in output.parents or output in repo.parents:
        raise PreservationError("Capture output must be a separate repository-external directory")
    provider = _load_provider(repo)
    extract = repo / "ExtractESG"
    rows, contents, memberships = [], {}, {}
    groups = provider.release_files(extract)
    for group, paths in groups.items():
        for path in paths:
            path = Path(path).resolve()
            if repo not in path.parents:
                raise PreservationError("Release provider selected source outside repository")
            relative = str(_relative(path.relative_to(repo).as_posix(), source=True))
            contents[relative] = _read(path)
            memberships.setdefault(relative, set()).add(group)
    if not contents:
        raise PreservationError("No executable source files selected")
    identity = provider.release_identity(extract)
    manifest_path = output / "releases" / ("release-" + _hex(identity["build_fingerprint"]) + ".json")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise PreservationError("An immutable manifest already exists for this release")
    for relative, raw in sorted(contents.items()):
        source_sha = digest(raw)
        rows.append({"path": relative, "groups": sorted(memberships[relative]), "sha256": source_sha,
                     "size": len(raw), "blob": f"blobs/sha256/{source_sha[:2]}/{source_sha}"})
    if _components(rows, contents, groups) != identity["components"]:
        raise PreservationError("Sources changed while collecting release identity")
    parent = None
    if parent_manifest is not None:
        result = verify(parent_manifest, legacy_base=parent_legacy_base)
        parent = {"path": result["manifest_path"], "sha256": result["manifest_sha256"],
                  "legacy_base": str(Path(parent_legacy_base).resolve()) if parent_legacy_base is not None else None}
    elif parent_legacy_base is not None:
        raise PreservationError("Legacy parent base requires a parent manifest")
    migration_refs = [_reference(p) for p in migration_receipts]
    test_refs = [_reference(p) for p in test_receipts]
    _verify_references(migration_refs); _verify_references(test_refs)
    # Recheck bytes and membership, not just mtime-based provider cache keys.
    final_groups = provider.release_files(extract)
    final_membership = {g: [str(Path(p).resolve()) for p in paths] for g, paths in final_groups.items()}
    initial_membership = {g: [str(Path(p).resolve()) for p in paths] for g, paths in groups.items()}
    if final_membership != initial_membership or any(_read(repo / r) != raw for r, raw in contents.items()) \
            or provider.release_identity(extract) != identity:
        raise PreservationError("Source release drifted during capture; no manifest written")
    manifest = {"schema_version": SCHEMA, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "release_id": identity["build_fingerprint"], "release_identity": identity,
                "source_scope": "workbench_release.release_files", "file_count": len(rows), "files": rows,
                "parent_manifest_sha256": parent["sha256"] if parent else None, "parent": parent,
                "migration_receipts": migration_refs, "test_receipts": test_refs,
                "source_restoration_performed": False}
    manifest["manifest_payload_sha256"] = digest(canonical(manifest))
    for row in rows:
        _write_once(_inside(output, row["blob"]), contents[row["path"]], reusable_blob=True)
    _write_once(_inside(output, str(manifest_path.relative_to(output))),
                json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n")
    return {**verify(manifest_path), "operation": "capture", "capture_output": str(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", nargs="?", choices=("verify", "capture"), default="verify")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--legacy-base", type=Path)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--parent-manifest", type=Path)
    parser.add_argument("--parent-legacy-base", type=Path)
    parser.add_argument("--migration-receipt", type=Path, action="append", default=[])
    parser.add_argument("--test-receipt", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.operation == "capture":
            if args.output is None or args.manifest or args.legacy_base or args.expected_manifest_sha256:
                parser.error("capture requires --output and does not accept verify-only options")
            result = capture(args.root, args.output, parent_manifest=args.parent_manifest,
                parent_legacy_base=args.parent_legacy_base, migration_receipts=args.migration_receipt,
                test_receipts=args.test_receipt)
        else:
            if args.manifest is None or args.output or args.parent_manifest or args.parent_legacy_base \
                    or args.migration_receipt or args.test_receipt:
                parser.error("verify requires --manifest and accepts no capture options")
            result = verify(args.manifest, legacy_base=args.legacy_base, expected_sha256=args.expected_manifest_sha256)
    except (PreservationError, OSError, ValueError, KeyError, TypeError) as exc:
        print("Source preservation failed: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
