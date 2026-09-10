from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from esg_targeted.config import Settings
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.evidence.visual_inputs import FocusedVisualInputBuilder
from esg_targeted.evidence.visual_route import VisualEscalationPlanner
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.nuextract import NuExtractMlxModel
from esg_targeted.models.preflight import PacketPreflightGuard
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.models.template import DirectFillTemplateCompiler
from esg_targeted.retrieval.embeddings import QwenMlxEmbeddingProvider
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.retrieval.sufficiency import EvidenceSufficiencyGate
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler


MAX_CALLS_PER_PROVIDER = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile and invoke exactly one real semantic-fill region."
    )
    parser.add_argument("--provider", choices=("local_nuextract", "qiniu_vlm"), required=True)
    parser.add_argument("--ir-root", type=Path, required=True)
    parser.add_argument("--metric", required=True, help="Source datapoint ID, for example E2-4_02")
    parser.add_argument("--region-index", type=int, default=1, help="One-based region index")
    parser.add_argument("--object-top-n", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--probe-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "diagnostics" / "semantic-probes",
    )
    return parser.parse_args()


class ProbeLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def count(self, provider: str) -> int:
        if not self.path.is_file():
            return 0
        count = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            count += int(
                item.get("event") == "model_request_started"
                and item.get("provider") == provider
            )
        return count

    def reserve(self, provider: str, probe_id: str, detail: dict[str, Any]) -> int:
        current = self.count(provider)
        if current >= MAX_CALLS_PER_PROVIDER:
            raise RuntimeError(
                f"probe call budget exhausted for {provider}: "
                f"{current}/{MAX_CALLS_PER_PROVIDER}"
            )
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": "model_request_started",
            "provider": provider,
            "ordinal": current + 1,
            "limit": MAX_CALLS_PER_PROVIDER,
            "probe_id": probe_id,
            **detail,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return current + 1


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def execution_profile(provider: str, settings: Settings) -> dict[str, int]:
    if provider == "qiniu_vlm":
        return {
            "packet_max_chars": settings.cloud_packet_max_chars,
            "selection_max_spans": 6000,
            "selection_max_groups": 512,
            "selection_max_candidates": 12000,
            "selection_max_images": 8,
            "region_max_groups": min(24, settings.cloud_packet_max_groups),
            "region_max_chars": settings.cloud_packet_max_chars - 18_000,
            "max_regions": 64,
            "region_max_output_rows": 24,
            "region_max_span_chars": 8_000,
            "region_max_images": 1,
        }
    return {
        "packet_max_chars": settings.packet_max_chars,
        "selection_max_spans": 2400,
        "selection_max_groups": 256,
        "selection_max_candidates": 4800,
        "selection_max_images": 32,
        "region_max_groups": 8,
        "region_max_chars": min(10_000, settings.packet_max_chars - 4_000),
        "max_regions": 64,
        "region_max_output_rows": 8,
        "region_max_span_chars": 900,
        "region_max_images": 1,
    }


