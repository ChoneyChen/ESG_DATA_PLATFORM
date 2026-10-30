"""Receive a sealed archive through a value contract, never a producer directory."""
from hashlib import sha256
import io
import json
from zipfile import ZipFile
from esg_contracts.exports import ExportManifest
from esg_contracts.facts import SourceFact, ClauseMapping, ClauseCoverage
from esg_data.hashing import digest as contract_digest

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode_archive(data):
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Export archive exceeds receiver capacity")
    with ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) != 9 or len({item.filename for item in entries}) != 9 or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("Export archive members or size are invalid")
        manifest = ExportManifest.model_validate_json(archive.read("manifest.json"))
        names = {file.name for file in manifest.files} | {"manifest.json"}
        if {item.filename for item in entries} != names:
            raise ValueError("Export archive differs from its manifest")
        members = {}
        for member in manifest.files:
            content = archive.read(member.name)
            if len(content) != member.bytes or sha256(content).hexdigest() != member.sha256:
                raise ValueError("Export member content differs from the sealed digest")
            value = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            if (len(value) if member.role != "document" else 1) != member.records:
                raise ValueError("Export member count differs from its manifest")
            members[member.role] = value
    identity = canonical([manifest.schema_version, manifest.run_id, *[file.sha256 for file in sorted(manifest.files, key=lambda file: file.role)]])
    if manifest.export_id != "export-" + sha256(identity).hexdigest():
        raise ValueError("Export identity differs from its sealed members")
    if members["document"] != manifest.document.model_dump(mode="json"):
        raise ValueError("Export document differs from the frozen source")
    if set(members["targets"]) != set(manifest.clause_targets) or any(
        target.get("clause_digest") != manifest.clause_targets[key] for key, target in members["targets"].items()
    ):
        raise ValueError("Export targets differ from the frozen selection")
    if any(contract_digest({key: value for key, value in target.items() if key != "clause_digest"}) != target["clause_digest"]
           for target in members["targets"].values()):
        raise ValueError("Frozen target content differs from its declared digest")
    rows, rejected, seen = [], [], set()
    for index, body in enumerate(members["facts"]):
        try:
            fact = SourceFact.model_validate(body)
            if (fact.document_id, fact.ir_run_id, fact.ir_revision) != (manifest.document.document_id, manifest.document.ir_run_id, manifest.document.ir_revision):
                raise ValueError("Fact belongs to a different document revision")
            if fact.source_fact_id in seen:
                raise ValueError("Duplicate source fact identity")
            seen.add(fact.source_fact_id)
            rows.append({"fact": fact.model_dump(mode="json"), "digest": sha256(canonical(body)).hexdigest()})
        except (ValueError, TypeError) as exc:
            rejected.append({"index": index, "source_fact_id": body.get("source_fact_id") if isinstance(body, dict) else None,
                "code": "invalid_source_fact"})
    relations = []
    relation_diagnostics = []
    for body in members["relations"]:
        try:
            link = ClauseMapping.model_validate(body)
            if manifest.clause_targets.get(link.clause_key) != link.clause_digest or link.source_fact_id not in seen:
                raise ValueError("Relation refers to an unavailable fact or target")
            if any(old["source_fact_id"] == link.source_fact_id and old["clause_key"] == link.clause_key for old in relations):
                raise ValueError("Duplicate relation")
            relations.append(link.model_dump(mode="json"))
        except (ValueError, TypeError):
            relation_diagnostics.append({"code": "invalid_relation"})
    for body in members["coverage"]:
        coverage = ClauseCoverage.model_validate(body)
        if manifest.clause_targets.get(coverage.clause_key) != coverage.clause_digest:
            raise ValueError("Coverage refers to a different frozen target")
    return {"manifest": manifest.model_dump(mode="json"), "digest": sha256(canonical(manifest.model_dump(mode="json"))).hexdigest(),
        "facts": rows, "rejected": rejected, "relations": relations, "targets": members["targets"],
        "coverage": members["coverage"], "diagnostics": [*members["diagnostics"], *relation_diagnostics]}
