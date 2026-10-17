"""Read historical call artifacts through current contracts; never run a model.

Only counts, hashes and issue categories are exported. Original prompts, report
values and responses stay in the supplied private artifact directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from esg_targeted.models.report_semantics import (
    AgentPlan, parse_model_json, validate_mapping_payload, validate_understanding_payload,
)


def replay(directory: Path) -> dict:
    rows, totals, previous = [], Counter(), None
    for path in sorted(directory.glob("*.json")):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        context, tool = artifact.get("input", {}), artifact.get("tool", "unknown")
        task = context.get("task", tool) if tool == "repair_format" else tool
        if tool == "repair_format":
            context = context.get("original_task") or (previous or {}).get("input", {})
        else:
            previous = artifact
        raw = artifact.get("raw_output", "")
        issue_stages, retained, categories = Counter(), 0, Counter()
        if artifact.get("finish_reason") == "length" or artifact.get("failure_category") == "generation_incomplete":
            status = "generation_incomplete"
        elif not raw:
            status = "no_model_output"
        else:
            try:
                if task == "metric_mapping":
                    result, issues = validate_mapping_payload(raw, context)
                    retained = len(result.matches)
                elif task in {"source_understanding", "read_object_context"}:
                    result, issues = validate_understanding_payload(raw, context)
                    retained = len(result.rows)
                elif task == "agent_plan":
                    result = parse_model_json(raw, AgentPlan)
                    ids = {o["object_id"] for o in [*context.get("candidate_objects", []), *context.get("understood_objects", [])]}
                    retained = sum(action.tool in {"finish", "search_report"} or action.object_id in ids for action in result.actions)
                    issues = []
                    if retained != len(result.actions):
                        issue_stages["reference"] += len(result.actions) - retained
                else:
                    result, issues = None, []
                issue_stages.update(issue.stage for issue in issues)
                status = "isolated_invalid_parts" if issue_stages else "protocol_passed"
            except (ValueError, TypeError, KeyError) as exc:
                status = "protocol_rejected"
                categories[type(exc).__name__] += 1
        totals[status] += 1
        rows.append({"artifact": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "tool": tool, "task": task, "generation_phase": artifact.get("phase"),
                     "status": status, "retained_protocol_items": retained,
                     "issue_stages": dict(issue_stages), "exception_categories": dict(categories)})
    return {"schema_version": "offline-protocol-replay-v1", "mode": "read_only_no_model",
            "limitations": ["Protocol acceptance does not establish evidence or metric correctness.",
                            "Old responses were not regenerated using the new prompt/schema.",
                            "Retained protocol items are not approved report facts."],
            "call_count": len(rows), "status_counts": dict(totals), "calls": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calls", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.calls.resolve()):
        parser.error("Replay output must be outside the original call directory")
    result = replay(args.calls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"call_count": result["call_count"], "status_counts": result["status_counts"]}, ensure_ascii=False))
