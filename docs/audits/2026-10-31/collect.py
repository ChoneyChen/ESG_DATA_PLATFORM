"""Read-only audit: source files, frozen evidence, in-memory binding, GET APIs.

No model calls, service changes, repository saves or database writes. Only the
explicit evidence output is written. Run using ExtractESG/.local/python/bin/python.
"""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import build_opener, ProxyHandler
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
for relative in ("ExtractESG/contracts/src", "ClauseESG/src", "ExtractESG/shared_runtime/src",
                 "ExtractESG/extraction/src", "database/src"):
    sys.path.insert(0, str(ROOT / relative))

from esg_clauses.contract import ClauseContract
from esg_clauses.scope import make_scope
from esg_contracts.evidence import DocumentReference, EvidenceFrame
from esg_contracts.exports import ExportManifest, ExportFile
from esg_extraction.pipeline.binding import numeric_fact, context_from_claims, mapping_for
from esg_extraction.pipeline.decisions import NumericMatch
from esg_extraction.pipeline.structure import period_columns
from esg_extraction.pipeline.exporting import json_bytes, property_tables
from esg_database.ingestion import decode_archive

RUN_ID = "extract-1099dd301a241c409a0ab052027c689c"
EXPORT_ID = "export-53416b2c235c075addd352562fb717bf79fe9ba8ba3590024f34f1f1166d17ab"


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def reference(relative, needle):
    lines = (ROOT / relative).read_text().splitlines()
    index = next(i for i, line in enumerate(lines, 1) if needle in line)
    return {"path": relative, "line": index, "excerpt": lines[index - 1].strip(),
            "sha256": sha256((ROOT / relative).read_bytes()).hexdigest()}


def get(path):
    try:
        with build_opener(ProxyHandler({})).open(path, timeout=5) as response:
            return json.load(response)
    except (OSError, ValueError) as error:
        return {"observation_error": type(error).__name__}


