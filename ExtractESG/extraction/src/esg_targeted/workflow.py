from __future__ import annotations

import json
import os
import threading
import time
import traceback
from contextlib import nullcontext
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import numpy as np
from esg_standard_packages.contracts import CompiledStandardPackage

from esg_targeted.config import Settings
from esg_targeted.contracts import (
    EvidencePacket,
    EvidenceGap,
    ExtractionRequest,
    GuardIssue,
    GuardResult,
    JobStatus,
    SemanticDecision,
)
from esg_targeted.evidence.inventory import EvidenceInventoryBuilder
from esg_targeted.evidence.disclosure import DisclosureSession
from esg_targeted.evidence.execution_strategy import ExecutionStrategyCompiler
from esg_targeted.evidence.context import EvidenceContextCompiler
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.evidence.visual_inputs import FocusedVisualInputBuilder
from esg_targeted.evidence.visual_route import VisualEscalationPlanner
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.ids import safe_filename, stable_id
from esg_targeted.io import read_json
from esg_targeted.ir.catalog import DocumentIrCatalog
from esg_targeted.ir.loader import DocumentIrLoader
from esg_targeted.models.errors import ModelRunFailure
from esg_targeted.models.nuextract import NuExtractMlxModel
from esg_targeted.models.model_selection import model_selection, same_model_backend
from esg_targeted.models.preflight import PacketPreflightGuard, PromptBudgetExceeded
from esg_targeted.models.protocols import SemanticFillModel
from esg_targeted.models.qiniu_vlm import QiniuVlmModel
from esg_targeted.models.qwen import QwenMlxModel
from esg_targeted.runtime_budget import BudgetExceeded
from esg_targeted.results.exporter import ResultExporter
from esg_targeted.results.deduplicator import ExactFactDeduplicator
from esg_targeted.results.derivation import DerivationExecutor
from esg_targeted.results.family_resolution import FamilyConflictResolver
from esg_targeted.results.semantic_conflicts import SemanticConflictAuditor
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.spec import (
    RECORD_LAYOUT,
    RESULT_BUNDLE_LAYOUT_VERSION,
    RESULT_SCHEMA_VERSION,
)
from esg_targeted.results.validation import ResultContractError, ResultContractValidator
from esg_targeted.retrieval.embeddings import (
    CachedEmbeddingIndex,
    EmbeddingProvider,
    QwenMlxEmbeddingProvider,
)
from esg_targeted.retrieval.hybrid import HybridRetriever
from esg_targeted.retrieval.sufficiency import EvidenceSufficiencyGate
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.query_compiler import MetricQueryCompiler
from esg_targeted.standards.runtime_snapshot import (
    build_internal_dependency_scope, deferred_extension_result, execution_digest, load_runtime_package, selected_output_ids,
)
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.index_assets import SemanticIndexAssetCatalog
from esg_targeted.storage.job_store import JobStore
from esg_targeted.storage.execution_supervisor import SupervisedJob, supervised_model_call


class CancelledError(RuntimeError):
    pass


