"""Immutable, self-contained export. Views and filesystem layout are not APIs."""
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from zipfile import ZipFile, ZIP_DEFLATED
from esg_contracts.results import FactBundle
from esg_contracts.exports import ExportManifest, ExportFile
from esg_contracts.annotations import property_tables


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def seal_export(run_id, store, artifacts):
    state = store.get(run_id)
    if state["status"] not in {"completed", "partial"}:
        raise ValueError("Only an ended extraction can be sealed for delivery")
    bundle = FactBundle.model_validate(artifacts.read(run_id, "results/facts.json"))
    facts = [fact.model_dump(mode="json") for fact in bundle.facts]
    properties, evidence = property_tables(facts)
    try:
        candidates = artifacts.read(run_id, "diagnostics/candidates.json")
    except FileNotFoundError:
        candidates = []
    members = {
        "facts": facts, "properties": properties, "evidence": evidence,
        "relations": [link.model_dump(mode="json") for link in bundle.clause_mappings],
        "coverage": [c.model_dump(mode="json") for c in bundle.coverage],
        "targets": state["targets"]["clauses"],
        "document": bundle.document.model_dump(mode="json"), "diagnostics": candidates,
    }
    files = []
    for role, content in sorted(members.items()):
        data = json_bytes(content)
        files.append(ExportFile(name=role + ".json", role=role, sha256=sha256(data).hexdigest(), bytes=len(data), records=len(content) if role != "document" else 1))
    identity = json_bytes(["extraction-export-1.1", run_id, *[f.sha256 for f in files]])
    export_id = "export-" + sha256(identity).hexdigest()
    name = f"exports/{export_id}/manifest.json"
    try:
        return ExportManifest.model_validate(artifacts.read(run_id, name))
    except FileNotFoundError:
        pass
    manifest = ExportManifest(authority="facts", export_id=export_id, run_id=run_id, engine_version=bundle.engine_version, engine_identity=bundle.engine_identity,
        document=bundle.document, clause_targets=bundle.clause_targets,
        execution_state="completed" if state["status"] == "completed" else "interrupted",
        sealed_at=datetime.now(timezone.utc).isoformat(), files=files)
    for role, content in members.items():
        # JSON formatting is part of the file digest, and only these canonical
        # bytes are handed off. Runtime files remain human-readable independently.
        path = artifacts.path(run_id, f"exports/{export_id}/{role}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes(content))
    artifacts.write(run_id, name, manifest.model_dump(mode="json"))
    return manifest


def export_archive(run_id, manifest, artifacts):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest.model_dump(mode="json")))
        for member in manifest.files:
            data = artifacts.path(run_id, f"exports/{manifest.export_id}/{member.name}").read_bytes()
            if sha256(data).hexdigest() != member.sha256:
                raise ValueError("Sealed export member was changed")
            archive.writestr(member.name, data)
    return stream.getvalue()