def build_regions(args: argparse.Namespace, settings: Settings, output_dir: Path):
    package = StandardPackageCatalog(settings.standard_dist_root).load(
        "esrs.2023-set1.e2-4", "1.0.0"
    )
    metric = next(
        (item for item in package.metrics if item.source_datapoint_id == args.metric),
        None,
    )
    if metric is None:
        raise ValueError(f"metric not found: {args.metric}")
    ir = DocumentIrLoader().load(args.ir_root)
    inventory = EvidenceInventoryBuilder().build(ir)
    query = MetricQueryCompiler().compile(package, metric)
    matrix = None
    query_vector = None
    if not args.lexical_only:
        cache_dir = settings.index_cache_root / inventory.inventory_id
        matrix_path = cache_dir / "embeddings.npy"
        if not matrix_path.is_file():
            raise FileNotFoundError(
                f"semantic index missing for probe; run a normal retrieval first: {matrix_path}"
            )
        matrix = np.load(matrix_path)
        if matrix.shape[0] != len(inventory.spans):
            raise ValueError(
                f"semantic index/span mismatch: {matrix.shape[0]} != {len(inventory.spans)}"
            )
        embedding = QwenMlxEmbeddingProvider(settings.embedding_model_path)
        try:
            query_vector = embedding.embed_query(query.semantic_text)
        finally:
            embedding.close()

    hits = HybridRetriever(inventory, embedding_matrix=matrix).search(
        query,
        max(settings.retrieval_top_k, args.object_top_n * 12, 36),
        query_vector=query_vector,
    )
    elements_by_id = {item.element_id: item for item in package.elements}
    elements = [
        elements_by_id[item]
        for item in package.elements_by_metric[metric.metric_id]
    ]
    profile = execution_profile(args.provider, settings)
    selection = EvidenceSelectionCompiler(
        max_spans=profile["selection_max_spans"],
        max_groups=profile["selection_max_groups"],
        max_candidates=profile["selection_max_candidates"],
        max_visual_artifacts=profile["selection_max_images"],
        max_retrieved_objects=args.object_top_n,
    ).compile(
        task_id=f"probe-{metric.metric_id.rsplit('.', 1)[-1]}",
        metric=metric,
        elements=elements,
        query=query,
        inventory=inventory,
        hits=hits,
        retrieval_complete=True,
    )
    sufficiency = EvidenceSufficiencyGate().assess(
        query, inventory, hits, retrieval_complete=True
    )
    route = VisualEscalationPlanner().plan(
        selection,
        sufficiency,
        visual_enabled=True,
        force_table_visual=True,
    )
    selection.budget["visual_route"] = route.as_dict()
    regions = EvidenceRegionCompiler(
        max_context_chars=profile["region_max_chars"],
        max_groups_per_region=profile["region_max_groups"],
        max_regions=profile["max_regions"],
        max_output_rows=profile["region_max_output_rows"],
        max_span_chars=profile["region_max_span_chars"],
        max_images_per_region=profile["region_max_images"],
    ).split(selection)
    regions = FocusedVisualInputBuilder(output_dir / "model-inputs").build_all(regions)
    return package, metric, ir, inventory, query, hits, sufficiency, selection, regions, profile


def build_model(
    provider: str,
    settings: Settings,
    ledger: ProbeLedger,
    probe_id: str,
    request_detail: dict[str, Any],
):
    if provider == "local_nuextract":
        ordinal = ledger.reserve(provider, probe_id, request_detail)
        return (
            NuExtractMlxModel(
                settings.nuextract_model_path,
                max_tokens=settings.model_max_tokens,
                max_input_chars=settings.packet_max_chars,
                max_input_tokens=settings.model_max_input_tokens,
                timeout_seconds=settings.model_timeout_seconds,
                min_output_tokens=settings.model_min_output_tokens,
            ),
            lambda: ordinal,
        )

    request_ordinals: list[int] = []

    def counted_requester(url, payload, headers, timeout):
        request_ordinals.append(
            ledger.reserve(provider, probe_id, request_detail)
        )
        return QiniuVlmModel._default_requester(url, payload, headers, timeout)

    return (
        QiniuVlmModel(
            api_key=settings.qiniu_api_key,
            base_url=settings.qiniu_base_url,
            model_id=settings.qiniu_model,
            timeout_seconds=settings.qiniu_timeout_seconds,
            max_output_tokens=settings.qiniu_max_output_tokens,
            max_image_bytes=settings.qiniu_max_image_bytes,
            requester=counted_requester,
        ),
        lambda: request_ordinals,
    )