class TargetedExtractionWorkflow:
    checkpoint_schema_version = "targeted-checkpoint-v3"

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

    def _execution_profile(self, request: ExtractionRequest, *, role="semantic_brain") -> dict:
        provider = getattr(request.roles, role).provider if request.roles else request.semantic_provider
        if provider == "qiniu_vlm":
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

    def _build_semantic_model(self, request: ExtractionRequest, *, role="semantic_brain") -> SemanticFillModel:
        if self.semantic_model_factory is not None:
            return self.semantic_model_factory()
        selection = model_selection(request, self.settings, role=role)
        provider, model_id = selection.provider, selection.model_id
        if provider == "qiniu_vlm":
            return QiniuVlmModel(
                api_key=self.settings.qiniu_api_key,
                base_url=self.settings.qiniu_base_url,
                model_id=model_id or self.settings.qiniu_model,
                timeout_seconds=request.call_timeout_seconds or self.settings.qiniu_timeout_seconds,
                max_output_tokens=self.settings.qiniu_max_output_tokens,
                max_image_bytes=self.settings.qiniu_max_image_bytes,
            )
        model_class = QwenMlxModel if provider == "local_qwen" else NuExtractMlxModel
        model_path = selection.model_path
        return model_class(
            model_path,
            max_tokens=self.settings.model_max_tokens,
            max_input_chars=self.settings.packet_max_chars,
            max_input_tokens=self.settings.model_max_input_tokens,
            timeout_seconds=request.call_timeout_seconds or self.settings.model_timeout_seconds,
            min_output_tokens=self.settings.model_min_output_tokens,
        )

    def _build_semantic_models(self, request: ExtractionRequest):
        if not request.semantic_fill:
            return None, None
        semantic = self._build_semantic_model(request)
        if self.semantic_model_factory is not None or not request.roles:
            return semantic, semantic
        brain = model_selection(request, self.settings, role="semantic_brain")
        specialist = model_selection(request, self.settings, role="specialist")
        return semantic, semantic if brain.identity == specialist.identity else self._build_semantic_model(request, role="specialist")

    @staticmethod
    def _model_label(model) -> str:
        if model is None:
            return "semantic model"
        provider = getattr(model, "provider_name", "local_nuextract")
        model_id = getattr(model, "model_id", None)
        return f"{provider}:{model_id}" if model_id else provider

    @staticmethod
    def _confusable_metric_contract(metric, package, elements_by_id) -> dict:
        fixed_scope = []
        for element_id in package.elements_by_metric.get(metric.metric_id, []):
            element = elements_by_id[element_id]
            if element.value_contract.fixed_value is None:
                continue
            fixed_scope.append(
                {
                    "element_code": element.element_code,
                    "value": element.value_contract.fixed_value,
                }
            )
        return {
            "id": metric.metric_id,
            "label": metric.labels.get("zh") or metric.labels.get("en"),
            "description": metric.description[:320],
            "measurement_kind": metric.measurement_kind,
            "fixed_scope": fixed_scope,
        }

    def run(self, job_id: str) -> None:
        # The unified queue owns isolated workers. Fake models remain in-process for
        # offline contract tests; neither path couples native work to an HTTP thread.
        if self.semantic_model_factory is not None:
            return self._run(job_id)
        with SupervisedJob(self.job_store.path, job_id):
            return self._run(job_id)

    def _run(self, job_id: str) -> None:
        request = self.job_store.get(job_id).request
        previous_failure = (
            self.artifact_store.read_json(job_id, "failure.json")
            if self.artifact_store.exists(job_id, "failure.json")
            else {}
        )
        contract_recovery_job = (
            "ResultContractError" in str(previous_failure.get("error") or "")
            or "duplicates dimension_value_id"
            in str(previous_failure.get("error") or "")
        )
        embedding_provider = None
        semantic_model = None
        specialist_model = None
        try:
            execution_profile = self._execution_profile(request)
            self.job_store.claim(job_id, f"{os.getpid()}:{threading.get_ident()}")
            self._stage(job_id, "loading_inputs", 0, 0, "Loading immutable inputs")
            snapshot = self.artifact_store.job_dir(job_id) / "input/standard-package.json"
            package = (self.standard_catalog.compiler.validate_compiled(snapshot) if snapshot.is_file()
                       else self.standard_catalog.load(request.package_id, request.package_version))
            package = load_runtime_package(package, artifact_root=self.artifact_store.job_dir(job_id), request=request)
            ir_path = self.ir_catalog.resolve(request.ir_run_id)
            ir = DocumentIrLoader().load(
                ir_path,
                require_ready=self.settings.require_ready_ir and not request.force_unready_ir,
            )
            selected_metrics = self._selected_metrics(package, selected_output_ids(request, package))
            selected_metric_ids = {m.metric_id for m in selected_metrics}
            internal_ids = self._dependency_metric_ids(package, selected_metric_ids) if request.roles or request.internal_metric_ids else set()
            internal_ids.update(request.internal_metric_ids)
            extension_operands = {mid for recipe in getattr(package, 'extension_computations', [])
                                  for mid in recipe['internal_operand_metric_ids']}
            internal_ids.update(extension_operands)
            if hasattr(package, 'runtime_snapshot'):
                internal_ids.update(self._dependency_metric_ids(package, selected_metric_ids | extension_operands))
            internal_ids -= selected_metric_ids
            self._selected_metrics(package, sorted(internal_ids)) if internal_ids else []  # validate explicit dependency IDs
            metrics = self._selected_metrics(package, [m.metric_id for m in package.metrics if m.metric_id in selected_metric_ids | internal_ids])
            self.artifact_store.write_json(job_id, "input/execution-plan.json", {
                "selected_metric_ids": sorted(selected_metric_ids), "internal_metric_ids": sorted(internal_ids),
                "runtime_standard_digest": execution_digest(package),
                "original_output_count": sum(m.source_datapoint_id is not None for m in selected_metrics),
                "extension_output_count": sum(m.source_datapoint_id is None for m in selected_metrics),
                "roles": request.roles.model_dump(mode="json") if request.roles else None,
            })
            if getattr(package, 'extension_computations', []):
                self.artifact_store.write_json(job_id, 'results/extension-computations.json', {
                    'schema_version':'extension-computation-requests-v1',
                    'runtime_standard_digest':execution_digest(package), 'requests':package.extension_computations})
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
                with supervised_model_call(self.job_store.path, job_id, "embedding_index",
                                           timeout_seconds=self.settings.model_timeout_seconds):
                    embedding_matrix = embedding_index.build_or_load(
                        inventory.spans,
                        cache_dir / "embeddings.npy",
                        cache_dir / "metadata.json",
                    )
                index_elapsed_seconds = round(time.perf_counter() - index_started, 3)
                query_texts = [queries[metric.metric_id].semantic_text for metric in metrics]
                embed_queries = getattr(embedding_provider, "embed_queries", None)
                query_started = time.perf_counter()
                with supervised_model_call(self.job_store.path, job_id, "embedding_queries",
                                           timeout_seconds=self.settings.model_timeout_seconds):
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
            selection_compilers, packet_preflights, region_compilers = {}, {}, {}
            for role in ("semantic_brain", "specialist"):
                profile = self._execution_profile(request, role=role)
                selection_compilers[role] = EvidenceSelectionCompiler(
                    max_spans=profile["selection_max_spans"], max_groups=profile["selection_max_groups"],
                    max_candidates=profile["selection_max_candidates"], max_visual_artifacts=profile["selection_max_images"],
                    max_retrieved_objects=request.retrieval_object_top_n)
                packet_preflights[role] = PacketPreflightGuard(profile["packet_max_chars"])
                region_compilers[role] = EvidenceRegionCompiler(
                    max_context_chars=profile["region_max_chars"], max_groups_per_region=profile["region_max_groups"],
                    max_regions=profile["max_regions"], max_output_rows=profile["region_max_output_rows"],
                    max_span_chars=profile["region_max_span_chars"], max_images_per_region=profile["region_max_images"])
            sufficiency_gate = EvidenceSufficiencyGate()
            guard_engine = GroundingGuard()
            materializer = CoreResultMaterializer()
            contract_validator = ResultContractValidator(package.core)
            visual_planner = VisualEscalationPlanner()
            semantic_model, specialist_model = self._build_semantic_models(request)
            from esg_targeted.models.budgeted import BudgetedModel
            from esg_targeted.budget_scope import package_model_budget
            # Role calls share one package ledger; another package cannot spend it.
            shared_budget = package_model_budget(self.artifact_store, request, job_id)
            same_model = semantic_model is specialist_model
            if semantic_model is not None:
                semantic_model = BudgetedModel(semantic_model, shared_budget, role="semantic_brain")
            if same_model and not request.roles:
                specialist_model = semantic_model
            elif specialist_model is not None:
                # One runtime can serve two distinct budgeted task roles.
                specialist_model = BudgetedModel(specialist_model, shared_budget, role="specialist")
            model_label = self._model_label(semantic_model)
            from esg_targeted.retrieval.embeddings import CachedQueryEmbeddingService
            def release_local_semantic_models():
                for model in {id(getattr(m, "_delegate", m)): m for m in (semantic_model, specialist_model) if m is not None}.values():
                    if getattr(model, "provider_name", "").startswith("local_"):
                        model.close()
            dynamic_queries = CachedQueryEmbeddingService(
                self.embedding_provider_factory, self.settings.index_cache_root / inventory.inventory_id / "queries",
                model_identity=str(self.settings.embedding_model_path), before_load=release_local_semantic_models,
                initial_vectors={queries[mid].semantic_text: vector for mid, vector in query_vectors.items()},
            )
            records: dict[str, list[dict]] = defaultdict(list)
            record_index: dict[str, dict[str, dict]] = {
                collection: {} for collection in RECORD_LAYOUT
            }
            outcomes = []
            model_executions: list[dict[str, str]] = []

            elements_by_id = {item.element_id: item for item in package.elements}
            metrics_by_id = {item.metric_id: item for item in package.metrics}
            retrieval_candidate_pool = max(self.settings.retrieval_top_k, request.retrieval_object_top_n * 12, 36)
            prepared_metrics = {}
            strategy_compiler = ExecutionStrategyCompiler()
            compiled_strategies = {}
            compiled_strategy_enabled = bool(request.report_session_id and package.format_version in {"2.0", "2.1"}
                and request.extraction_mode == "auto" and request.semantic_fill)

            def prepare_metric(metric):
                if metric.metric_id in prepared_metrics:
                    return prepared_metrics[metric.metric_id]
                self._check_cancel(job_id)
                query = queries[metric.metric_id]
                hits = retriever.search(query, retrieval_candidate_pool, query_vector=query_vectors.get(metric.metric_id))
                sufficiency = sufficiency_gate.assess(query, inventory, hits, retrieval_complete=True)
                task_id = stable_id("task", ir.document_id, ir.run_id, ir.ir_revision, package.manifest.package_id,
                                    package.manifest.package_version, metric.metric_id)
                elements = [elements_by_id[item] for item in package.elements_by_metric[metric.metric_id]]
                role = "semantic_brain" if compiled_strategy_enabled or disclosure_session.eligible(metric.metric_id) else "specialist"
                def compile_packet(selected_role):
                    selected = selection_compilers[selected_role].compile(
                        task_id=task_id, metric=metric, elements=elements, query=query, inventory=inventory,
                        hits=hits, retrieval_complete=True,
                        model_led_review=selected_role == "semantic_brain",
                        confusable_metrics=([
                            self._confusable_metric_contract(metrics_by_id[sibling_id], package, elements_by_id)
                            for sibling_id in metric.confusable_metric_ids if sibling_id in metrics_by_id
                        ][:8] if metric.metric_family else []),
                    )
                    selected.budget["retrieval_sufficiency"] = sufficiency.model_dump(mode="json")
                    return EvidenceContextCompiler().attach(selected, inventory, package)
                packet = compile_packet(role)
                if compiled_strategy_enabled:
                    descriptor = strategy_compiler.compile(metric, packet, request)
                    if descriptor.route == "table_series":
                        direct_packet = compile_packet("specialist")
                        if strategy_compiler.has_selected_table_cells(direct_packet):
                            packet = direct_packet
                        else:
                            descriptor = strategy_compiler.compile(metric, packet, request, permit_table_series=False)
                    descriptor = strategy_compiler.compile(metric, packet, request,
                        model_identity=model_selection(request, self.settings, role=descriptor.role).model_id,
                        permit_table_series=descriptor.route == "table_series")
                    compiled_strategies[metric.metric_id] = descriptor.model_dump()
                    packet.budget["execution_strategy"] = descriptor.model_dump()
                prepared_metrics[metric.metric_id] = query, hits, sufficiency, packet
                return prepared_metrics[metric.metric_id]

            def search_report(metric_id, text):
                metric = metrics_by_id[metric_id]
                base = queries[metric_id]
                from esg_targeted.retrieval.tokenizer import tokenize
                rewritten = base.model_copy(update={
                    "query_text": text, "lexical_text": text,
                    "semantic_text": text, "lexical_terms": tokenize(text),
                    "intent": base.intent.model_copy(update={
                        "topic_term_groups": [[text]], "must_term_groups": [],
                        "role_term_groups": [], "should_terms": tokenize(text),
                    }),
                })
                vector = None
                if request.semantic_search:
                    with supervised_model_call(self.job_store.path, job_id, "embedding_gap_query",
                                               timeout_seconds=request.call_timeout_seconds or self.settings.model_timeout_seconds):
                        vector = dynamic_queries.vector(text)
                    self.job_store.add_event(job_id, "embedding_gap_query", "info", "Embedded the actual evidence query", dynamic_queries.last_telemetry)
                hits = retriever.search(rewritten, retrieval_candidate_pool, query_vector=vector)
                elements = [elements_by_id[e] for e in package.elements_by_metric[metric_id]]
                found = selection_compilers["semantic_brain"].compile(
                    task_id=prepare_metric(metric)[3].task_id, metric=metric, elements=elements,
                    query=rewritten, inventory=inventory, hits=hits, retrieval_complete=True,
                    model_led_review=True,
                )
                return EvidenceContextCompiler().attach(found, inventory, package)

            if compiled_strategy_enabled:
                for metric in metrics:
                    if metric.metric_id not in getattr(package, 'derived_only_metric_ids', set()):
                        prepare_metric(metric)
                execution_plan = self.artifact_store.read_json(job_id, "input/execution-plan.json")
                execution_plan.update(execution_strategy_schema="evidence-execution-strategy-v1", execution_strategies=compiled_strategies)
                self.artifact_store.write_json(job_id, "input/execution-plan.json", execution_plan)

            disclosure_session = DisclosureSession(
                package=package, metrics=[m for m in metrics if m.metric_id not in getattr(package, 'derived_only_metric_ids', set())], request=request, artifact_store=self.artifact_store,
                job_id=job_id, max_context_chars=execution_profile["packet_max_chars"],
                inventory=inventory, job_store=self.job_store, search_report=search_report,
                execution_strategies=compiled_strategies if compiled_strategy_enabled else None,
                call_timeout_seconds=request.call_timeout_seconds or (self.settings.qiniu_timeout_seconds
                if (request.roles.semantic_brain.provider if request.roles else request.semantic_provider) == "qiniu_vlm" else self.settings.model_timeout_seconds),
            )
            initial_agent_calls = disclosure_session.agent["model_calls"]
            for index, metric in enumerate(metrics, 1):
                self._check_cancel(job_id)
                attempts_path = "progress/metric-attempts.json"
                metric_attempts = self.artifact_store.read_json(job_id, attempts_path) if self.artifact_store.exists(job_id, attempts_path) else {}
                metric_attempts[metric.metric_id] = {"status": "entered"}
                self.artifact_store.write_json(job_id, attempts_path, metric_attempts)
                disclosure_session.agent["current_metric_id"] = metric.metric_id
                disclosure_session._save()
                metric_slug = safe_filename(metric.metric_id)
                checkpoint_path = f"checkpoints/{metric_slug}.json"
                if metric.metric_id in getattr(package, 'derived_only_metric_ids', set()):
                    result = deferred_extension_result(package, metric, ir, job_id)
                    outcome = self._commit_metric_result(job_id=job_id, checkpoint_path=checkpoint_path,
                        metric=metric, package=package, result=result, packet_id=result.outcome.task_id,
                        records=records, record_index=record_index, validator=contract_validator, limited_evidence=False)
                    outcomes.append(outcome)
                    self._stage(job_id, 'reviewed_publication_dependency', index, len(metrics),
                                '延伸指标等待操作数审核及正式发布计算')
                    continue
                if self.artifact_store.exists(job_id, checkpoint_path):
                    checkpoint = self.artifact_store.read_json(job_id, checkpoint_path)
                    strategy_current = (not compiled_strategy_enabled or strategy_compiler.checkpoint_matches(checkpoint, compiled_strategies[metric.metric_id]))
                    semantic_current = strategy_current and (not disclosure_session.eligible(metric.metric_id) or
                                        checkpoint.get("semantic_pipeline_version") == disclosure_session.agent["version"])
                    if semantic_current:
                        retired = disclosure_session._retired_source_ids()
                        projection_path = f"disclosures/projections/{metric_slug}.json"
                        if self.artifact_store.exists(job_id, projection_path):
                            semantic_current = not any(b.get("source_fact_id") in retired for b in
                                self.artifact_store.read_json(job_id, projection_path).get("core_bindings", []))
                    if (
                        semantic_current
                        and
                        checkpoint.get("metric_id") == metric.metric_id
                        and self._checkpoint_reusable(
                            checkpoint,
                            package_source_digest=package.compilation.source_digest,
                            materializer_version=materializer.materializer_version,
                            runtime_standard_digest=execution_digest(package),
                        )
                    ):
                        try:
                            contract_validator.validate(checkpoint["records"])
                            self._merge_records(
                                records,
                                record_index,
                                checkpoint["records"],
                                metric.metric_id,
                            )
                        except ResultContractError as exc:
                            self.job_store.add_event(
                                job_id,
                                "checkpoint_rejected",
                                "warning",
                                f"Checkpoint contract failed for {metric.source_datapoint_id}; rebuilding from preserved decision",
                                {"metric_id": metric.metric_id, "error": str(exc)},
                            )
                        else:
                            outcomes.append(checkpoint["outcome"])
                            self.job_store.update(job_id, progress_current=index)
                            self.job_store.add_event(
                                job_id,
                                "checkpoint_resume",
                                "info",
                                f"Reused contract-validated checkpoint for {metric.source_datapoint_id}",
                                {"metric_id": metric.metric_id},
                            )
                            continue

                    should_rematerialize = strategy_current and (semantic_current or contract_recovery_job) and self._checkpoint_requires_rematerialization(
                        checkpoint,
                        contract_recovery_job=contract_recovery_job,
                        package_source_digest=package.compilation.source_digest,
                        runtime_standard_digest=execution_digest(package),
                        materializer_version=materializer.materializer_version,
                    )
                    recovered = (
                        self._rematerialize_preserved_decision(
                            job_id=job_id,
                            metric=metric,
                            metric_slug=metric_slug,
                            package=package,
                            materializer=materializer,
                        )
                        if should_rematerialize
                        else None
                    )
                    if recovered is not None:
                        recovered_result, recovered_packet_id = recovered
                        outcome = self._commit_metric_result(
                            job_id=job_id,
                            checkpoint_path=checkpoint_path,
                            metric=metric,
                            package=package,
                            result=recovered_result,
                            packet_id=recovered_packet_id,
                            records=records,
                            record_index=record_index,
                            validator=contract_validator,
                            limited_evidence=(
                                ir.manifest.get("evidence_policy", {}).get("mode")
                                == "limited"
                            ),
                            recovery_mode="rematerialize_preserved_decision",
                            semantic_pipeline_version=(disclosure_session.agent["version"]
                                                       if disclosure_session.eligible(metric.metric_id) else None),
                            execution_strategy=compiled_strategies.get(metric.metric_id),
                        )
                        outcomes.append(outcome)
                        self.job_store.update(job_id, progress_current=index)
                        continue
                    if should_rematerialize:
                        # A preserved semantic result must not trigger an implicit
                        # second model call merely because reconstruction failed.
                        failed_outcome = dict(checkpoint.get("outcome") or {})
                        failed_outcome.update(
                            {
                                "task_id": failed_outcome.get("task_id")
                                or checkpoint.get("task_id"),
                                "metric_id": metric.metric_id,
                                "status": "system_contract_failed",
                                "found_status": "uncertain",
                                "review_status": "human_required",
                                "guard_accepted": bool(
                                    failed_outcome.get("guard_accepted")
                                ),
                                "attempts": int(
                                    failed_outcome.get("attempts") or 0
                                ),
                                "result_counts": {
                                    "reporting_task": 0,
                                    "quantitative_observation": 0,
                                    "qualitative_assertion": 0,
                                    "attribute_value": 0,
                                    "dimension_value": 0,
                                    "evidence_reference": 0,
                                },
                                "uncertainty_reason": (
                                    "Preserved checkpoint could not be rematerialized; "
                                    "no model call was made."
                                ),
                                "contract_validation_status": "failed",
                                "contract_error": (
                                    "Preserved packet, accepted decision, or Guard "
                                    "artifact is missing or invalid."
                                ),
                            }
                        )
                        outcomes.append(failed_outcome)
                        self.job_store.update(job_id, progress_current=index)
                        continue

                self._stage(
                    job_id,
                    "metric_retrieval",
                    index - 1,
                    len(metrics),
                    f"Retrieving evidence for {metric.source_datapoint_id}",
                    {"metric_id": metric.metric_id},
                )
                query, hits, sufficiency, packet = prepare_metric(metric)
                if disclosure_session.eligible(metric.metric_id):
                    packet = disclosure_session.expand(metric, packet, prepare_metric)
                visual_route = visual_planner.plan(
                    packet,
                    sufficiency,
                    visual_enabled=bool(request.visual_fallback and request.semantic_fill),
                    force_table_visual=True,
                )
                packet.budget["visual_route"] = visual_route.as_dict()
                execution_role = "semantic_brain" if disclosure_session.eligible(metric.metric_id) else "specialist"
                decision_packets = region_compilers[execution_role].split(packet)
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
                for decision_packet in ([] if disclosure_session.eligible(metric.metric_id) else decision_packets):
                    try:
                        preflight = packet_preflights[execution_role].validate(decision_packet)
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

                if sufficiency.auto_not_found_allowed and not packet.allowed_group_ids and not disclosure_session.eligible(metric.metric_id):
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
                    active_model = semantic_model if disclosure_session.eligible(metric.metric_id) else specialist_model
                    inactive_model = specialist_model if active_model is semantic_model else semantic_model
                    if inactive_model is not None and not same_model_backend(inactive_model, active_model) and getattr(inactive_model, "provider_name", "").startswith("local_"):
                        inactive_model.close()
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
                        semantic_model=semantic_model if disclosure_session.eligible(metric.metric_id) else specialist_model,
                        guard_engine=guard_engine,
                        request=request,
                        disclosure_session=disclosure_session,
                    )
                    if disclosure_session.eligible(metric.metric_id) and hasattr(disclosure_session, "last_packet"):
                        packet = disclosure_session.last_packet
                        self.artifact_store.write_json(job_id, f"packets/{metric_slug}.json", packet.model_dump(mode="json"))

                for model_record in model_records:
                    provider = model_record.get("provider")
                    model_id = model_record.get("model_id")
                    if provider:
                        model_executions.append(
                            {
                                "provider": str(provider),
                                "model_id": str(model_id or "unknown"),
                                "route": str(model_record.get("route") or "decision"),
                                "role": ("semantic_brain" if disclosure_session.eligible(metric.metric_id) else "specialist") if request.roles else "semantic",
                                "metric_id": metric.metric_id,
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
                if disclosure_session.eligible(metric.metric_id):
                    result.outcome.failure_category = disclosure_session.topic_results.get(metric.disclosure_topic, {}).get("failure_category")
                else:
                    result.outcome.failure_category = next((r.get("failure_category") for r in reversed(model_records) if r.get("failure_category")), None)
                outcome = self._commit_metric_result(
                    job_id=job_id,
                    checkpoint_path=checkpoint_path,
                    metric=metric,
                    package=package,
                    result=result,
                    packet_id=packet.packet_id,
                    records=records,
                    record_index=record_index,
                    validator=contract_validator,
                    limited_evidence=(
                        ir.manifest.get("evidence_policy", {}).get("mode") == "limited"
                    ),
                    semantic_pipeline_version=(disclosure_session.agent["version"]
                                               if disclosure_session.eligible(metric.metric_id) else None),
                    execution_strategy=compiled_strategies.get(metric.metric_id),
                )
                outcomes.append(outcome)
                self.job_store.update(job_id, progress_current=index)
                exhausted_boundary = (disclosure_session.agent.get("budget_boundary", shared_budget.boundary)
                    if disclosure_session.eligible(metric.metric_id) else
                    next((r.get("budget_boundary", shared_budget.boundary) for r in reversed(model_records) if r.get("failure_category") == "budget"), shared_budget.boundary))
                if outcome.get("failure_category") == "budget" and exhausted_boundary in {"report", "package"}:
                    raise BudgetExceeded(f"{exhausted_boundary.title()} runtime budget exhausted; completed fragments and unexecuted requirements are preserved",
                                         shared_budget.snapshot()["usage"], boundary=exhausted_boundary)

            # A deliberate reread creates a new interpretation revision. Rebuild
            # dependent projections through mappings for the current semantics;
            # unchanged mapping slices reuse their validated durable outputs.
            if disclosure_session._retired_source_ids():
                retired = disclosure_session._retired_source_ids()
                affected = []
                for path in (self.artifact_store.job_dir(job_id) / "disclosures/projections").glob("*.json"):
                    projection = read_json(path)
                    if any(b.get("source_fact_id") in retired for b in projection.get("core_bindings", [])):
                        affected.append(projection["metric_id"])
                disclosure_session.refresh_invalidated_mappings(affected, model=semantic_model, cancel_check=self._cancel_probe(job_id))
                for metric_id in affected:
                    metric = metrics_by_id[metric_id]
                    packet = EvidencePacket.model_validate(self.artifact_store.read_json(job_id, f"packets/{safe_filename(metric_id)}.agent.json"))
                    decision, guarded, _, _ = disclosure_session.decide(packet=packet, model=semantic_model,
                        guard_engine=guard_engine, cancel_check=self._cancel_probe(job_id))
                    records = defaultdict(list, self._partition_metric_records(records, set(metrics_by_id) - {metric_id}))
                    record_index = {key: {r[RECORD_LAYOUT[key]["primary_key"]]: r for r in records[key]} for key in RECORD_LAYOUT}
                    result = materializer.materialize(package=package, metric=metric, packet=disclosure_session.last_packet,
                        decision=decision, guard=guarded, attempts=0)
                    refreshed = self._commit_metric_result(job_id=job_id, checkpoint_path=f"checkpoints/{safe_filename(metric_id)}.json",
                        metric=metric, package=package, result=result, packet_id=packet.packet_id, records=records,
                        record_index=record_index, validator=contract_validator, limited_evidence=ir.manifest.get("evidence_policy", {}).get("mode") == "limited",
                        semantic_pipeline_version=disclosure_session.agent["version"],
                        execution_strategy=compiled_strategies.get(metric_id))
                    outcomes = [refreshed if o["metric_id"] == metric_id else o for o in outcomes]

            conflict_auditor = SemanticConflictAuditor()
            initial_semantic_conflicts = conflict_auditor.audit(records, package)
            modern_conflicts = [conflict for conflict in initial_semantic_conflicts
                if any(disclosure_session.eligible(mid) for mid in conflict["metric_ids"])]
            direct_conflicts = [conflict for conflict in initial_semantic_conflicts if conflict not in modern_conflicts]
            conflict_reports = []
            if modern_conflicts:
                if specialist_model is not None and not same_model_backend(specialist_model, semantic_model) and getattr(specialist_model, "provider_name", "").startswith("local_"):
                    specialist_model.close()
                modern_report = disclosure_session.resolve_conflicts(conflicts=modern_conflicts,
                    records=records, outcomes=outcomes, model=semantic_model, cancel_check=self._cancel_probe(job_id))
                for family in modern_report.get("families", []):
                    family["route"] = "metric_mapping"
                conflict_reports.append(modern_report)
            if direct_conflicts:
                if semantic_model is not None and not same_model_backend(semantic_model, specialist_model) and getattr(semantic_model, "provider_name", "").startswith("local_"):
                    semantic_model.close()
                with supervised_model_call(self.job_store.path, job_id, "family_adjudication",
                                           timeout_seconds=request.call_timeout_seconds or self.settings.model_timeout_seconds):
                    direct_report = FamilyConflictResolver().resolve(
                        conflicts=direct_conflicts, records=records, outcomes=outcomes,
                        package=package, model=specialist_model, cancel_check=self._cancel_probe(job_id))
                for family in direct_report.get("families", []):
                    family["route"] = "joint_family_adjudication"
                conflict_reports.append(direct_report)
            family_adjudication = {"schema_version": "conflict-resolution-v2", "attempted": any(r.get("attempted") for r in conflict_reports),
                "families": [f for r in conflict_reports for f in r.get("families", [])],
                "resolved_case_ids": [case for r in conflict_reports for case in r.get("resolved_case_ids", [])],
                "rejected_fact_ids": [fid for r in conflict_reports for fid in r.get("rejected_fact_ids", [])]}
            if family_adjudication.get("attempted"):
                self.artifact_store.write_json(
                    job_id,
                    "family-adjudication.json",
                    family_adjudication,
                )
                for family in family_adjudication.get("families", []):
                    if family.get("provider"):
                        model_executions.append(
                            {
                                "provider": str(family["provider"]),
                                "model_id": str(family.get("model_id") or "unknown"),
                                "route": family.get("route", "joint_family_adjudication"),
                                "role": ("semantic_brain" if family.get("route") == "metric_mapping" else "specialist") if request.roles else "semantic",
                            }
                        )
                self.job_store.add_event(
                    job_id,
                    "family_adjudication",
                    "info",
                    "Jointly re-adjudicated conflicting sibling metrics",
                    {
                        "initial_conflict_count": len(initial_semantic_conflicts),
                        "resolved_case_count": len(
                            family_adjudication.get("resolved_case_ids", [])
                        ),
                        "rejected_fact_count": len(
                            family_adjudication.get("rejected_fact_ids", [])
                        ),
                    },
                )
            resolved_fact_sets = {
                frozenset(
                    fact["fact_id"]
                    for fact in case.get("facts", [])
                )
                for family in family_adjudication.get("families", [])
                for case in family.get("packet", {}).get("cases", [])
                if case.get("case_id")
                in set(family.get("decided_case_ids", []))
            }
            resolved_fact_sets.discard(frozenset())
            semantic_conflicts = [
                conflict
                for conflict in conflict_auditor.audit(records, package)
                if frozenset(conflict.get("fact_ids", [])) not in resolved_fact_sets
            ]
            if semantic_conflicts:
                conflicted_metrics = {
                    metric_id
                    for conflict in semantic_conflicts
                    for metric_id in conflict["metric_ids"]
                }
                for outcome in outcomes:
                    if outcome.get("metric_id") not in conflicted_metrics:
                        continue
                    outcome.update(
                        status="partial" if outcome.get("status") == "found" else outcome.get("status"),
                        review_status="human_required",
                        semantic_decision_status="unresolved",
                        display_readiness_status="needs_semantic_completion",
                        uncertainty_reason=(
                            "Sibling metrics assigned the same physical source cell to "
                            "incompatible fixed dimensions; joint model re-adjudication is required."
                        ),
                    )
                for task in records.get("reporting_tasks", []):
                    if task.get("metric_id") in conflicted_metrics:
                        task.update(
                            task_status="partial",
                            found_status="partial",
                            review_status="human_required",
                            uncertainty_reason=(
                                "Cross-metric semantic conflict requires joint re-adjudication."
                            ),
                        )
                self.artifact_store.write_json(
                    job_id,
                    "semantic-conflicts.json",
                    {
                        "schema_version": "semantic-conflicts-v1",
                        "conflict_count": len(semantic_conflicts),
                        "conflicts": semantic_conflicts,
                    },
                )
            source_facts_for_calculation, source_bindings_for_calculation = disclosure_session.projection_sources()
            direct_sources, direct_bindings = disclosure_session.direct_projection_sources(records, model_executions=model_executions)
            source_facts_for_calculation.extend(direct_sources)
            source_bindings_for_calculation.extend(direct_bindings)
            derivation_report = DerivationExecutor().execute(records=records, outcomes=outcomes, package=package,
                source_facts=source_facts_for_calculation, source_bindings=source_bindings_for_calculation)
            for outcome in outcomes:
                if outcome['metric_id'] in getattr(package, 'derived_only_metric_ids', set()):
                    pending = deferred_extension_result(package,
                        next(m for m in package.metrics if m.metric_id == outcome['metric_id']), ir, job_id).outcome
                    outcome.update(dependency_status='blocked', dependency_blocks=pending.dependency_blocks)
            # Recalculate dependency gaps from durable operands on every resume.
            # These gaps concern calculation/mapping, never document IR repair.
            disclosure_session.agent["gaps"] = [gap for gap in disclosure_session.agent.get("gaps", [])
                                                  if gap.get("kind") != "dependency"]
            metrics_for_dependencies = {metric.metric_id: metric for metric in package.metrics}
            for recipe in getattr(package, 'extension_computations', []):
                gap = EvidenceGap(gap_id=stable_id('extension-publication-gap', job_id, recipe['extension_id']),
                    kind='dependency', metric_ids=[recipe['extension_id']], topic_id=recipe['extension_id']+'.topic',
                    reason='延伸计算必须使用审核后的规范操作数，当前提取候选不会自动成为已发布数值。',
                    failure_stage='reviewed_publication', next_action='review_dependencies').model_dump(mode='json')
                disclosure_session.agent.setdefault('gaps', []).append(gap)
            for rule_result in derivation_report["rules"]:
                if rule_result.get("dependency_status") != "blocked":
                    continue
                target = rule_result["target_metric_id"]
                metric = metrics_for_dependencies.get(target)
                for block in rule_result.get("dependency_blocks", []):
                    gap = EvidenceGap(gap_id=stable_id("dependency-gap", job_id, target, block),
                        kind="dependency", metric_ids=[target],
                        topic_id=(metric.disclosure_topic if metric else None) or f"dependency:{rule_result['rule_id']}",
                        reason=f"{block['code']}: {block['reason']}", failure_stage=block["stage"],
                        next_action="review_dependencies").model_dump(mode="json")
                    disclosure_session.agent.setdefault("gaps", []).append(gap)
            disclosure_session._save()
            if package.derivations:
                self.artifact_store.write_json(
                    job_id,
                    "derivations.json",
                    derivation_report,
                )
            internal_outcomes = [o for o in outcomes if o["metric_id"] in internal_ids]
            if internal_ids:
                internal_scope = build_internal_dependency_scope(package,internal_ids,selected_metric_ids,
                    self._partition_metric_records(records,internal_ids),internal_outcomes,
                    source_bindings_for_calculation,source_facts_for_calculation)
                self.artifact_store.write_json(job_id,'results/internal-dependencies.json',internal_scope)
                records = defaultdict(list, self._partition_metric_records(records, selected_metric_ids))
                outcomes = [o for o in outcomes if o["metric_id"] in selected_metric_ids]
            contract_validation = contract_validator.validate(records)
            self.artifact_store.write_json(job_id, "progress/core-postprocessing.json", {
                "package_source_digest": package.compilation.source_digest,
                "runtime_standard_digest": execution_digest(package),
                "records": {key: list(records.get(key, [])) for key in RECORD_LAYOUT}, "outcomes": outcomes,
                "contract_validation": contract_validation,
            })
            from esg_targeted.results.stages import publish_stage
            publish_stage(self.artifact_store, job_id, request, package)
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
            summary["semantic_conflicts"] = {
                "count": len(semantic_conflicts),
                "artifact": "semantic-conflicts.json" if semantic_conflicts else None,
            }
            summary["family_adjudication"] = {
                "attempted": family_adjudication.get("attempted", False),
                "resolved_case_count": len(
                    family_adjudication.get("resolved_case_ids", [])
                ),
                "rejected_fact_count": len(
                    family_adjudication.get("rejected_fact_ids", [])
                ),
                "artifact": (
                    "family-adjudication.json"
                    if family_adjudication.get("attempted")
                    else None
                ),
            }
            summary["derivations"] = {
                "rule_count": len(derivation_report.get("rules", [])),
                "derived_observation_count": len(
                    derivation_report.get("derived_observation_ids", [])
                ),
                "artifact": "derivations.json" if package.derivations else None,
            }
            summary["internal_dependencies"] = {"metric_ids": sorted(internal_ids), "outcome_count": len(internal_outcomes),
                "artifact": "results/internal-dependencies.json" if internal_ids else None}
            summary["report_session_id"] = request.report_session_id
            summary["roles"] = request.roles.model_dump(mode="json") if request.roles else None
            summary["agent"] = disclosure_session.agent
            # Agent state counts actual calls, including failed/repair calls and
            # previous attempts after resume. Per-metric projection reuse is free.
            summary["model_call_count"] = disclosure_session.agent["model_calls"] + sum(
                item["attempts"] for item in outcomes if not disclosure_session.eligible(item["metric_id"])
            )
            summary["agent_model_calls_this_run"] = disclosure_session.agent["model_calls"] - initial_agent_calls
            summary["recorded_model_execution_count"] = len(model_executions)
            accepted_count = sum(
                item["guard_accepted"]
                and item["status"] in {"found", "not_found"}
                and item.get("contract_validation_status") == "passed"
                and item.get("semantic_decision_status") != "unresolved"
                and item.get("display_readiness_status")
                != "needs_semantic_completion"
                for item in outcomes
            )
            has_contract_failures = any(
                item.get("contract_validation_status") == "failed"
                for item in outcomes
            )
            had_semantic_attempt = any(
                int(item.get("attempts") or 0) > 0 for item in outcomes
            )
            source_gap_ids = {g["gap_id"] for g in [*disclosure_session.agent.get("gaps", []),
                              *(g for source in disclosure_session.sources.values() for g in source.get("gaps", []))]}
            summary["evidence_gap_count"] = len(source_gap_ids)
            final_status = (
                JobStatus.COMPLETED if accepted_count == len(outcomes) and not source_gap_ids
                else JobStatus.FAILED
                if (
                    accepted_count == 0
                    and request.semantic_fill
                    and not model_executions
                    and not had_semantic_attempt
                    and not has_contract_failures
                    and not any(o.get('status') == 'awaiting_reviewed_operands' for o in outcomes)
                )
                else JobStatus.PARTIAL
            )
            summary["execution_status"] = final_status.value
            disclosure_session.finalize(records, outcomes, model_executions=model_executions)
            exports = ResultExporter().export(
                output_dir=self.artifact_store.job_dir(job_id),
                records=dict(records),
                summary=summary,
                package=package,
            )
            summary["exports"] = exports
            self.artifact_store.write_json(job_id, "manifest.json", summary)
            self.job_store.update(
                job_id,
                status=final_status,
                stage="failed" if final_status == JobStatus.FAILED else "finished",
                error=(
                    "No metric produced an accepted semantic result; inspect model attempts and provider status."
                    if final_status == JobStatus.FAILED else None
                ),
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
            if isinstance(exc, ModelRunFailure) and exc.category == "cancelled":
                self.job_store.update(job_id, status=JobStatus.CANCELLED, stage="cancelled")
                self.job_store.add_event(job_id, "cancelled", "warning", "Extraction cancelled")
                return
            error = f"{type(exc).__name__}: {exc}"
            failure = {"error": error, "traceback": traceback.format_exc()}
            failure["failure_category"] = getattr(exc, "category", None)
            if isinstance(exc, BudgetExceeded):
                failure["budget_boundary"] = exc.boundary
            if isinstance(exc, ModelRunFailure):
                failure["failure_category"] = exc.category
                failure["provider_error_code"] = exc.telemetry.get("provider_error_code")
                failure["http_status"] = exc.telemetry.get("http_status")
            self.artifact_store.write_json(
                job_id,
                "failure.json",
                failure,
            )
            current_summary = self.job_store.get(job_id).summary or {}
            self.job_store.update(job_id, status=JobStatus.FAILED, stage="failed", error=error,
                                  summary={**current_summary, "failure_category": failure.get("failure_category"),
                                           "budget_boundary": failure.get("budget_boundary"),
                                           "provider_error_code": failure.get("provider_error_code")})
            self.job_store.add_event(job_id, "failed", "error", error)
        finally:
            for model in {id(getattr(m, "_delegate", m)): m for m in (semantic_model, specialist_model) if m is not None}.values():
                model.close()
            if embedding_provider is not None:
                embedding_provider.close()
            current = self.job_store.get(job_id)
            if current.status in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED} and self.artifact_store.exists(job_id, "input/standard-package.json"):
                from esg_targeted.results.stages import publish_stage_from_inputs
                try:
                    snapshot = publish_stage_from_inputs(current.output_dir, job_id, current.status.value, current.error, export_bundle=True)
                    self.job_store.update(job_id, summary={**(current.summary or {}), **snapshot["summary"]})
                except Exception as stage_error:
                    self.job_store.add_event(job_id, "stage_publish_failed", "error", str(stage_error))
                    stage_path = Path(current.output_dir) / 'progress/snapshot.json'
                    if stage_path.is_file():
                        try:
                            retained = json.loads(stage_path.read_text(encoding='utf-8'))
                            if retained.get('schema_version') == 'report-stage-snapshot-v1' and retained.get('summary',{}).get('job_id') == job_id:
                                self.job_store.update(job_id, summary={**(current.summary or {}), **retained['summary'],
                                    'delivery_artifact_error': str(stage_error)})
                        except (OSError, ValueError, KeyError):
                            pass
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
        disclosure_session=None,
    ):
        if disclosure_session and disclosure_session.eligible(metric_id):
            validation_packet = self._region_merge_validation_packet(packet, decision_packets)
            return disclosure_session.decide(
                packet=validation_packet, model=semantic_model, guard_engine=guard_engine,
                cancel_check=self._cancel_probe(job_id),
            )
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
                job_id=job_id, metric_id=metric_id, packet=decision_packet,
                semantic_model=semantic_model, guard_engine=guard_engine, request=request,
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
            if any(r.get("failure_category") == "budget" and r.get("budget_boundary", "report") in {"report", "package"} for r in region_records):
                break

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
        previous_no_progress_payload: str | None = None
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
                call_context = supervised_model_call(
                    self.job_store.path, job_id, "direct_semantic_fill",
                    timeout_seconds=self.settings.model_timeout_seconds,
                ) if isinstance(self.job_store, JobStore) else nullcontext()
                with call_context:
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
                no_progress = bool(
                    decision.status in {"found", "partial"}
                    and not any(
                        group.assignments
                        for group in decision.fact_groups
                        if group.metric_match == "match"
                    )
                )
                semantic_payload = json.dumps(
                    decision.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if no_progress and semantic_payload == previous_no_progress_payload:
                    model_records[-1]["retry_stopped"] = (
                        "identical_empty_semantic_output"
                    )
                    self.job_store.add_event(
                        job_id,
                        "model_call",
                        "warning",
                        f"{model_label} repeated the same empty semantic decision; retries stopped",
                        {
                            "metric_id": metric_id,
                            "attempt": attempt,
                        },
                    )
                    break
                previous_no_progress_payload = semantic_payload if no_progress else None
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
                    "The decision remained ambiguous. Correct it using only supplied IDs. "
                    "For qualitative output provide a visible statement/list member; for "
                    "quantitative output select the correct physical row before its cells."
                )
                use_visual_next = self._visual_evidence_needed(packet, guard, decision)
            except ModelRunFailure as exc:
                elapsed = round(time.perf_counter() - started_at, 3)
                if exc.category == "cancelled":
                    raise CancelledError() from exc
                if exc.category == "provider_account_blocked":
                    self.job_store.add_event(
                        job_id,
                        "provider_account_blocked",
                        "error",
                        "The selected model provider rejected the account before inference",
                        {"metric_id": metric_id, **exc.telemetry},
                    )
                    raise
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
            except BudgetExceeded as exc:
                last_failure_category = "budget"
                model_records.append({"attempt": attempt, "route": "budget", "failure_category": "budget",
                    "budget_boundary": exc.boundary, "error": str(exc), "visual_used": False,
                    "elapsed_seconds": round(time.perf_counter() - started_at, 3)})
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
            "provider_account_blocked": "stop_local_contract",
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

    def _commit_metric_result(
        self,
        *,
        job_id: str,
        checkpoint_path: str,
        metric,
        package: CompiledStandardPackage,
        result,
        packet_id: str,
        records: dict[str, list[dict]],
        record_index: dict[str, dict[str, dict]],
        validator: ResultContractValidator,
        limited_evidence: bool,
        recovery_mode: str | None = None,
        semantic_pipeline_version: str | None = None,
        execution_strategy: dict | None = None,
    ) -> dict:
        if limited_evidence:
            note = (
                "Extraction excludes unresolved IR pages; document-wide completeness "
                "is not established."
            )
            result.outcome.status = "partial"
            result.outcome.found_status = "partial"
            reasons = [result.outcome.uncertainty_reason]
            decision_path = f"decisions/{safe_filename(metric.metric_id)}.json"
            if self.artifact_store.exists(job_id, decision_path):
                preserved = self.artifact_store.read_json(job_id, decision_path)
                reasons.extend(preserved.get("accepted_decision", {}).get("missing_context", []))
            reasons.append(note)
            combined_reason = "; ".join(dict.fromkeys(reason for reason in reasons if reason))
            # A scope limitation adds a reason; it cannot replace the actual
            # planning/protocol/provider failure or create an IR repair cause.
            result.outcome.uncertainty_reason = combined_reason
            for task_record in result.reporting_tasks:
                task_record.update(
                    task_status="partial",
                    found_status="partial",
                    uncertainty_reason=combined_reason,
                )

        checkpoint_records = {
            collection: list(getattr(result, collection))
            for collection in RECORD_LAYOUT
        }
        contract_error = None
        contract_validation = None
        try:
            contract_validation = validator.validate(checkpoint_records)
            self._merge_records(
                records,
                record_index,
                checkpoint_records,
                metric.metric_id,
            )
        except ResultContractError as exc:
            contract_error = str(exc)
            result.outcome.status = "system_contract_failed"
            result.outcome.found_status = "uncertain"
            result.outcome.review_status = "human_required"
            result.outcome.contract_validation_status = "failed"
            result.outcome.contract_error = contract_error
            result.outcome.uncertainty_reason = (
                "Result materialization failed its deterministic contract; preserved "
                "model evidence was quarantined."
            )
            result.outcome.result_counts = {
                "reporting_task": 0,
                "quantitative_observation": 0,
                "qualitative_assertion": 0,
                "attribute_value": 0,
                "dimension_value": 0,
                "evidence_reference": 0,
            }
            self.artifact_store.write_json(
                job_id,
                f"contract-errors/{safe_filename(metric.metric_id)}.json",
                {
                    "metric_id": metric.metric_id,
                    "error_type": "ResultContractError",
                    "error": contract_error,
                    "quarantined_records": checkpoint_records,
                },
            )
            self.job_store.add_event(
                job_id,
                "metric_contract_failed",
                "error",
                f"Quarantined invalid materialization for {metric.source_datapoint_id}",
                {"metric_id": metric.metric_id, "error": contract_error},
            )
        else:
            result.outcome.contract_validation_status = "passed"
            result.outcome.contract_error = None

        outcome = result.outcome.model_dump(mode="json")
        self.artifact_store.write_json(
            job_id,
            checkpoint_path,
            {
                "schema_version": self.checkpoint_schema_version,
                "metric_id": metric.metric_id,
                "task_id": result.outcome.task_id,
                "packet_id": packet_id,
                "package_source_digest": package.compilation.source_digest,
                "runtime_standard_digest": execution_digest(package),
                "materializer_version": CoreResultMaterializer.materializer_version,
                "semantic_pipeline_version": semantic_pipeline_version,
                "execution_strategy": execution_strategy,
                "contract_validation": contract_validation
                or {"status": "failed", "error": contract_error},
                "recovery_mode": recovery_mode,
                "reusable": bool(
                    contract_error is None
                    and outcome["guard_accepted"]
                    and outcome["status"] != "ambiguous"
                    and (semantic_pipeline_version is None or outcome["status"] in {"found", "not_found"})
                ),
                "outcome": outcome,
                "records": checkpoint_records,
            },
        )
        from esg_targeted.results.stages import publish_stage
        publish_stage(self.artifact_store, job_id, self.job_store.get(job_id).request, package)
        return outcome

    def _rematerialize_preserved_decision(
        self,
        *,
        job_id: str,
        metric,
        metric_slug: str,
        package: CompiledStandardPackage,
        materializer: CoreResultMaterializer,
    ):
        required = {
            "packet": f"packets/{metric_slug}.json",
            "decision": f"decisions/{metric_slug}.json",
            "guard": f"guards/{metric_slug}.json",
        }
        if not all(self.artifact_store.exists(job_id, path) for path in required.values()):
            return None
        try:
            packet = EvidencePacket.model_validate(
                self.artifact_store.read_json(job_id, required["packet"])
            )
            decision_payload = self.artifact_store.read_json(job_id, required["decision"])
            decision = SemanticDecision.model_validate(
                decision_payload["accepted_decision"]
            )
            guard = GuardResult.model_validate(
                self.artifact_store.read_json(job_id, required["guard"])
            )
            attempts = 0
            checkpoint_path = f"checkpoints/{metric_slug}.json"
            if self.artifact_store.exists(job_id, checkpoint_path):
                old_checkpoint = self.artifact_store.read_json(job_id, checkpoint_path)
                attempts = int((old_checkpoint.get("outcome") or {}).get("attempts") or 0)
            result = materializer.materialize(
                package=package,
                metric=metric,
                packet=packet,
                decision=decision,
                guard=guard,
                attempts=attempts,
            )
        except Exception as exc:
            self.job_store.add_event(
                job_id,
                "checkpoint_rematerialization_skipped",
                "warning",
                f"Could not rebuild {metric.source_datapoint_id} from preserved artifacts",
                {"metric_id": metric.metric_id, "error": str(exc)},
            )
            return None
        self.job_store.add_event(
            job_id,
            "checkpoint_rematerialized",
            "info",
            f"Rebuilt {metric.source_datapoint_id} without a model call",
            {"metric_id": metric.metric_id, "packet_id": packet.packet_id},
        )
        return result, packet.packet_id

    @staticmethod
    def _merge_records(
        records: dict[str, list[dict]],
        record_index: dict[str, dict[str, dict]],
        incoming: dict[str, list[dict]],
        metric_id: str,
    ) -> None:
        pending: list[tuple[str, str, dict]] = []
        for collection, layout in RECORD_LAYOUT.items():
            primary_key = layout["primary_key"]
            for row in incoming.get(collection, []):
                record_id = row.get(primary_key)
                existing = record_index[collection].get(record_id)
                if existing is None:
                    pending.append((collection, record_id, row))
                elif existing != row:
                    raise ResultContractError(
                        f"aggregate collision for {collection}.{primary_key}={record_id} "
                        f"while merging metric {metric_id}; existing and incoming payloads differ"
                    )
        for collection, record_id, row in pending:
            record_index[collection][record_id] = row
            records[collection].append(row)

    @classmethod
    def _checkpoint_requires_rematerialization(
        cls,
        checkpoint: dict,
        *,
        contract_recovery_job: bool,
        package_source_digest: str,
        materializer_version: str,
        runtime_standard_digest: str | None = None,
    ) -> bool:
        if contract_recovery_job:
            return True
        validation = checkpoint.get("contract_validation") or {}
        if validation.get("status") == "failed":
            return True
        outcome = checkpoint.get("outcome") or {}
        was_semantically_accepted = bool(
            outcome.get("guard_accepted")
            and outcome.get("status") != "ambiguous"
            and checkpoint.get("reusable", True)
        )
        if not was_semantically_accepted:
            return False
        if checkpoint.get("schema_version") != cls.checkpoint_schema_version:
            return True
        return bool(
            checkpoint.get("package_source_digest") != package_source_digest
            or checkpoint.get('runtime_standard_digest', package_source_digest) != (runtime_standard_digest or package_source_digest)
            or checkpoint.get("materializer_version") != materializer_version
        )

    @classmethod
    def _checkpoint_reusable(
        cls,
        checkpoint: dict,
        *,
        package_source_digest: str,
        materializer_version: str,
        runtime_standard_digest: str | None = None,
    ) -> bool:
        return bool(
            checkpoint.get("schema_version") == cls.checkpoint_schema_version
            and checkpoint.get("package_source_digest") == package_source_digest
            and checkpoint.get('runtime_standard_digest', package_source_digest) == (runtime_standard_digest or package_source_digest)
            and checkpoint.get("materializer_version") == materializer_version
            and (checkpoint.get("contract_validation") or {}).get("status") == "passed"
            and checkpoint.get("reusable")
        )

    @staticmethod
    def _partition_metric_records(records, metric_ids):
        """Evidence has no metric_id; partition through its real parent IDs."""
        from esg_targeted.results.durable_sources import partition_metric_records
        return partition_metric_records(records,metric_ids)

    @staticmethod
    def _dependency_metric_ids(package, selected):
        metrics = {m.metric_id: m for m in package.metrics}
        dependencies, pending = set(), list(selected)
        while pending:
            mid = pending.pop()
            metric = metrics[mid]
            if metric.derivation_policy != "reported_then_derived_when_dependencies_complete":
                continue
            required = set(metric.depends_on)
            required.update(o.source_metric_id for rule in package.derivations if rule.target_metric_id == mid
                            and rule.execution_stage == "data_processing" for o in rule.operands)
            for dependency in required:
                if dependency not in metrics:
                    raise ValueError(f"Unknown package dependency {dependency}")
                if dependency not in selected and dependency not in dependencies:
                    dependencies.add(dependency)
                    pending.append(dependency)
        return dependencies

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
        expected_roles = {"semantic_brain": request.roles.semantic_brain, "specialist": request.roles.specialist} if request.roles else {}
        expected_models = {role: (Path(value.model).name if value.model and value.provider.startswith("local_") else value.model)
            for role, value in expected_roles.items()}
        role_runs = [{**item, "expected_provider": expected_roles[item["role"]].provider,
                      "expected_model": expected_models[item["role"]],
                      "provider_match": item["provider"] == expected_roles[item["role"]].provider,
                      "model_match": (item.get("model_id") == expected_models[item["role"]]) if expected_models[item["role"]] else None}
                     for item in model_executions if item.get("role") in expected_roles]
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
                "runtime_standard_digest": execution_digest(package),
            },
            "configuration": request.model_dump(mode="json"),
            "model_execution": {
                "requested_provider": request.semantic_provider,
                "requested_model": request.semantic_model,
                "actual_providers": actual_providers,
                "actual_models": actual_models,
                "model_match": all(item["model_match"] for item in role_runs) if role_runs and all(item["model_match"] is not None for item in role_runs) else None,
                "provider_match": (all(item["provider_match"] for item in role_runs) if role_runs else None) if request.roles else (
                    not request.semantic_fill or actual_providers == [request.semantic_provider]
                ) if actual_providers or not request.semantic_fill else None,
                "requested_roles": request.roles.model_dump(mode="json") if request.roles else None,
                "role_executions": role_runs,
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
