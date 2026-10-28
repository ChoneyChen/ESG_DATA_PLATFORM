"""Reproduce Phase 0 measurements from retained evidence; no model or production DB.

Example (from the repository root):
  python3 scripts/phase0_baseline.py replay \
    --lab /path/to/foundation-lab --output .local/phase0-baseline

The lab holds large real IRs and the independently checked legacy gold files.
Its paths are inputs, not production storage locations. See docs/PHASE0_BASELINE.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import sys
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REPORTS = ("longi", "catl", "cmobile", "tencent", "zijin", "boc", "pingan", "report00004", "alibaba")
FIELD_NAMES = ("value", "period", "subject", "boundary", "unit", "dimension")
POLLUTANTS = {
    "NOx": "pollution.nox.air", "VOCs": "pollution.voc.air", "COD": "pollution.cod.water",
    "SOx": "pollution.sox.air", "NH3-N": "pollution.ammonia.water",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def numeric(value):
    try:
        return Decimal(str(value).replace(",", "").replace("%", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def legacy_rows(gold: dict, result: dict, report: str):
    conclusions = {item["code"]: item for item in result["conclusions"]}
    for code, expected in gold.items():
        conclusion = conclusions[code]
        members = {member.get("member"): member for member in conclusion.get("members") or []}
        if expected is None:
            found = conclusion["status"] in {"found", "review", "conflict"}
            yield {"case_id": f"legacy:{report}:{code}", "report": report, "code": code,
                   "expected": None, "outcome": "false_positive" if found else "correct",
                   "coverage": "legacy_value_and_absence", "status": conclusion["status"]}
        elif expected == "present":
            found = conclusion["status"] in {"found", "review", "conflict"}
            yield {"case_id": f"legacy:{report}:{code}", "report": report, "code": code,
                   "expected": expected, "outcome": "correct" if found else "missed",
                   "coverage": "legacy_value_and_absence", "status": conclusion["status"]}
        else:
            expected_members = expected if isinstance(expected, dict) else {None: expected}
            for member, value in expected_members.items():
                actual = members.get(member)
                outcome = "missed" if not actual else "correct" if numeric(actual["value_raw"]) == numeric(value) else "wrong"
                yield {"case_id": f"legacy:{report}:{code}:{member or 'total'}", "report": report,
                       "code": code, "member": member, "expected": value,
                       "actual": actual["value_raw"] if actual else None, "outcome": outcome,
                       "coverage": "legacy_value_and_absence",
                       "status": actual["status"] if actual else conclusion["status"]}


def source_rows(result: dict):
    def flatten(code, member, row, *, outer=None):
        source = row.get("source") or row
        source = {"period_year": (outer or {}).get("period_year"), "entity_basis": (outer or {}).get("entity_basis"), **source}
        return {"code": code, "member": member, "status": row["status"], "source": source, "derived": False}
    for conclusion in result["conclusions"]:
        for member in conclusion.get("members") or []:
            yield flatten(conclusion["code"], member.get("member"), member, outer=member)
        for candidate in conclusion.get("candidates") or []:
            yield flatten(conclusion["code"], candidate.get("member"), candidate)
        for section in ("agreeing_sources", "conflicting_sources", "related_subset_sources", "component_sources"):
            for member in conclusion.get("members") or []:
                for source in member.get(section) or []:
                    yield {**flatten(conclusion["code"], member.get("member"), source),
                           "derived": section == "component_sources"}


def neutral_rows(facts: list[dict], metrics: dict[str, dict]):
    for fact in facts:
        identity = fact["identity_raw"]
        for code in metrics:
            if code.startswith(("E2-4_02", "E2-4_03")):
                member = identity.get("pollutant")
                if not member:
                    continue
            yield {"code": code, "member": identity.get("pollutant"),
                   "status": "accepted", "derived": False, "source": {
                       "page_number": fact["physical_sources"][0]["pdf_page_index"] + 1,
                       "value_raw": fact["value_raw"], "unit_raw": fact["unit_raw"],
                       "period_year": int(identity["reporting_period"][:4]) if identity.get("reporting_period", "")[:4].isdigit() else None,
                       "entity_basis": identity.get("entity_basis"),
                       "reporting_boundary": identity.get("reporting_boundary"),
                       "attributes": {"boundary_label": identity.get("reporting_boundary")}}}


def neutral_baseline(gold: dict, facts: list[dict], metrics: dict[str, dict]):
    return full_field_rows(gold, {"conclusions": [{"code": code, "status": "found", "members": []} for code in metrics]},
                          actual_rows=list(neutral_rows(facts, metrics)))


def full_field_rows(gold: dict, result: dict, actual_rows: list[dict] | None = None):
    actual = actual_rows if actual_rows is not None else list(source_rows(result))
    for fact in gold["facts"]:
        expected_year = int(fact["column_year"])
        dimension = next((key for key, concept in POLLUTANTS.items() if fact["source_concept"] == concept
                          or fact["source_concept"].startswith(concept + ".")), None)
        matches = [row for row in actual if row["code"] in fact["allowed_standard_datapoint_ids"]
                   and row["source"]["page_number"] == fact["evidence"]["pdf_page_number"]
                   and numeric(row["source"]["value_raw"]) == numeric(fact["raw_value"])
                   and row["member"] == dimension and not row["derived"]]
        allowed = bool(fact["allowed_standard_datapoint_ids"])
        if not allowed:
            conflicts = [row for row in actual if row["code"] in fact["forbidden_standard_datapoint_ids"]
                         and row["source"]["page_number"] == fact["evidence"]["pdf_page_number"]
                         and numeric(row["source"]["value_raw"]) == numeric(fact["raw_value"]) and not row["derived"]]
            accepted = [row for row in conflicts if row["status"] != "rejected"]
            rejected = [row for row in conflicts if row["status"] == "rejected"]
            outcome = "semantic_failure" if accepted else "rejected" if rejected else "not_covered"
        else:
            matches = [row for row in matches if row["source"].get("period_year") == expected_year]
            outcome = "matched" if matches else "missed"
        fields = {name: {"expected": None, "actual": None, "correct": None} for name in FIELD_NAMES}
        if allowed and matches:
            actual_source = matches[0]["source"]
            fields.update({
                "value": {"expected": fact["raw_value"], "actual": actual_source["value_raw"], "correct": True},
                "period": {"expected": expected_year, "actual": actual_source["period_year"], "correct": True},
                "unit": {"expected": fact["raw_unit"], "actual": actual_source["unit_raw"],
                         "correct": actual_source["unit_raw"] == fact["raw_unit"]},
                "dimension": {"expected": dimension, "actual": matches[0]["member"], "correct": True},
            })
            boundary = actual_source.get("attributes", {}).get("boundary_label")
            if fact["measurement_boundary"] == "company_report_scope":
                entity = actual_source.get("entity_basis") or actual_source.get("attributes", {}).get("entity_basis")
                fields["boundary"] = {"expected": "group", "actual": entity,
                                      "correct": entity == "group" if entity in {"group", "subset"} else None}
            if fact["source_concept"] == "pollution.voc.air.refining":
                fields["boundary"] = {"expected": "炼化", "actual": boundary or actual_source.get("reporting_boundary"),
                                      "correct": (boundary or actual_source.get("reporting_boundary")) in {"refining", "炼化"}}
        elif not allowed and outcome == "rejected":
            fields["value"] = {"expected": "rejected_source_value", "actual": rejected[0]["source"]["value_raw"], "correct": True}
        yield {"case_id": fact["gold_fact_id"], "report": "cnpc", "code": fact["allowed_standard_datapoint_ids"],
               "allowed_codes": bool(fact["allowed_standard_datapoint_ids"]),
               "forbidden_codes": fact["forbidden_standard_datapoint_ids"], "expected": fact["raw_value"],
               "period": expected_year, "concept": fact["source_concept"], "boundary": fact["report_boundary"],
               "outcome": outcome, "fields": fields,
               "coverage": "independent_pdf_cell_gold", "status": matches[0]["status"] if matches else outcome}


def summarize(rows: list[dict]):
    outcomes = dict(Counter(row["outcome"] for row in rows))
    legacy = [row for row in rows if row["coverage"] == "legacy_value_and_absence"]
    positive = [row for row in legacy if row["expected"] not in (None, "present")]
    negative = [row for row in legacy if row["expected"] is None]
    metrics = {
        "legacy_expected_value_recall": {"correct": sum(row["outcome"] == "correct" for row in positive), "total": len(positive)},
        "legacy_presence_recall": {"correct": sum(row["outcome"] == "correct" for row in legacy if row["expected"] is not None),
                                    "total": sum(row["expected"] is not None for row in legacy)},
        "legacy_negative_specificity": {"correct": sum(row["outcome"] == "correct" for row in negative), "total": len(negative)},
        "legacy_correct": {"correct": outcomes.get("correct", 0), "total": len(legacy)},
    }
    source_facts = [row for row in rows if row["coverage"] == "independent_pdf_cell_gold" and row["code"]]
    current_source_facts = [row for row in source_facts if row.get("period") == 2025]
    metrics.update({
        "current_period_source_recall": {"correct": sum(row["outcome"] == "matched" for row in current_source_facts),
                                         "total": len(current_source_facts)},
        "historical_source_retention": {"correct": sum(row["outcome"] == "matched" for row in source_facts
                                                      if row.get("period") != 2025),
                                        "total": sum(row.get("period") != 2025 for row in source_facts)},
        "dimension_retention": {"correct": sum(row.get("outcome") == "matched" for row in source_facts), "total": len(source_facts)},
    })
    fields = {}
    for name in FIELD_NAMES:
        applicable = [row["fields"][name] for row in rows if row.get("fields", {}).get(name, {}).get("expected") is not None]
        fields[name] = {"correct": sum(field["correct"] is True for field in applicable),
                        "wrong": sum(field["correct"] is False for field in applicable),
                        "unmeasured": sum(field["correct"] is None for field in applicable), "total": len(applicable)}
    return {"outcomes": outcomes, "metrics": metrics, "field_correctness": fields,
            "note": "not_covered is not a correct semantic rejection; unmatched report-level subject remains unmeasured"}


def replay(args):
    from esg_targeted.foundation.engine import read_calls
    from esg_targeted.foundation.runner import run

    lab = args.lab.resolve()
    output = args.output.resolve()
    if not lab.is_dir():
        raise SystemExit(f"Lab not found: {lab}")
    ir_map = load(lab / "gold-ir-map.json")
    rows, cases = [], []
    for name in LEGACY_REPORTS:
        calls = lab / "runs" / f"{name}-r2" / "calls.jsonl"
        ir = lab / "tl-ir" / ir_map[name]
        if not calls.is_file() or not ir.is_dir():
            cases.append({"case": name, "status": "blocked", "reason": "retained calls or IR missing"})
            continue
        started = time.perf_counter()
        result = run(ir, output / "reports" / name, replay=calls)
        gold = load(lab / "gold" / f"{name}.json")
        scored = list(legacy_rows(gold, result, name))
        rows.extend(scored)
        cases.append({"case": name, "status": "replayed", "pdf": load(lab / "reports.json")[name],
                      "ir_run_id": result["ir_run_id"], "ir_revision": result["ir_revision"],
                      "model_calls": result["model_calls"], "reused_answers": result["reused_answers"],
                      "prepare_seconds": result["prepare_seconds"], "wall_seconds": round(time.perf_counter() - started, 3),
                      "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                      "outcomes": dict(Counter(row["outcome"] for row in scored))})
    if args.cnpc_ir and args.cnpc_calls:
        ir, calls = args.cnpc_ir.resolve(), args.cnpc_calls.resolve()
        result = run(ir, output / "reports" / "cnpc", replay=calls)
        gold = load(ROOT / "ExtractESG/tests/fixtures/business/cnpc-2025-esrs-major-gold.v1.json")
        rows.extend(full_field_rows(gold, result))
        cases.append({"case": "cnpc-full-field", "status": "replayed", "ir_run_id": result["ir_run_id"],
                      "ir_revision": result["ir_revision"], "reused_answers": result["reused_answers"]})
    else:
        cases.append({"case": "cnpc-full-field", "status": "blocked",
                      "reason": "supply --cnpc-ir and --cnpc-calls for the independently audited PDF; no IR is inferred from gold"})
    summary = {"schema_version": "phase0-baseline-v1", "mode": "retained-evidence-replay",
               "models_called": False, "production_database_used": False,
               "cases": cases, "summary": summarize(rows), "resources": resources(time.perf_counter())}
    dump(output / "baseline.json", summary)
    with (output / "cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("case_id", "report", "code", "expected", "actual", "outcome", "coverage", "status"))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    dump(output / "full-field-cases.json", [row for row in rows if row.get("fields")])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def resources(started):
    return {"wall_seconds": round(time.perf_counter() - started, 3),
            "process_peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3, 3),
            "note": "process peak includes IR replay; MLX peak is recorded per live model call"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--lab", type=Path, required=True)
    replay_parser.add_argument("--output", type=Path, required=True)
    replay_parser.add_argument("--cnpc-ir", type=Path)
    replay_parser.add_argument("--cnpc-calls", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("ESG_ALLOW_CLOUD_OCR", "false")
    replay(args)


if __name__ == "__main__":
    main()
