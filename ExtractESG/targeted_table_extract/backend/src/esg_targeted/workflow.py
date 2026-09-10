from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from typing import Callable

import numpy as np
from esg_standard_packages.contracts import CompiledStandardPackage

from esg_targeted.config import Settings
from esg_targeted.contracts import (
    ExtractionRequest,
    GuardIssue,
    GuardResult,
    JobStatus,
    SemanticDecision,
)
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.context import EvidenceContextCompiler
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.evidence.visual_inputs import FocusedVisualInputBuilder
from esg_targeted.evidence.visual_route import VisualEscalationPlanner
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ids import safe_filename, stable_id
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.nuextract import NuExtractMlxModel
from esg_targeted.models.preflight import PacketPreflightGuard, PromptBudgetExceeded
from esg_targeted.models.protocols import SemanticFillModel
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.results.exporter import ResultExporter
from esg_targeted.results.deduplicator import ExactFactDeduplicator
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.spec import RESULT_BUNDLE_LAYOUT_VERSION, RESULT_SCHEMA_VERSION
from esg_targeted.results.validation import ResultContractValidator
from esg_targeted.retrieval.embeddings import (
    CachedEmbeddingIndex,
    EmbeddingProvider,
    QwenMlxEmbeddingProvider,
)
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.retrieval.sufficiency import EvidenceSufficiencyGate
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.index_assets import SemanticIndexAssetCatalog
from esg_targeted.storage.job_store import JobStore


class CancelledError(RuntimeError):
    pass


