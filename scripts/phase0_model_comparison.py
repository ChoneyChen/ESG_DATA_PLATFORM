"""Same immutable table, prompt and binding contract for local Qwen/NuExtract.

Run with the local model virtualenv. The extraction production provider is not
changed. Models run sequentially and their peak memory is not additive.
"""
from __future__ import annotations

import argparse
import csv
import json
import resource
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def actual_rows(result):
    def flatten(code, member, row):
        source = row.get("source") or row
        return code, member, {**source, "member": member, "status": row["status"]}
    for conclusion in result["conclusions"]:
        for member in conclusion.get("members") or []:
            yield flatten(conclusion["code"], member.get("member"), member)
        for candidate in conclusion.get("candidates") or []:
            yield flatten(conclusion["code"], candidate.get("member"), candidate)
        for member in conclusion.get("members") or []:
            for section in ("agreeing_sources", "conflicting_sources", "related_subset_sources", "component_sources"):
                for candidate in member.get(section) or []:
                    yield flatten(conclusion["code"], candidate.get("member"), candidate)


def numeric(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def score(case, result, source_rows=None):
    rows = list(source_rows) if source_rows is not None else list(actual_rows(result))
    scored = []
    for expected in case["facts"]:
        candidates = [(code, member, row) for code, member, row in rows
                      if code == expected["code"] and numeric(row.get("value_raw")) == numeric(expected["value"])]
        if source_rows is not None:
            candidates = [row for row in candidates if row[2].get("period_year") == int(expected["period"])
                          and row[2].get("row_id") == expected["row"]]
        row = next((row for row in candidates if row[1] == expected["member"]), None)
        fields = {"recall": bool(row)}
        if row:
            source = row[2]
            fields.update(value=numeric(source["value_raw"]) == numeric(expected["value"]),
                          period=source.get("period_year") == int(expected["period"]),
                          unit=source.get("unit_raw") == expected["unit"],
                          dimension=row[1] == expected["member"],
                          boundary=(None if source.get("entity_basis") in (None, "unknown")
                                    else source.get("entity_basis") == expected["boundary"]))
        else:
            fields.update(value=False, period=False, unit=False, dimension=False, boundary=False)
        scored.append({"case_id": case["case_id"], "code": expected["code"], "member": expected["member"],
                       "row": expected["row"], "expected": expected["value"],
                       "actual": row[2]["value_raw"] if row else None,
                       "status": row[2]["status"] if row else "missed", **fields})
    for negative in case["negative_rows"]:
        candidates = [(code, row) for code, _, row in rows if code == negative["code"] and row.get("row_id") == negative["row"]]
        rejected = [row for _, row in candidates if row["status"] == "rejected"]
        accepted = [row for _, row in candidates if row["status"] != "rejected"]
        scored.append({"case_id": case["case_id"], "code": negative["code"], "row": negative["row"],
                       "expected": f"reject:{negative['reason']}",
                       "actual": rejected[0].get("rejected_by") if rejected else accepted[0]["status"] if accepted else None,
                       "outcome": "false_positive" if accepted else "rejected" if rejected else "not_covered"})
    return scored


def metrics(rows):
    positive = [row for row in rows if "recall" in row]
    negative = [row for row in rows if "outcome" in row]
    field_metrics = {key: {"correct": sum(row[key] is True for row in positive),
                           "unmeasured": sum(row[key] is None for row in positive), "total": len(positive)}
                     for key in ("recall", "value", "period", "unit", "dimension", "boundary")}
    field_metrics["negative_rejection"] = {"correct": sum(row["outcome"] == "rejected" for row in negative), "total": len(negative)}
    field_metrics["negative_not_covered"] = {"correct": sum(row["outcome"] == "not_covered" for row in negative), "total": len(negative)}
    field_metrics["negative_false_positive"] = {"correct": sum(row["outcome"] == "false_positive" for row in negative), "total": len(negative)}
    return field_metrics


def resources(started):
    return {"wall_seconds": round(time.perf_counter() - started, 3),
            "process_peak_rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3, 3)}


def main():
    from esg_targeted.foundation.engine import FoundationEngine
    from esg_targeted.foundation.kernel import build_request, request_digest, run_task

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=ROOT / "ExtractESG/tests/fixtures/business/phase0-model-comparison-gold.v1.json")
    parser.add_argument("--providers", nargs="+", choices=("local_qwen", "local_nuextract"), default=["local_qwen", "local_nuextract"])
    parser.add_argument("--model-root", type=Path, default=Path.home() / "Desktop/model")
    parser.add_argument("--reuse", action="store_true", help="Replay existing responses in --output without model calls")
    args = parser.parse_args()
    if args.output.resolve() == args.lab.resolve() or args.lab.resolve() in args.output.resolve().parents:
        raise SystemExit("Model output must not be written into the input lab")
    cases = load(args.gold)["cases"]
    results, rows = {}, []
    for provider in args.providers:
        started = time.perf_counter()
        records = []
        result_path = args.output / provider / "result.json"
        if args.reuse:
            saved = load(result_path)
            replayed = []
            for case in cases:
                record = next(record for record in saved["records"] if record["case_id"] == case["case_id"])
                if record.get("raw_output"):
                    engine = FoundationEngine.from_path(args.lab / "tl-ir" / case["ir_run_id"], only_tables=[case["table_id"]])
                    class ReplayModel:
                        def generate_structured(self, **kwargs):
                            return record["raw_output"], {key: record.get(key) for key in (
                                "input_tokens", "generation_tps", "prompt_tps", "peak_memory_gb", "model_id")}
                    replayed_record = run_task(ReplayModel(), engine.tasks[0], engine.period.year)
                    replayed_record.update({key: value for key, value in record.items() if key in (
                        "case_id", "provider", "request", "output_tokens", "model_load_seconds", "generation_seconds",
                        "elapsed_seconds", "input_tokens", "prompt_tps", "generation_tps", "peak_memory_gb", "model_id")})
                    record = replayed_record
                replayed.append(record)
            result = {**saved, "records": replayed, "resources": saved.get("resources") or result.get("resources"),
                      "evaluation_basis": "unchanged_raw_model_answers_rebound_with_shared_response_contract" if args.reuse else "live_local_model"}
            dump(result_path, result)
        else:
            if provider == "local_qwen":
                from esg_targeted.models.qwen import QwenMlxModel
                model = QwenMlxModel(args.model_root / "Qwen3.5-9B-4bit", max_tokens=4096, timeout_seconds=300)
            else:
                from esg_targeted.models.nuextract import NuExtractMlxModel
                model = NuExtractMlxModel(args.model_root / "NuExtract3-mlx-4bits", max_tokens=4096, timeout_seconds=300)
            try:
                for case in cases:
                    engine = FoundationEngine.from_path(args.lab / "tl-ir" / case["ir_run_id"], only_tables=[case["table_id"]])
                    if len(engine.tasks) != 1:
                        raise RuntimeError(f"Expected one immutable task for {case['case_id']}, got {len(engine.tasks)}")
                    task = engine.tasks[0]
                    instructions, context, schema, _ = build_request(task, engine.period.year)
                    record = run_task(model, task, engine.period.year)
                    record.update(case_id=case["case_id"], provider=provider,
                                  request={"instructions": instructions, "context": context, "schema": schema})
                    if record.get("request_sha") != request_digest(instructions, context, schema):
                        raise RuntimeError("Comparison input identity changed")
                    records.append(record)
                    print(provider, case["case_id"], record["status"], record.get("elapsed_seconds"), flush=True)
            finally:
                model.close()
            result = {"schema_version": "phase0-model-comparison-v1", "provider": provider,
                      "production_provider_changed": False, "records": records,
                      "resources": resources(started)}
            dump(result_path, result)
        results[provider] = result
        scored = []
        for case in cases:
            record = next(record for record in result["records"] if record["case_id"] == case["case_id"])
            engine = FoundationEngine.from_path(args.lab / "tl-ir" / case["ir_run_id"], only_tables=[case["table_id"]])
            conclusion = engine.conclude([record], elapsed_seconds=record.get("elapsed_seconds") or 0)
            sources = [(row["code"], row.get("member"), row) for row in record.get("observations") or []]
            scored.extend(score(case, conclusion, sources))
        rows.extend(scored)
        result["metrics"] = metrics(scored)
        dump(result_path, result)
    summary = {"schema_version": "phase0-model-comparison-summary-v1",
               "gold_schema_version": load(args.gold)["schema_version"], "case_count": len(cases),
               "production_provider_changed": False, "providers": {
                   provider: {"metrics": result["metrics"], "resources": result["resources"],
                              "failed_calls": [r["task_id"] for r in result["records"] if r["status"] != "ok"],
                              "elapsed_seconds": sum(r.get("elapsed_seconds") or 0 for r in result["records"]),
                              "peak_model_memory_gb": max((r.get("peak_memory_gb") or 0 for r in result["records"]), default=0),
                              "model_load_seconds": sum(r.get("model_load_seconds") or 0 for r in result["records"]),
                              "input_tokens": sum(r.get("input_tokens") or 0 for r in result["records"]),
                              "output_tokens": sum(r.get("output_tokens") or 0 for r in result["records"])}
                   for provider, result in results.items()}}
    dump(args.output / "comparison.json", summary)
    dump(args.output / "cases.json", rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
