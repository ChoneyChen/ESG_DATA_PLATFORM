from __future__ import annotations

import json
import shutil
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from esg_v2.evidence.builder import EvidenceInventoryBuilder
from esg_v2.evidence.contracts import EvidenceBuildRequest
from esg_v2.evidence.local_index import LocalEvidenceIndex
from esg_v2.evidence.package import EvidencePackageReader
from esg_v2.storage.package_layout import package_dir
from esg_v2.targeted.assessment import TargetedAssessor, TargetedGuard
from esg_v2.targeted.catalog import DisclosureCatalog
from esg_v2.targeted.contracts import LocalInferenceRecord, TargetedRunRequest, TargetedRunResult
from esg_v2.targeted.package import TargetedPackageWriter
from esg_v2.targeted.retrieval import HybridLocalRetriever
from esg_v2.targeted.semantic import SentenceTransformerEmbeddingBackend
from esg_v2.templates.registry import TemplateAdapterRegistry


class TargetedRecallWorkflow:
    def __init__(
        self,
        *,
        document_ir_root: Path,
        evidence_output_root: Path,
        targeted_output_root: Path,
        template_registry: TemplateAdapterRegistry | None = None,
    ):
        self.document_ir_root = document_ir_root
        self.evidence_output_root = evidence_output_root
        self.targeted_output_root = targeted_output_root
        self.template_registry = template_registry or TemplateAdapterRegistry()

    def plan(self, template_path: Path) -> dict[str, object]:
        adapter = self.template_registry.resolve(template_path)
        task_set = adapter.compile(template_path)
        return {
            "adapter_id": adapter.adapter_id,
            "template_name": task_set.template_name,
            "template_sha256": task_set.template_sha256,
            "requirement_count": len(task_set.requirements),
            "counts_by_kind": dict(sorted(Counter(item.requirement_kind for item in task_set.requirements).items())),
            "warnings": task_set.warnings,
            "requirements": [item.model_dump(mode="json") for item in task_set.requirements],
            "cloud_policy": {"enabled": False, "max_calls": 0, "max_cost_cny": 0},
        }

    def run(self, request: TargetedRunRequest, *, log=lambda _message: None) -> TargetedRunResult:
        if request.cloud_policy.enabled or request.cloud_policy.max_calls or request.cloud_policy.max_cost_cny:
            raise ValueError("Cloud fallback is not implemented in this milestone; local modes require a zero cloud budget")
        adapter = self.template_registry.resolve(request.template_path)
        task_set = adapter.compile(request.template_path)
        strategy_counts = Counter(profile.execution_strategy for profile in task_set.requirements)
        log(
            f"Compiled {len(task_set.requirements)} requirement execution specs with "
            f"{adapter.adapter_id}: {dict(sorted(strategy_counts.items()))}"
        )
        evidence_root = self._evidence_root(request, log)
        evidence_reader = EvidencePackageReader(evidence_root)
        evidence_reader.validate_integrity().require_valid()
        if evidence_reader.manifest.get("source_ir_run_id") != request.ir_run_id:
            raise ValueError("Evidence package source_ir_run_id does not match the requested Document IR")
        atoms = evidence_reader.atoms()
        run_id = request.run_id or self._new_run_id()
        final_dir = package_dir(self.targeted_output_root, run_id)
        if final_dir.exists():
            raise FileExistsError(f"Targeted run already exists: {run_id}")
        staging_dir = self.targeted_output_root / ".staging" / run_id
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)
        try:
            index = LocalEvidenceIndex(staging_dir / "retrieval" / "local-index.sqlite3")
            index_metadata = index.build(atoms)
            log(f"Built SQLite FTS5 index for {len(atoms)} evidence atoms")
            catalog = DisclosureCatalog.build(atoms)
            log(f"Compiled {len(catalog.groups)} local disclosure groups with deterministic fact features")
            semantic_backend, inference_records, semantic_status = self._semantic_backend(request, log)
            retrieval = HybridLocalRetriever(
                index,
                atoms,
                catalog=catalog,
                semantic_backend=semantic_backend,
                semantic_status=semantic_status,
            ).retrieve(task_set.requirements, mode_requested=request.mode, top_k=request.top_k)
            inference_records.extend(retrieval.local_inferences)
            assessor = TargetedAssessor(atoms, catalog)
            trace_map = {trace.requirement_id: trace for trace in retrieval.traces}
            answers = [
                assessor.assess(
                    profile,
                    retrieval.candidates.get(profile.requirement_id, []),
                    search_truncated=trace_map[profile.requirement_id].candidate_limit_reached,
                )
                for profile in task_set.requirements
            ]
            cloud_calls: list[dict[str, object]] = []
            validation = TargetedGuard().validate(
                task_set.requirements,
                answers,
                atoms,
                cloud_calls=cloud_calls,
                catalog=catalog,
            )
            if not validation.can_export:
                raise ValueError("Targeted Guard rejected export: " + json.dumps(validation.issues, ensure_ascii=False))
            paths = TargetedPackageWriter(staging_dir).write(
                run_id=run_id,
                ir_run_id=request.ir_run_id,
                evidence_manifest=evidence_reader.manifest,
                task_set=task_set,
                template_path=request.template_path,
                template_adapter=adapter,
                mode_requested=request.mode,
                traces=retrieval.traces,
                candidates=retrieval.candidates,
                answers=answers,
                atoms=atoms,
                validation=validation,
                local_inferences=inference_records,
                cloud_calls=cloud_calls,
                index_metadata=index_metadata,
                catalog=catalog,
                verifications=assessor.verifications,
            )
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir.replace(final_dir)
            counts = Counter(answer.status for answer in answers)
            log(f"Targeted Recall complete with zero cloud calls: {dict(sorted(counts.items()))}")
            return TargetedRunResult(
                run_id=run_id,
                ir_run_id=request.ir_run_id,
                evidence_run_id=str(evidence_reader.manifest["run_id"]),
                status="done",
                output_dir=final_dir,
                manifest_path=final_dir / paths["manifest"].relative_to(staging_dir),
                export_path=final_dir / paths["export"].relative_to(staging_dir),
                requirement_count=len(answers),
                status_counts=dict(sorted(counts.items())),
                cloud_call_count=0,
                cloud_cost_cny=0,
                local_model_inference_count=sum(1 for item in inference_records if item.status == "succeeded"),
            )
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise

    def _evidence_root(self, request: TargetedRunRequest, log) -> Path:
        builder = EvidenceInventoryBuilder(self.document_ir_root, self.evidence_output_root)
        if request.evidence_run_id:
            return package_dir(self.evidence_output_root, request.evidence_run_id)
        existing = builder.find_existing(request.ir_run_id)
        if existing:
            log(f"Reusing admitted Evidence Inventory {existing.name}")
            return existing
        result = builder.build(EvidenceBuildRequest(ir_run_id=request.ir_run_id), log=log)
        return result.output_dir

    @staticmethod
    def _semantic_backend(
        request: TargetedRunRequest,
        log,
    ) -> tuple[SentenceTransformerEmbeddingBackend | None, list[LocalInferenceRecord], str]:
        if request.mode != "local_semantic":
            return None, [LocalInferenceRecord(component="semantic_retrieval", backend="none", status="not_requested")], "not_requested"
        started = time.monotonic()
        try:
            backend = SentenceTransformerEmbeddingBackend(
                request.local_embedding_model,
                allow_download=request.allow_local_model_download,
            )
            log(f"Loaded local embedding model {request.local_embedding_model}")
            return backend, [], "available"
        except Exception as exc:
            log(f"Local embedding unavailable; explicitly falling back to local_strict: {exc}")
            return None, [
                LocalInferenceRecord(
                    component="semantic_retrieval",
                    backend="sentence-transformers",
                    model_id=request.local_embedding_model,
                    status="fallback",
                    duration_ms=(time.monotonic() - started) * 1000,
                    details={"reason": str(exc), "effective_mode": "local_strict"},
                )
            ], "fallback"

    @staticmethod
    def _new_run_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"trg-{stamp}-{uuid.uuid4().hex[:12]}"
