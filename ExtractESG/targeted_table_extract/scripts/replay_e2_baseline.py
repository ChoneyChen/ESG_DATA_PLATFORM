"""Read-only replay of the pinned E2-4 model outputs; never calls a model.

Run from backend with PYTHONPATH=src python ../scripts/replay_e2_baseline.py.
The original result bundles must remain present. No hashes or rewritten bundles
are needed: compare the actual accepted field values and evidence references.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from esg_targeted.contracts import EvidencePacket, SemanticDecision
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.models.response_adapter import DirectFillResponseAdapter


BASELINE_COUNTS = {
    "tx-20260903T115404Z-3bbe54e59a": 12,
    "tx-20260903T115410Z-71d4efdbf7": 12,
    "tx-20260903T123239Z-fd2fffbf63": 106,
    "tx-20260903T123243Z-804e0bcf82": 106,
}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _signature(decision):
    return sorted(
        tuple(sorted((item.element_id, item.value_raw, tuple(item.evidence_span_ids))
                     for item in group.assignments))
        for group in decision.fact_groups
    )


def replay(root: Path) -> list[dict]:
    reports = []
    for job, expected_count in BASELINE_COUNTS.items():
        bundle = root / job
        observation_count = len((bundle / "results/quantitative-observations.jsonl").read_text().splitlines())
        if observation_count != expected_count:
            raise AssertionError(f"{job}: baseline observation count changed")
        replay_count = 0
        for path in sorted((bundle / "decisions").glob("*.json")):
            region_path = bundle / "packets" / f"{path.stem}.regions.json"
            if not region_path.is_file():
                continue  # A local no-evidence decision has no model regions.
            regions = {item["packet_id"]: EvidencePacket.model_validate(item)
                       for item in _read(region_path)["regions"]}
            for attempt in _read(path)["attempts"]:
                region_id = attempt.get("region_packet_id")
                if not attempt.get("guard", {}).get("accepted") or region_id not in regions:
                    continue
                packet = regions[region_id]
                current, _, _ = DirectFillResponseAdapter().parse_with_diagnostics(
                    attempt["raw_output"], packet=packet, visual_used=attempt.get("visual_used", False),
                )
                previous = SemanticDecision.model_validate(attempt["decision"])
                guard = GroundingGuard().validate(packet, current)
                if not guard.accepted:
                    raise AssertionError(f"{job}/{path.stem}: {guard.feedback}")
                if current.status != previous.status or _signature(current) != _signature(previous):
                    raise AssertionError(f"{job}/{path.stem}: accepted values/evidence changed")
                replay_count += 1
        if not replay_count:
            raise AssertionError(f"{job}: no accepted model output was replayed")
        reports.append({"job": job, "observations": observation_count,
                        "accepted_outputs_replayed": replay_count, "unchanged": True})
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "targeted_extract_output")
    args = parser.parse_args()
    print(json.dumps(replay(args.bundle_root), ensure_ascii=False, indent=2))