class TargetedExtractionWorkflow:
    def __init__(
        self,
        settings: Settings,
        job_store: JobStore,
        artifact_store: ArtifactStore,
        *,
        embedding_provider_factory: Callable[[], EmbeddingProvider] | None = None,
        semantic_model_factory: Callable[[], SemanticFillModel] | None = None,
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.artifact_store = artifact_store
        self.ir_catalog = DocumentIrCatalog(settings.ir_roots)
        self.standard_catalog = StandardPackageCatalog(settings.standard_dist_root)
        self.semantic_index_assets = SemanticIndexAssetCatalog(
            settings.index_cache_root
        )
        self.embedding_provider_factory = embedding_provider_factory or (
            lambda: QwenMlxEmbeddingProvider(settings.embedding_model_path)
        )
        self.semantic_model_factory = semantic_model_factory

    def _execution_profile(self, request: ExtractionRequest) -> dict:
        if request.semantic_provider == "qiniu_vlm":
            return {
                "packet_max_chars": self.settings.cloud_packet_max_chars,
                "selection_max_spans": 6000,
                "selection_max_groups": 512,
                "selection_max_candidates": 12000,
                "selection_max_images": 8,
                "region_max_groups": 256,
                "region_max_chars": max(
                    10_000, self.settings.cloud_packet_max_chars - 16_000
                ),
                "max_regions": 64,
                "region_max_output_rows": 256,
                "region_max_span_chars": 8_000,
                "region_max_images": 1,
            }
        return {
            "packet_max_chars": self.settings.packet_max_chars,
            "selection_max_spans": 2400,
            "selection_max_groups": 256,
            "selection_max_candidates": 4800,
            "selection_max_images": 32,
            "region_max_groups": 128,
            "region_max_chars": min(
                145_000,
                max(10_000, self.settings.packet_max_chars - 15_000),
            ),
            "max_regions": 64,
            "region_max_output_rows": 256,
            "region_max_span_chars": 4_000,
            "region_max_images": 1,
        }

    def _build_semantic_model(self, request: ExtractionRequest) -> SemanticFillModel:
        if self.semantic_model_factory is not None:
            return self.semantic_model_factory()
        if request.semantic_provider == "qiniu_vlm":
            return QiniuVlmModel(
                api_key=self.settings.qiniu_api_key,
                base_url=self.settings.qiniu_base_url,
                model_id=request.semantic_model or self.settings.qiniu_model,
                timeout_seconds=self.settings.qiniu_timeout_seconds,
                max_output_tokens=self.settings.qiniu_max_output_tokens,
                max_image_bytes=self.settings.qiniu_max_image_bytes,
            )
        return NuExtractMlxModel(
            self.settings.nuextract_model_path,
            max_tokens=self.settings.model_max_tokens,
            max_input_chars=self.settings.packet_max_chars,
            max_input_tokens=self.settings.model_max_input_tokens,
            timeout_seconds=self.settings.model_timeout_seconds,
            min_output_tokens=self.settings.model_min_output_tokens,
        )

    @staticmethod
    def _model_label(model) -> str:
        if model is None:
            return "semantic model"
        provider = getattr(model, "provider_name", "local_nuextract")
        model_id = getattr(model, "model_id", None)
        return f"{provider}:{model_id}" if model_id else provider

    def run(self, job_id: str) -> None:
        request = self.job_store.get(job_id).request
        embedding_provider = None
        semantic_model = None
        try:
            execution_profile = self._execution_profile(request)
            self.job_store.claim(job_id, f"{os.getpid()}:{threading.get_ident()}")
            self._stage(job_id, "loading_inputs", 0, 0, "Loading immutable inputs")
            snapshot = self.artifact_store.job_dir(job_id) / "input/standard-package.json"
            package = (self.standard_catalog.compiler.validate_compiled(snapshot) if snapshot.is_file()
                       else self.standard_catalog.load(request.package_id, request.package_version))
            ir_path = self.ir_catalog.resolve(request.ir_run_id)
            ir = DocumentIrLoader().load(
                ir_path,
                require_ready=self.settings.require_ready_ir and not request.force_unready_ir,
            )
            metrics = self._selected_metrics(package, request.metric_ids)
            self.job_store.update(job_id, progress_total=len(metrics))
            self.artifact_store.write_json(
                job_id, "input/request.json", request.model_dump(mode="json")
            )
            self.artifact_store.write_json(job_id, "input/ir-manifest.json", ir.manifest)
            self.artifact_store.write_json(
                job_id, "input/standard-package.json", package.model_dump(mode="json")
            )
            self._check_cancel(job_id)

            self._stage(
                job_id,
                "evidence_inventory",
                0,
                len(metrics),
                "Building normalized Evidence Inventory",
            )
            inventory = EvidenceInventoryBuilder().build(ir)
            self.artifact_store.write_json(
                job_id,
                "inventory/index.json",
                {
                    "inventory_id": inventory.inventory_id,
                    "document_id": inventory.document_id,
                    "ir_run_id": inventory.ir_run_id,
                    "ir_revision": inventory.ir_revision,
                    "stats": inventory.stats,
                    "page_images": inventory.page_images,
                    "visual_artifacts": inventory.visual_artifacts,
                    "visual_artifact_pages": inventory.visual_artifact_pages,
                },
            )
            self.artifact_store.write_jsonl(
                job_id,
                "inventory/spans.jsonl",
                (item.model_dump(mode="json") for item in inventory.spans),
            )
            self.artifact_store.write_jsonl(
                job_id,
                "inventory/candidates.jsonl",
                (item.model_dump(mode="json") for item in inventory.candidates),
            )

            query_compiler = MetricQueryCompiler()
            queries = {metric.metric_id: query_compiler.compile(package, metric) for metric in metrics}
            embedding_matrix = None
            query_vectors: dict[str, np.ndarray] = {}
            if request.semantic_search:
                self._stage(
                    job_id,
                    "semantic_index",
                    0,
                    len(metrics),
                    "Building/loading Qwen3 semantic index and batching metric queries",
                )
                embedding_provider = self.embedding_provider_factory()
                cache_dir = self.settings.index_cache_root / inventory.inventory_id
                embedding_index = CachedEmbeddingIndex(embedding_provider)
                index_started = time.perf_counter()
                embedding_matrix = embedding_index.build_or_load(
                    inventory.spans,
                    cache_dir / "embeddings.npy",
                    cache_dir / "metadata.json",
                )
                index_elapsed_seconds = round(time.perf_counter() - index_started, 3)
                query_texts = [queries[metric.metric_id].semantic_text for metric in metrics]
                embed_queries = getattr(embedding_provider, "embed_queries", None)
                query_started = time.perf_counter()
                if callable(embed_queries):
                    vectors = embed_queries(query_texts)
                else:
                    vectors = np.stack(
                        [embedding_provider.embed_query(text) for text in query_texts]
                    )
                query_elapsed_seconds = round(time.perf_counter() - query_started, 3)
                query_vectors = {
                    metric.metric_id: vectors[index] for index, metric in enumerate(metrics)
                }
                semantic_index_details = {
                    "inventory_id": inventory.inventory_id,
                    "cache_path": str(cache_dir),
                    "cache_hit": embedding_index.cache_hit,
                    "index_policy": embedding_index.index_policy,
                    "shape": list(embedding_matrix.shape),
                    "retrieval_eligible_span_count": (
                        embedding_index.retrieval_eligible_span_count
                    ),
                    "semantic_indexed_span_count": (
                        embedding_index.semantic_indexed_span_count
                    ),
                    "query_count": len(query_vectors),
                    "batched_queries": True,
                    "model_path": str(self.settings.embedding_model_path),
                    "index_elapsed_seconds": index_elapsed_seconds,
                    "query_elapsed_seconds": query_elapsed_seconds,
                }
                self.semantic_index_assets.record(
                    inventory.inventory_id,
                    {
                        **semantic_index_details,
                        "document_id": inventory.document_id,
                        "document_label": ir.manifest.get("document_label")
                        or (ir.manifest.get("source") or {}).get("display_name"),
                        "ir_run_id": inventory.ir_run_id,
                        "ir_revision": inventory.ir_revision,
                    },
                )
                self.artifact_store.write_json(
                    job_id,
                    "retrieval/semantic-index.json",
                    semantic_index_details,
                )
                self.job_store.add_event(
                    job_id,
                    "semantic_index",
                    "info",
                    (
                        "Reused semantic index cache"
                        if embedding_index.cache_hit
                        else "Built semantic representative index"
                    ),
                    semantic_index_details,
                )
                embedding_provider.close()
                embedding_provider = None

            retriever = HybridRetriever(inventory, embedding_matrix=embedding_matrix)
            selection_compiler = EvidenceSelectionCompiler(
                max_spans=execution_profile["selection_max_spans"],
                max_groups=execution_profile["selection_max_groups"],
                max_candidates=execution_profile["selection_max_candidates"],
                max_visual_artifacts=execution_profile["selection_max_images"],
                max_retrieved_objects=request.retrieval_object_top_n,
            )
            packet_preflight = PacketPreflightGuard(execution_profile["packet_max_chars"])
            region_compiler = EvidenceRegionCompiler(
                max_context_chars=execution_profile["region_max_chars"],
                max_groups_per_region=execution_profile["region_max_groups"],
                max_regions=execution_profile["max_regions"],
                max_output_rows=execution_profile["region_max_output_rows"],
                max_span_chars=execution_profile["region_max_span_chars"],
                max_images_per_region=execution_profile["region_max_images"],
            )
            sufficiency_gate = EvidenceSufficiencyGate()
            guard_engine = GroundingGuard()
            materializer = CoreResultMaterializer()
            visual_planner = VisualEscalationPlanner()
            semantic_model = self._build_semantic_model(request) if request.semantic_fill else None
            model_label = self._model_label(semantic_model)
            records: dict[str, list[dict]] = defaultdict(list)
            outcomes = []
            model_executions: list[dict[str, str]] = []

            elements_by_id = {item.element_id: item for item in package.elements}
            for index, metric in enumerate(metrics, 1):
                self._check_cancel(job_id)
                metric_slug = safe_filename(metric.metric_id)
                checkpoint_path = f"checkpoints/{metric_slug}.json"
                if self.artifact_store.exists(job_id, checkpoint_path):
                    checkpoint = self.artifact_store.read_json(job_id, checkpoint_path)
                    if (
                        checkpoint.get("metric_id") == metric.metric_id
                        and self._checkpoint_reusable(checkpoint)
                    ):
                        outcomes.append(checkpoint["outcome"])
                        for key, rows in checkpoint["records"].items():
                            records[key].extend(rows)
                        self.job_store.update(job_id, progress_current=index)
                        self.job_store.add_event(
                            job_id,
                            "checkpoint_resume",
                            "info",
                            f"Reused completed checkpoint for {metric.source_datapoint_id}",
                            {"metric_id": metric.metric_id},
                        )
                        continue

                self._stage(
                    job_id,
                    "metric_retrieval",
                    index - 1,
                    len(metrics),
                    f"Retrieving evidence for {metric.source_datapoint_id}",
                    {"metric_id": metric.metric_id},
                )
                query = queries[metric.metric_id]
                # Top N controls how many independent objects reach the model, not
                # how shallowly we discover candidate objects. Keep a wider local
                # candidate pool so several high-scoring cells from one index table
                # cannot hide the actual data table.
                retrieval_candidate_pool = max(
                    self.settings.retrieval_top_k,
                    request.retrieval_object_top_n * 12,
                    36,
                )
                hits = retriever.search(
                    query,
                    retrieval_candidate_pool,
                    query_vector=query_vectors.get(metric.metric_id),
                )
                sufficiency = sufficiency_gate.assess(
                    query, inventory, hits, retrieval_complete=True
                )
                task_id = stable_id(
                    "task",
                    ir.document_id,
                    ir.ir_revision,
                    package.manifest.package_id,
                    package.manifest.package_version,
                    metric.metric_id,
                )
                elements = [
                    elements_by_id[item]
                    for item in package.elements_by_metric[metric.metric_id]
                ]
                packet = selection_compiler.compile(
                    task_id=task_id,
                    metric=metric,
                    elements=elements,
                    query=query,
                    inventory=inventory,
                    hits=hits,
                    retrieval_complete=True,
                )
                packet = EvidenceContextCompiler().attach(packet, inventory, package)
                visual_route = visual_planner.plan(
                    packet,
                    sufficiency,
                    visual_enabled=bool(request.visual_fallback and request.semantic_fill),
                    force_table_visual=True,
                )
                packet.budget["visual_route"] = visual_route.as_dict()
                decision_packets = region_compiler.split(packet)
                decision_packets = FocusedVisualInputBuilder(
                    self.artifact_store.job_dir(job_id) / "model-inputs"
                ).build_all(decision_packets)
                packet.budget["evidence_regions"] = [
                    {
                        "packet_id": item.packet_id,
                        "source_group_count": len(item.allowed_group_ids),
                        "span_count": len(item.spans),
                        "candidate_count": len(item.candidates),
                        "context_chars": len(item.model_context),
                        "region": item.budget.get("region"),
                        "max_output_rows": item.budget.get("max_output_rows"),
                        "focused_visual": item.budget.get("focused_visual"),
                    }
                    for item in decision_packets
                ]
                preflight_error = None
                preflight_records = []
                for decision_packet in decision_packets:
                    try:
                        preflight = packet_preflight.validate(decision_packet)
                        preflight_records.append(
                            {"packet_id": decision_packet.packet_id, **preflight.as_dict()}
                        )
                    except PromptBudgetExceeded as exc:
                        preflight_error = str(exc)
                        preflight_records.append(
                            {
                                "packet_id": decision_packet.packet_id,
                                "accepted": False,
                                "error": preflight_error,
                            }
                        )
                        break
                packet.budget["preflight"] = {
                    "accepted": preflight_error is None,
                    "regions": preflight_records,
                }
                self.artifact_store.write_json(
                    job_id,
                    f"retrieval/{metric_slug}.json",
                    {
                        "query": query.model_dump(mode="json"),
                        "sufficiency": sufficiency.model_dump(mode="json"),
                        "visual_route": visual_route.as_dict(),
                        "candidate_pool_size": retrieval_candidate_pool,
                        "requested_object_top_n": request.retrieval_object_top_n,
                        "object_ranking": packet.budget.get(
                            "evidence_object_ranking", []
                        ),
                        "selected_objects": packet.budget.get(
                            "selected_retrieval_objects", []
                        ),
                        "hits": [item.model_dump(mode="json") for item in hits],
                    },
                )
                self.artifact_store.write_json(
                    job_id, f"packets/{metric_slug}.json", packet.model_dump(mode="json")
                )
                self.artifact_store.write_json(
                    job_id,
                    f"packets/{metric_slug}.regions.json",
                    {
                        "parent_packet_id": packet.packet_id,
                        "regions": [
                            item.model_dump(mode="json") for item in decision_packets
                        ],
                    },
                )

                if sufficiency.auto_not_found_allowed and not packet.allowed_group_ids:
                    self._stage(
                        job_id,
                        "local_not_found",
                        index - 1,
                        len(metrics),
                        f"Full local scan found no plausible evidence for {metric.source_datapoint_id}",
                        {"metric_id": metric.metric_id},
                    )
                    decision = SemanticDecision(
                        task_id=packet.task_id,
                        status="not_found",
                        fact_groups=[],
                        selected_evidence_span_ids=[],
                        uncertainty_code="none",
                    )
                    guard_result = guard_engine.validate(packet, decision)
                    attempts = 0
                    model_records = [
                        {
                            "attempt": 0,
                            "route": "local_not_found",
                            "sufficiency": sufficiency.model_dump(mode="json"),
                        }
                    ]
                elif preflight_error:
                    decision, guard_result, attempts, model_records = self._preflight_rejection(
                        packet, preflight_error
                    )
                else:
                    self._stage(
                        job_id,
                        "semantic_fill",
                        index - 1,
                        len(metrics),
                        f"Directly filling standard fields for {metric.source_datapoint_id}",
                        {"metric_id": metric.metric_id},
                    )
                    decision, guard_result, attempts, model_records = self._decide_regions(
                        job_id=job_id,
                        metric_id=metric.metric_id,
                        packet=packet,
                        decision_packets=decision_packets,
                        semantic_model=semantic_model,
                        guard_engine=guard_engine,
                        request=request,
                    )

                for model_record in model_records:
                    provider = model_record.get("provider")
                    model_id = model_record.get("model_id")
                    if provider:
                        model_executions.append(
                            {
                                "provider": str(provider),
                                "model_id": str(model_id or "unknown"),
                                "route": str(model_record.get("route") or "decision"),
                            }
                        )

                self._stage(
                    job_id,
                    "grounding_guard",
                    index - 1,
                    len(metrics),
                    f"Applying deterministic grounding guard for {metric.source_datapoint_id}",
                    {
                        "metric_id": metric.metric_id,
                        "accepted": guard_result.accepted,
                        "attempts": attempts,
                    },
                )
                self.artifact_store.write_json(
                    job_id,
                    f"decisions/{metric_slug}.json",
                    {
                        "attempts": model_records,
                        "accepted_decision": decision.model_dump(mode="json"),
                    },
                )
                self.artifact_store.write_json(
                    job_id,
                    f"guards/{metric_slug}.json",
                    guard_result.model_dump(mode="json"),
                )
                result = materializer.materialize(
                    package=package,
                    metric=metric,
                    packet=packet,
                    decision=decision,
                    guard=guard_result,
                    attempts=attempts,
                )
                if ir.manifest.get("evidence_policy", {}).get("mode") == "limited":
                    note = "Extraction excludes unresolved IR pages; document-wide completeness is not established."
                    result.outcome.status = "partial"
                    result.outcome.found_status = "partial"
                    result.outcome.uncertainty_reason = note
                    for task_record in result.reporting_tasks:
                        task_record.update(task_status="partial", found_status="partial", uncertainty_reason=note)
                outcome = result.outcome.model_dump(mode="json")
                outcomes.append(outcome)
                checkpoint_records = {}
                for key in (
                    "reporting_tasks",
                    "quantitative_observations",
                    "qualitative_assertions",
                    "attribute_values",
                    "dimension_values",
                    "evidence_references",
                ):
                    rows = getattr(result, key)
                    records[key].extend(rows)
                    checkpoint_records[key] = rows
                self.artifact_store.write_json(
                    job_id,
                    checkpoint_path,
                    {
                        "schema_version": "targeted-checkpoint-v2",
                        "metric_id": metric.metric_id,
                        "task_id": task_id,
                        "packet_id": packet.packet_id,
                        "reusable": bool(
                            outcome["guard_accepted"] and outcome["status"] != "ambiguous"
                        ),
                        "outcome": outcome,
                        "records": checkpoint_records,
                    },
                )
                self.job_store.update(job_id, progress_current=index)

            contract_validation = ResultContractValidator(package.core).validate(records)
            summary = self._summary(
                job_id,
                request,
                ir.manifest,
                package,
                outcomes,
                records,
                contract_validation,
                model_executions,
            )
            exports = ResultExporter().export(
                output_dir=self.artifact_store.job_dir(job_id),
                records=dict(records),
                summary=summary,
                package=package,
            )
            summary["exports"] = exports
            self.artifact_store.write_json(job_id, "manifest.json", summary)
            final_status = (
                JobStatus.COMPLETED
                if all(item["guard_accepted"] and item["status"] != "ambiguous" for item in outcomes)
                else JobStatus.PARTIAL
            )
            self.job_store.update(
                job_id,
                status=final_status,
                stage="finished",
                progress_current=len(metrics),
                progress_total=len(metrics),
                summary=summary,
            )
            self.job_store.add_event(
                job_id,
                "finished",
                "info",
                f"Extraction finished with status {final_status.value}",
                {"task_count": len(metrics)},
            )
        except CancelledError:
            self.job_store.update(job_id, status=JobStatus.CANCELLED, stage="cancelled")
            self.job_store.add_event(job_id, "cancelled", "warning", "Extraction cancelled")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.artifact_store.write_json(
                job_id,
                "failure.json",
                {"error": error, "traceback": traceback.format_exc()},
            )
            self.job_store.update(job_id, status=JobStatus.FAILED, stage="failed", error=error)
            self.job_store.add_event(job_id, "failed", "error", error)
        finally:
            if semantic_model is not None:
                semantic_model.close()
            if embedding_provider is not None:
                embedding_provider.close()
            self.job_store.release_worker(job_id)

    def _decide_regions(
        self,
        *,
        job_id,
        metric_id,
        packet,
        decision_packets,
        semantic_model,
        guard_engine,
        request,
    ):
        if len(decision_packets) == 1 and decision_packets[0].packet_id == packet.packet_id:
            return self._decide(
                job_id=job_id,
                metric_id=metric_id,
                packet=packet,
                semantic_model=semantic_model,
                guard_engine=guard_engine,
                request=request,
            )

        accepted_decisions = []
        model_records = []
        attempts = 0
        unresolved = False
        for region_index, decision_packet in enumerate(decision_packets, 1):
            decision, guard, region_attempts, region_records = self._decide(
                job_id=job_id,
                metric_id=metric_id,
                packet=decision_packet,
                semantic_model=semantic_model,
                guard_engine=guard_engine,
                request=request,
            )
            attempts += region_attempts
            for record in region_records:
                model_records.append(
                    {
                        **record,
                        "region_index": region_index,
                        "region_count": len(decision_packets),
                        "region_packet_id": decision_packet.packet_id,
                    }
                )
            if guard.accepted and decision.status in {"found", "partial", "not_found"}:
                accepted_decisions.append(decision)
            elif not (guard.accepted and decision.status == "not_found"):
                unresolved = True

        deduplication = ExactFactDeduplicator().merge([
            group
            for decision in accepted_decisions
            for group in decision.fact_groups
        ])
        groups = deduplication.groups
        selected_spans = sorted(
            {
                span_id
                for decision in accepted_decisions
                for span_id in decision.selected_evidence_span_ids
            }
        )
        if groups:
            partial = unresolved or any(
                decision.status == "partial" for decision in accepted_decisions
            )
            status = "partial" if partial else "found"
            uncertainty = "insufficient_evidence" if partial else "none"
        elif unresolved:
            status = "ambiguous"
            uncertainty = "model_output_invalid"
        else:
            status = "not_found"
            uncertainty = "none"
        merged = SemanticDecision(
            task_id=packet.task_id,
            status=status,
            fact_groups=groups,
            selected_evidence_span_ids=selected_spans,
            uncertainty_code=uncertainty,
            skipped_targets={k: v for d in accepted_decisions for k, v in d.skipped_targets.items()},
            missing_context=list(dict.fromkeys(s for d in accepted_decisions for s in d.missing_context)),
        )
        validation_packet = self._region_merge_validation_packet(
            packet,
            decision_packets,
        )
        guard = guard_engine.validate(validation_packet, merged)
        if merged.status == "ambiguous":
            guard = self._unresolved_guard(
                validation_packet,
                guard,
                code="regions_unresolved",
                message="One or more coherent evidence regions did not produce a grounded multi-row fill",
            )
        model_records.append(
            {
                "attempt": attempts,
                "route": "final_exact_deduplication",
                "region_count": len(decision_packets),
                "input_row_count": deduplication.input_count,
                "output_row_count": deduplication.output_count,
                "removed_duplicate_count": deduplication.removed_count,
                "visual_used": False,
                "elapsed_seconds": 0.0,
            }
        )
        model_records.append(
            {
                "attempt": attempts,
                "route": "region_merge",
                "region_count": len(decision_packets),
                "output_row_count": len(groups),
                "decision": merged.model_dump(mode="json"),
                "guard": guard.model_dump(mode="json"),
                "visual_used": False,
                "elapsed_seconds": 0.0,
            }
        )
        if guard.accepted:
            return merged, guard, attempts, model_records
        safe = SemanticDecision(
            task_id=packet.task_id,
            status="ambiguous",
            fact_groups=[],
            selected_evidence_span_ids=[],
            uncertainty_code="model_output_invalid",
        )
        return safe, guard, attempts, model_records

    @staticmethod
    def _region_merge_validation_packet(packet, decision_packets):
        """Build the union contract used by the final deterministic merge.

        Region compilation adds physical target-cell contracts that do not exist in
        the parent retrieval packet.  Decisions produced from those regions use the
        immutable IR cell id as ``group_ref_id`` so sibling period/entity cells stay
        distinct.  Final validation must therefore retain both the visual anchors
        *and* the target-cell-to-row mapping from every region.  Validating against
        the parent packet alone incorrectly turns an already accepted region result
        into ``unknown_group``/``cross_group_source`` errors.
        """

        visual_anchor_ids = list(
            dict.fromkeys(
                anchor_id
                for region in decision_packets
                for anchor_id in region.alias_map.get("visual", {}).values()
            )
        )
        page_image_paths = list(
            dict.fromkeys(
                path for region in decision_packets for path in region.page_image_paths
            )
        )
        group_aliases = dict(packet.alias_map.get("groups", {}))
        span_aliases = dict(packet.alias_map.get("spans", {}))
        group_alias_by_id = {value: key for key, value in group_aliases.items()}
        span_alias_by_id = {value: key for key, value in span_aliases.items()}

        def ensure_alias(alias_map, reverse_map, value, prefix):
            alias = reverse_map.get(value)
            if alias is not None:
                return alias
            next_index = len(alias_map) + 1
            alias = f"{prefix}{next_index}"
            while alias in alias_map:
                next_index += 1
                alias = f"{prefix}{next_index}"
            alias_map[alias] = value
            reverse_map[value] = alias
            return alias

        target_cells = []
        seen_cell_ids = set()
        for region in decision_packets:
            try:
                region_context = json.loads(region.model_context)
            except (TypeError, json.JSONDecodeError):
                continue
            for raw_cell in (region_context.get("region") or {}).get(
                "target_value_cells"
            ) or []:
                cell_id = str(raw_cell.get("cell_id") or "").strip()
                region_group_alias = str(raw_cell.get("group") or "").strip()
                group_id = region.alias_map.get("groups", {}).get(region_group_alias)
                if not cell_id or not group_id or cell_id in seen_cell_ids:
                    continue
                seen_cell_ids.add(cell_id)
                group_alias = ensure_alias(
                    group_aliases, group_alias_by_id, group_id, "G"
                )
                normalized_cell = dict(raw_cell)
                normalized_cell["id"] = f"T{len(target_cells) + 1}"
                normalized_cell["group"] = group_alias

                region_span_alias = str(raw_cell.get("span") or "").strip()
                span_id = region.alias_map.get("spans", {}).get(region_span_alias)
                if span_id:
                    normalized_cell["span"] = ensure_alias(
                        span_aliases, span_alias_by_id, span_id, "S"
                    )
                target_cells.append(normalized_cell)

        try:
            validation_context = json.loads(packet.model_context)
        except (TypeError, json.JSONDecodeError):
            validation_context = {}
        validation_region = dict(validation_context.get("region") or {})
        validation_region["target_value_cells"] = target_cells
        validation_context["region"] = validation_region

        alias_map = {
            **packet.alias_map,
            "groups": group_aliases,
            "spans": span_aliases,
            "visual": {
                f"V{index}": anchor_id
                for index, anchor_id in enumerate(visual_anchor_ids, 1)
            },
            "target_cells": {
                cell["id"]: cell["cell_id"] for cell in target_cells
            },
        }
        return packet.model_copy(
            update={
                "alias_map": alias_map,
                "page_image_paths": page_image_paths or packet.page_image_paths,
                "model_context": json.dumps(
                    validation_context,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "allowed_group_ids": list(
                    dict.fromkeys(
                        [
                            *packet.allowed_group_ids,
                            *group_aliases.values(),
                        ]
                    )
                ),
                "budget": {
                    **packet.budget,
                    "merge_target_value_cell_count": len(target_cells),
                },
            },
            deep=True,
        )

    def _decide(
        self,
        *,
        job_id,
        metric_id,
        packet,
        semantic_model,
        guard_engine,
        request,
    ):
        if semantic_model is None:
            decision = SemanticDecision(
                task_id=packet.task_id,
                status="ambiguous",
                fact_groups=[],
                selected_evidence_span_ids=[],
                uncertainty_code="insufficient_evidence",
            )
            guard = guard_engine.validate(packet, decision)
            guard = self._unresolved_guard(
                packet,
                guard,
                code="semantic_model_unavailable",
                message="No semantic fill model was available for this evidence packet",
            )
            return decision, guard, 0, []

        feedback = None
        model_records = []
        last_decision = None
        last_guard = None
        last_failure_category = None
        continuation_fragments: list[SemanticDecision] = []
        visual_route = packet.budget.get("visual_route", {})
        prefer_visual = getattr(semantic_model, "prefers_visual_for", None)
        use_visual_next = bool(
            visual_route.get("route") == "visual_first"
            or (callable(prefer_visual) and prefer_visual(packet))
        )
        model_label = self._model_label(semantic_model)
        max_attempts = 1 + self.settings.model_retries
        previous_failed_output: tuple[str, str] | None = None
        for attempt in range(1, max_attempts + 1):
            use_visual = bool(request.visual_fallback and use_visual_next)
            self.job_store.add_event(
                job_id,
                "model_call",
                "info",
                f"{model_label} attempt {attempt} started",
                {
                    "metric_id": metric_id,
                    "attempt": attempt,
                    "phase": "started",
                    "visual_used": use_visual,
                    "retry_reason": last_failure_category,
                },
            )
            started_at = time.perf_counter()
            try:
                result = semantic_model.decide(
                    packet,
                    feedback=feedback,
                    use_visual=use_visual,
                    cancel_check=self._cancel_probe(job_id),
                )
                decision = result.decision
                guard = guard_engine.validate(packet, decision)
                elapsed = round(time.perf_counter() - started_at, 3)
                model_records.append(
                    {
                        "attempt": attempt,
                        "route": "visual" if use_visual else "text",
                        "raw_output": result.raw_output,
                        "cleaned_output": result.cleaned_output,
                        "visual_used": result.visual_used,
                        "image_count": result.image_count,
                        "generation_tokens": result.generation_tokens,
                        "output_token_budget": result.output_token_budget,
                        "prompt_tokens": result.prompt_tokens,
                        "finish_reason": result.finish_reason,
                        "input_chars": result.input_chars,
                        "peak_memory_gb": result.peak_memory_gb,
                        "timings": result.timings,
                        "normalization_actions": result.normalization_actions,
                        "provider": result.provider,
                        "model_id": result.model_id,
                        "region": packet.budget.get("region"),
                        "input_group_count": len(packet.allowed_group_ids),
                        "input_span_count": len(packet.spans),
                        "input_candidate_count": len(packet.candidates),
                        "output_row_count": len(decision.fact_groups),
                        "elapsed_seconds": elapsed,
                        "decision": decision.model_dump(mode="json"),
                        "guard": guard.model_dump(mode="json"),
                    }
                )
                self.job_store.add_event(
                    job_id,
                    "model_call",
                    "info",
                    f"{model_label} attempt {attempt} finished",
                    {
                        "metric_id": metric_id,
                        "attempt": attempt,
                        "phase": "finished",
                        "elapsed_seconds": elapsed,
                        "guard_accepted": guard.accepted,
                        "finish_reason": result.finish_reason,
                        "output_row_count": len(decision.fact_groups),
                    },
                )
                last_decision, last_guard = decision, guard
                if continuation_fragments and guard.accepted:
                    decision = self._merge_continuation_decisions(
                        packet,
                        [*continuation_fragments, decision],
                    )
                    guard = guard_engine.validate(packet, decision)
                    last_decision, last_guard = decision, guard
                    model_records.append(
                        {
                            "attempt": attempt,
                            "route": "output_continuation_merge",
                            "fragment_count": len(continuation_fragments) + 1,
                            "output_row_count": len(decision.fact_groups),
                            "decision": decision.model_dump(mode="json"),
                            "guard": guard.model_dump(mode="json"),
                            "visual_used": False,
                            "elapsed_seconds": 0.0,
                        }
                    )
                if (
                    result.finish_reason == "length"
                    and guard.accepted
                    and decision.fact_groups
                    and attempt < max_attempts
                ):
                    continuation_fragments.append(decision)
                    uncovered = self._uncovered_target_aliases(packet, decision)
                    feedback = (
                        "The previous output reached its generation budget. Continue "
                        "with only these not-yet-covered target cells and do not repeat "
                        f"earlier rows: {', '.join(uncovered) if uncovered else 'none deterministically identified'}."
                    )
                    last_failure_category = "output_budget_continuation"
                    use_visual_next = use_visual
                    continue
                # Normal completion is a semantic decision, not a request to fill
                # every OCR number. Only genuine output truncation continues above.
                # In particular, a correctly skipped Scope 1+2 row must not be
                # presented again as a missing Scope 1 value.
                if guard.accepted and decision.status != "ambiguous":
                    return decision, guard, attempt, model_records
                last_failure_category = "grounding"
                feedback = self._compact_feedback(guard.feedback) or (
                    "The decision remained ambiguous. Correct it using only supplied IDs."
                )
                use_visual_next = self._visual_evidence_needed(packet, guard, decision)
            except ModelRunFailure as exc:
                elapsed = round(time.perf_counter() - started_at, 3)
                if exc.category == "cancelled":
                    raise CancelledError() from exc
                last_failure_category = exc.category
                retry_strategy = self._failure_retry_strategy(exc.category)
                feedback = self._failure_feedback(exc.category, str(exc))
                use_visual_next = bool(
                    request.visual_fallback
                    and packet.page_image_paths
                    and (use_visual or exc.category in {"schema", "syntax", "output_budget"})
                )
                model_records.append(
                    {
                        "attempt": attempt,
                        "route": "visual" if use_visual else "text",
                        "failure_category": exc.category,
                        "retry_strategy": retry_strategy,
                        "error": str(exc),
                        "raw_output": exc.raw_output,
                        "cleaned_output": exc.cleaned_output,
                        "visual_used": use_visual,
                        "elapsed_seconds": elapsed,
                        **exc.telemetry,
                    }
                )
                failed_output = str(exc.cleaned_output or exc.raw_output or "")
                repeated_output = bool(
                    failed_output
                    and previous_failed_output == (exc.category, failed_output)
                )
                previous_failed_output = (exc.category, failed_output)
                if repeated_output:
                    model_records[-1]["retry_stopped"] = "identical_contract_output"
                    self.job_store.add_event(
                        job_id,
                        "model_call",
                        "warning",
                        f"{model_label} repeated the same invalid contract output; retries stopped",
                        {
                            "metric_id": metric_id,
                            "attempt": attempt,
                            "failure_category": exc.category,
                        },
                    )
                    break
                if retry_strategy == "stop_local_contract":
                    break
            except PromptBudgetExceeded as exc:
                last_failure_category = "request_budget"
                model_records.append(
                    {
                        "attempt": attempt,
                        "route": "preflight",
                        "failure_category": "request_budget",
                        "error": str(exc),
                        "visual_used": False,
                        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    }
                )
                break
            except Exception as exc:
                last_failure_category = "runtime"
                feedback = self._failure_feedback("runtime", str(exc))
                use_visual_next = False
                model_records.append(
                    {
                        "attempt": attempt,
                        "route": "visual" if use_visual else "text",
                        "failure_category": "runtime",
                        "error": str(exc),
                        "visual_used": use_visual,
                        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    }
                )

        attempts = len(model_records)
        if last_decision is None:
            safe_decision = SemanticDecision(
                task_id=packet.task_id,
                status="ambiguous",
                fact_groups=[],
                selected_evidence_span_ids=[],
                uncertainty_code="model_output_invalid",
            )
            recoverable = last_failure_category in {
                "syntax", "schema", "runtime", "timeout", "output_budget", "service"
            }
            last_guard = GuardResult(
                task_id=packet.task_id,
                accepted=False,
                recoverable=recoverable,
                issues=[
                    GuardIssue(
                        code=f"model_{last_failure_category or 'output_invalid'}",
                        severity="error",
                        message="All bounded model attempts failed before a grounded decision",
                        recommended_action="Inspect the preserved raw output and retry direct semantic filling.",
                    )
                ],
                accepted_group_count=0,
                accepted_assignment_count=0,
                feedback=feedback or "Model output invalid",
            )
            return safe_decision, last_guard, attempts, model_records

        if not last_guard.accepted:
            safe_decision = SemanticDecision(
                task_id=packet.task_id,
                status="ambiguous",
                fact_groups=[],
                selected_evidence_span_ids=[],
                uncertainty_code="model_output_invalid",
            )
            return safe_decision, last_guard, attempts, model_records
        if last_decision.status == "ambiguous":
            last_guard = self._unresolved_guard(
                packet,
                last_guard,
                code="ambiguous_decision",
                message="The model did not produce a grounded found, partial, or not_found decision",
            )
        return last_decision, last_guard, attempts, model_records

    @staticmethod
    def _uncovered_target_aliases(packet, decision) -> list[str]:
        covered_span_ids = {
            span_id
            for group in decision.fact_groups
            for assignment in group.assignments
            for span_id in assignment.evidence_span_ids
        }
        return [
            contract.alias
            for contract in target_cell_contracts(packet)
            if contract.alias
            and contract.alias not in decision.skipped_targets
            and (not contract.span_id or contract.span_id not in covered_span_ids)
        ]

    @classmethod
    def _merge_continuation_decisions(cls, packet, decisions) -> SemanticDecision:
        deduplicated = ExactFactDeduplicator().merge(
            [group for decision in decisions for group in decision.fact_groups]
        ).groups
        selected = sorted(
            {
                span_id
                for decision in decisions
                for span_id in decision.selected_evidence_span_ids
            }
        )
        provisional = SemanticDecision(
            task_id=packet.task_id,
            status="partial",
            fact_groups=deduplicated,
            selected_evidence_span_ids=selected,
            uncertainty_code="insufficient_evidence",
            skipped_targets={k: v for d in decisions for k, v in d.skipped_targets.items()},
            missing_context=list(dict.fromkeys(v for d in decisions for v in d.missing_context)),
        )
        if not cls._uncovered_target_aliases(packet, provisional):
            provisional = provisional.model_copy(
                update={"status": "found", "uncertainty_code": "none"}
            )
        return provisional

    @staticmethod
    def _unresolved_guard(packet, guard, *, code: str, message: str) -> GuardResult:
        issues = list(guard.issues)
        if not any(item.code == code for item in issues):
            issues.append(
                GuardIssue(
                    code=code,
                    severity="error",
                    message=message,
                    recommended_action="Inspect the bounded model packet and preserved model output.",
                )
            )
        feedback = GroundingGuard._feedback(issues)
        return guard.model_copy(
            update={
                "task_id": packet.task_id,
                "accepted": False,
                "recoverable": True,
                "issues": issues,
                "accepted_group_count": 0,
                "accepted_assignment_count": 0,
                "feedback": feedback,
            }
        )

    @staticmethod
    def _preflight_rejection(packet, error):
        decision = SemanticDecision(
            task_id=packet.task_id,
            status="ambiguous",
            fact_groups=[],
            selected_evidence_span_ids=[],
            uncertainty_code="model_output_invalid",
        )
        guard = GuardResult(
            task_id=packet.task_id,
            accepted=False,
            recoverable=False,
            issues=[
                GuardIssue(
                    code="packet_preflight_failed",
                    severity="error",
                    message=error,
                    recommended_action="Reduce packet evidence before any model call.",
                )
            ],
            accepted_group_count=0,
            accepted_assignment_count=0,
            feedback=error,
        )
        return decision, guard, 0, [
            {
                "attempt": 0,
                "route": "preflight",
                "failure_category": "request_budget",
                "error": error,
            }
        ]

    @staticmethod
    def _visual_evidence_needed(packet, guard, decision) -> bool:
        if not packet.page_image_paths:
            return False
        route = packet.budget.get("visual_route", {})
        if route.get("route") == "text_only":
            return False
        if route.get("route") == "visual_first":
            return True
        visual_flags = {
            "needs_visual_review",
            "ocr_low_confidence",
            "visual_only",
            "table_structure_unreliable",
            "review_required",
        }
        flagged = any(
            span.span_type == "figure_text"
            or visual_flags.intersection(span.quality_flags)
            for span in packet.spans
        )
        missing_codes = {
            "span_value_mismatch",
            "candidate_value_mismatch",
            "visual_evidence_unbound",
            "table_header_value_mismatch",
        }
        relevant_issue = decision.status == "ambiguous" or any(
            item.code in missing_codes for item in guard.issues
        )
        planned_escalation = bool(
            route.get("route") == "text_then_visual"
            and "textual_evidence_incomplete" in route.get("reasons", [])
        )
        return bool((flagged or planned_escalation) and relevant_issue)

    @staticmethod
    def _failure_feedback(category: str, message: str) -> str:
        message = TargetedExtractionWorkflow._compact_feedback(message, limit=1200)
        if category == "syntax":
            return (
                "Return exactly one compact JSON object. Do not add markdown, prose, trailing "
                f"brackets or special tokens. Previous syntax error: {message}"
            )
        if category == "schema":
            return (
                "Rewrite only the response contract: return every otherwise valid row, "
                "use one supplied T target_cell and its matching G group per row, and use "
                f"only required fields. Contract error: {message}"
            )
        if category == "output_budget":
            return (
                "The previous generation reached its output limit before yielding a fully "
                "usable JSON object. Return compact JSON only, omit prose, and preserve all "
                f"applicable target cells. Error: {message}"
            )
        if category in {"service", "timeout"}:
            return (
                "The previous request failed at the model transport/runtime boundary before "
                "a usable semantic response was accepted. Repeat the same complete evidence "
                "task without changing its meaning."
            )
        if category == "runtime":
            return f"Retry the same direct multi-row fill after runtime failure: {message}"
        return f"Correct the previous response: {message}"

    @staticmethod
    def _failure_retry_strategy(category: str) -> str:
        return {
            "syntax": "constrained_json_rewrite",
            "schema": "contract_correction",
            "output_budget": "compact_output_rewrite",
            "grounding": "evidence_scoped_correction",
            "service": "transport_retry_same_evidence",
            "timeout": "transport_retry_same_evidence",
            "runtime": "runtime_retry_same_evidence",
            "request_budget": "stop_local_contract",
            "request_config": "stop_local_contract",
        }.get(category, "bounded_retry")

    @staticmethod
    def _compact_feedback(message: str | None, *, limit: int = 1600) -> str:
        if not message:
            return ""
        compact = " ".join(str(message).split())
        return compact if len(compact) <= limit else f"{compact[:limit]}..."

    def _cancel_probe(self, job_id: str):
        last_checked = 0.0
        cached = False

        def probe() -> bool:
            nonlocal last_checked, cached
            now = time.monotonic()
            if now - last_checked >= 0.25:
                cached = self.job_store.is_cancel_requested(job_id)
                last_checked = now
            return cached

        return probe

    @staticmethod
    def _checkpoint_reusable(checkpoint: dict) -> bool:
        if "reusable" in checkpoint:
            return bool(checkpoint["reusable"])
        outcome = checkpoint.get("outcome", {})
        return bool(
            outcome.get("guard_accepted") and outcome.get("status") != "ambiguous"
        )

    @staticmethod
    def _selected_metrics(package: CompiledStandardPackage, requested: list[str]):
        metric_by_id = {item.metric_id: item for item in package.metrics}
        if not requested:
            return list(package.metrics)
        unknown = sorted(set(requested) - metric_by_id.keys())
        if unknown:
            raise ValueError(f"unknown metric ids: {unknown}")
        return [metric_by_id[item] for item in requested]

    def _stage(
        self,
        job_id: str,
        stage: str,
        current: int,
        total: int,
        message: str,
        detail: dict | None = None,
    ) -> None:
        self.job_store.update(
            job_id,
            status=JobStatus.RUNNING,
            stage=stage,
            progress_current=current,
            progress_total=total,
        )
        self.job_store.heartbeat(job_id)
        self.job_store.add_event(job_id, stage, "info", message, detail)

    def _check_cancel(self, job_id: str) -> None:
        if self.job_store.is_cancel_requested(job_id):
            raise CancelledError()

    @staticmethod
    def _summary(
        job_id,
        request,
        ir_manifest,
        package,
        outcomes,
        records,
        contract_validation,
        model_executions,
    ) -> dict:
        actual_providers = sorted({item["provider"] for item in model_executions})
        actual_models = sorted(
            {f"{item['provider']}:{item['model_id']}" for item in model_executions}
        )
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "bundle_layout_version": RESULT_BUNDLE_LAYOUT_VERSION,
            "job_id": job_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "core_schema": {
                "core_schema_id": package.core.core_schema_id,
                "core_schema_version": package.core.core_schema_version,
            },
            "document": {
                "document_id": ir_manifest.get("document_id"),
                "document_label": ir_manifest.get("document_label")
                or ir_manifest.get("source", {}).get("display_name"),
                "ir_run_id": ir_manifest.get("run_id"),
                "ir_revision": ir_manifest.get("ir_revision"),
                "ir_schema_version": ir_manifest.get("schema_version"),
                "evidence_policy": ir_manifest.get("evidence_policy", {}),
            },
            "standard": {
                "package_id": package.manifest.package_id,
                "package_version": package.manifest.package_version,
                "source_digest": package.compilation.source_digest,
            },
            "configuration": request.model_dump(mode="json"),
            "model_execution": {
                "requested_provider": request.semantic_provider,
                "requested_model": request.semantic_model,
                "actual_providers": actual_providers,
                "actual_models": actual_models,
                "provider_match": (
                    not request.semantic_fill
                    or not actual_providers
                    or actual_providers == [request.semantic_provider]
                ),
            },
            "task_count": len(outcomes),
            "task_status_counts": dict(
                sorted(
                    {
                        status: sum(item["status"] == status for item in outcomes)
                        for status in {item["status"] for item in outcomes}
                    }.items()
                )
            ),
            "guard_accepted_count": sum(item["guard_accepted"] for item in outcomes),
            "model_call_count": sum(item["attempts"] for item in outcomes),
            "record_counts": {key: len(value) for key, value in sorted(records.items())},
            "contract_validation": contract_validation,
            "outcomes": outcomes,
        }