def main() -> int:
    args = parse_args()
    settings = Settings.load()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    probe_id = (
        f"probe-{timestamp}-{args.provider}-{args.metric.lower()}-r{args.region_index}"
    )
    output_dir = args.probe_root.resolve() / probe_id
    output_dir.mkdir(parents=True, exist_ok=False)
    ledger = ProbeLedger(args.probe_root.resolve() / "ledger.jsonl")
    started = time.perf_counter()
    try:
        (
            package,
            metric,
            ir,
            inventory,
            query,
            hits,
            sufficiency,
            selection,
            regions,
            profile,
        ) = build_regions(args, settings, output_dir)
        if not regions:
            raise ValueError("retrieval produced no model regions")
        region_summaries = [
            {
                "index": index,
                "packet_id": item.packet_id,
                "region": item.budget.get("region"),
                "groups": len(item.allowed_group_ids),
                "spans": len(item.spans),
                "candidates": len(item.candidates),
                "images": len(item.page_image_paths),
                "context_chars": len(item.model_context),
                "focused_visual": item.budget.get("focused_visual"),
            }
            for index, item in enumerate(regions, 1)
        ]
        write_json(
            output_dir / "compile-summary.json",
            {
                "probe_id": probe_id,
                "provider": args.provider,
                "note": args.note,
                "ir_run_id": ir.run_id,
                "document_id": ir.document_id,
                "metric_id": metric.metric_id,
                "source_datapoint_id": metric.source_datapoint_id,
                "query": query.model_dump(mode="json"),
                "sufficiency": sufficiency.model_dump(mode="json"),
                "profile": profile,
                "selected_objects": selection.budget.get(
                    "selected_retrieval_objects", []
                ),
                "region_count": len(regions),
                "regions": region_summaries,
                "existing_call_count": ledger.count(args.provider),
            },
        )
        if args.region_index < 1 or args.region_index > len(regions):
            raise ValueError(
                f"region index out of range: {args.region_index}; available 1..{len(regions)}"
            )
        packet = regions[args.region_index - 1]
        preflight = PacketPreflightGuard(profile["packet_max_chars"]).validate(packet)
        template = DirectFillTemplateCompiler()
        write_json(output_dir / "packet.json", packet.model_dump(mode="json"))
        write_json(
            output_dir / "prompt-components.json",
            {
                "instructions": template.instructions(),
                "template": template.compile(packet),
                "model_context": json.loads(packet.model_context),
                "preflight": preflight.as_dict(),
            },
        )
        if args.dry_run:
            print(json.dumps({"probe_id": probe_id, "dry_run": True, "regions": region_summaries}, ensure_ascii=False))
            return 0

        request_detail = {
            "ir_run_id": ir.run_id,
            "metric": metric.source_datapoint_id,
            "region_index": args.region_index,
            "packet_id": packet.packet_id,
        }
        model, call_ordinals = build_model(
            args.provider, settings, ledger, probe_id, request_detail
        )
        use_visual = bool(packet.page_image_paths)
        try:
            result = model.decide(packet, use_visual=use_visual)
            guard = GroundingGuard().validate(packet, result.decision)
            payload = {
                "probe_id": probe_id,
                "provider": args.provider,
                "call_ordinals": call_ordinals(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "raw_output": result.raw_output,
                "cleaned_output": result.cleaned_output,
                "decision": result.decision.model_dump(mode="json"),
                "guard": guard.model_dump(mode="json"),
                "telemetry": result.model_dump(mode="json", exclude={"decision", "raw_output", "cleaned_output"}),
            }
            write_json(output_dir / "result.json", payload)
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if guard.accepted else 2
        except ModelRunFailure as exc:
            payload = {
                "probe_id": probe_id,
                "provider": args.provider,
                "call_ordinals": call_ordinals(),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "failure_category": exc.category,
                "error": str(exc),
                "raw_output": exc.raw_output,
                "cleaned_output": exc.cleaned_output,
                "telemetry": exc.telemetry,
            }
            write_json(output_dir / "failure.json", payload)
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            return 3
        finally:
            model.close()
    except Exception as exc:
        write_json(
            output_dir / "probe-failure.json",
            {
                "probe_id": probe_id,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