def collect():
    clauses = [json.loads(path.read_text()) for path in sorted((ROOT / "ClauseESG/clauses").glob("*/*/clause.json"))]
    for clause in clauses:
        ClauseContract.model_validate(clause).verify()
    blocked = [{"clause_key": c["clause_key"], "title": c["labels"].get("zh") or c["labels"].get("en"),
                "data_kind": c["data_kind"], "blocking_reasons": c["blocking_reasons"],
                "definition": c["definition"], "path": f"ClauseESG/clauses/{c['group_id']}/{c['clause_key']}/clause.json"}
               for c in clauses if c["readiness"] == "blocked"]
    gaps = [{"clause_key": c["clause_key"], "axes": sorted(set(c["value"]["identity_axes"]) - set(c["purposes"]["enrichment"]["identity_axes"]))}
            for c in clauses]
    gaps = [g for g in gaps if g["axes"]]
    run = ROOT / ".local/data/extractions" / RUN_ID
    bundle = json.loads((run / "results/facts.json").read_text())
    document = DocumentReference.model_validate(bundle["document"])
    clause = ClauseContract.model_validate(json.loads((run / "input/clauses.json").read_text())["E2-4_02"])
    frame = next(EvidenceFrame.model_validate_json(path.read_text()) for path in sorted((run / "evidence").glob("*.json"))
                 if json.loads(path.read_text())["kind"] == "table")
    facts, mappings, rejected = [], [], []
    for row in frame.rows:
        for cell in row.cells:
            match = NumericMatch(row_id=row.row_id, columns=[cell.column_id], selection="measurement",
                                 reason="Read-only bindability probe, not a model or production run", match="match")
            fact, mapping, error = numeric_fact(clause, document, frame, match, row, cell, "audit-in-memory")
            if fact:
                facts.append(fact)
                mappings.append(mapping)
            else:
                rejected.append({"label": row.label, "column": cell.column_id, "raw": cell.raw_text, "reason": error})
    air_label = lambda label: any(term in (label or "") for term in ("氮氧化物", "二氧化硫", "挥发性有机物"))
    air = lambda fact: air_label(fact.source_row_label)
    expected_air = [{"label": f.source_row_label, "year": f.identity.reporting_period.value,
                     "raw": f.value.value_raw, "unit": f.value.unit_raw} for f in facts if air(f)]
    actual_air = [{"label": f["source_row_label"], "year": f["identity"]["reporting_period"]["value"],
                   "raw": f["value"]["value_raw"], "unit": f["value"]["unit_raw"]}
                  for f in bundle["facts"] if air_label(f["source_row_label"])]
    no_period = frame.model_copy(update={"columns": {"c2": ["数值"]}, "column_sources": {}})
    context, _ = context_from_claims(no_period, [], column_id="c2", document=document)
    isolated_scope = make_scope("任何年度", period="annual")
    isolated = clause.model_copy(update={"output_scopes": [isolated_scope]})
    scope_probe = [{"year": f.identity.reporting_period.value,
                    "covered": bool(mapping_for(isolated, document, f, "match", "audit").scope_ids)}
                   for f in facts if air(f) and "氮氧化物" in f.source_row_label]
    # A complete transport is assembled only in memory. This proves acceptance
    # format capacity, not persistence, fresh extraction or scientific approval.
    bodies = [f.model_dump(mode="json") for f in facts]
    properties, evidence = property_tables(bodies)
    members = {"facts": bodies, "properties": properties, "evidence": evidence,
               "relations": [m.model_dump(mode="json") for m in mappings], "coverage": [],
               "targets": {clause.clause_key: clause.model_dump(mode="json")},
               "document": document.model_dump(mode="json"), "diagnostics": []}
    files = [ExportFile(name=role + ".json", role=role, sha256=sha256(json_bytes(value)).hexdigest(),
                        bytes=len(json_bytes(value)), records=1 if role == "document" else len(value))
             for role, value in sorted(members.items())]
    run_name = "readonly-audit-multi-year"
    export_id = "export-" + sha256(json_bytes(["extraction-export-1.0", run_name, *[f.sha256 for f in files]])).hexdigest()
    manifest = ExportManifest(export_id=export_id, run_id=run_name, engine_version=bundle["engine_version"],
                              engine_identity=bundle["engine_identity"], document=document,
                              clause_targets={clause.clause_key: clause.clause_digest}, execution_state="completed",
                              sealed_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), files=files)
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest.model_dump(mode="json")))
        for role, value in members.items():
            archive.writestr(role + ".json", json_bytes(value))
    decoded = decode_archive(stream.getvalue())
    receipt = get(f"http://127.0.0.1:18280/v1/ingestion/receipts/{EXPORT_ID}")
    captured = get(f"http://127.0.0.1:18280/v1/ingestion/exports/{EXPORT_ID}/facts")
    references = {
        "single_year_selector": reference("ExtractESG/extraction/src/esg_extraction/pipeline/structure.py", "years == {document.reporting_year}"),
        "engine_override": reference("ExtractESG/extraction/src/esg_extraction/pipeline/engine.py", 'update={"columns": decided_columns}'),
        "annual_scope_current_year": reference("ExtractESG/extraction/src/esg_extraction/pipeline/binding.py", "period.value != str(document.reporting_year)"),
        "report_year_inheritance": reference("ExtractESG/extraction/src/esg_extraction/pipeline/binding.py", 'state="inherited"'),
        "period_evidence_loss": reference("ExtractESG/document/src/esg_document/document/evidence_service.py", "reporting_period_raw=str(period.year)"),
        "object_top_k": reference("ExtractESG/document/src/esg_document/document/evidence_service.py", ": intent.limit"),
        "enrichment_request": reference("ClauseESG/src/esg_clauses/contract.py", "requested = self.purposes.enrichment.identity_axes"),
        "blocked_selection": reference("ClauseESG/src/esg_clauses/repository.py", "Blocked clause contracts"),
        "batch_metadata": reference("ExtractESG/contracts/src/esg_reports/contract.py", "class BatchItem"),
        "receiver_decoder": reference("database/src/esg_database/ingestion.py", "def decode_archive"),
        "raw_storage": reference("database/migrations/0019_capture_ingestion.sql", "CREATE TABLE esg.captured_facts"),
        "capture_query_limit": reference("database/runtime/worker.mjs", "LIMIT 1000"),
        "capture_boot_legacy_dictionary": reference("database/runtime/worker.mjs", "dictionary_membership"),
        "init_legacy_dictionary": reference("database/runtime/manage.mjs", "await seedMetricDictionary"),
        "parquet_all_strings": reference("database/queries/scientific_products.mjs", "pa.field(k,pa.string())"),
        "publication_legacy_sources": reference("database/queries/service.mjs", "const data = await snapshot(db, params.dataset_version)"),
        "retired_import": reference("PlatESG/backend/main.py", 'code\":\"legacy_production_retired\",\"message\":\"旧标准包/Core 产物入口'),
        "console_routes": reference("ConsoleESG/frontend/src/app/App.tsx", 'const nav = ['),
        "plat_pdf_roots": reference("PlatESG/backend/main.py", 'roots=[Path(value) for value in config.get("PLATESG_PUBLIC_PDF_ROOTS"'),
        "structural_supplement": reference("ExtractESG/extraction/src/esg_extraction/pipeline/engine.py", 'not row.unit_raw or not row.unit_sources'),
        "fact_identity": reference("ExtractESG/extraction/src/esg_extraction/pipeline/binding.py", '"interpretation": interpretation_id'),
    }
    assert len(blocked) == 37 and len(expected_air) == 9 and len(actual_air) == 3
    assert len(facts) == 51 and len(decoded["facts"]) == 51 and not decoded["rejected"]
    return {
        "schema_version": "multi-year-audit-1.0", "observed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "source + frozen artifacts + physical PDF rendering + in-memory probes + loopback GET; no model/run/db mutation",
        "inventory": {"clauses": len(clauses), "kinds": dict(Counter(c["data_kind"] for c in clauses)),
                      "readiness": dict(Counter(c["readiness"] for c in clauses)),
                      "curation": dict(Counter(c["organization"]["curation"] for c in clauses)),
                      "blocking_reasons": dict(Counter(r for c in blocked for r in c["blocking_reasons"])),
                      "enrichment_axis_gaps": gaps, "enrichment_axis_gap_clause_count": len(gaps)},
        "blocked": blocked,
        "case": {"run_id": RUN_ID, "export_id": EXPORT_ID, "source_sha256": document.source_sha256,
                 "source_physical_page": frame.page_index + 1, "printed_pages": [141, 142],
                 "columns": frame.columns, "current_structural_selection": period_columns(frame, document),
                 "saved_fact_count": len(bundle["facts"]),
                 "saved_periods": dict(Counter(str(f["identity"]["reporting_period"]["value"]) for f in bundle["facts"])),
                 "expected_air": expected_air, "saved_air": actual_air,
                 "bounded_air_retention": {"expected": 9, "retained": 3, "missing": 6,
                                           "note": "This manually checked three-row table only; no report-wide recall metric."},
                 "all_table_bindability_probe": {"facts": len(facts), "periods": dict(Counter(str(f.identity.reporting_period.value) for f in facts)),
                                                  "rejected_cells": rejected, "not_fresh_extraction": True},
                 "search_scope": document.search_scope,
                 "receipt": {k: receipt.get(k) for k in ("status", "accepted_facts", "rejected_facts", "publication_status")},
                 "receiver_readback": {"returned": len(captured.get("items", [])),
                                       "periods": dict(Counter(str(row["record"]["identity"]["reporting_period"]["value"]) for row in captured.get("items", []))),
                                       "observation_error": captured.get("observation_error")}},
        "probes": {"annual_scope": scope_probe,
                   "no_local_period": {"constructed_counterexample": True, "input_column": "数值", "document_year": document.reporting_year,
                                       "output": context.reporting_period.model_dump(mode="json")},
                   "in_memory_multi_year_archive": {"accepted_by_decoder": len(decoded["facts"]), "rejected": len(decoded["rejected"]),
                                                     "persisted": False, "database_receipt_created": False}},
        "services": {name: {k: body.get(k) for k in ("status", "service", "dataset_version", "restart_required", "auto_publish") if k in body}
                     for name, body in (("document", get("http://127.0.0.1:18080/health")),
                                        ("extraction", get("http://127.0.0.1:18180/health")),
                                        ("receiver", get("http://127.0.0.1:18280/health")),
                                        ("plat", get("http://127.0.0.1:8765/health")))},
        "references": references,
    }


if __name__ == "__main__":
    result = collect()
    output = Path(__file__).with_name("evidence.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(ROOT)), "clauses": result["inventory"]["clauses"],
                      "blocked": len(result["blocked"]), "three_year_air": result["case"]["bounded_air_retention"],
                      "bindable": result["case"]["all_table_bindability_probe"]["facts"]}, ensure_ascii=False))
