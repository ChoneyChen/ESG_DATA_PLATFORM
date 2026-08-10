from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from esg_v2.evidence.contracts import EvidenceAtom
from esg_v2.standards.contracts import CompiledTaskSet
from esg_v2.storage.package_layout import read_jsonl, resolve_package_path, write_file_index, write_json, write_jsonl
from esg_v2.storage.package_validator import PackageValidationResult, validate_package
from esg_v2.targeted.contracts import (
    CandidateEvidence,
    CandidateVerification,
    LocalInferenceRecord,
    RequirementSearchTrace,
    SearchCoverageRecord,
    TargetedAnswer,
    TargetedValidationReport,
)
from esg_v2.targeted.catalog import DisclosureCatalog
from esg_v2.templates.contracts import TemplateAnswer


class TargetedPackageWriter:
    PACKAGE_TYPE = "targeted-recall-run"
    PACKAGE_SCHEMA = "targeted-recall-package-v2"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def write(
        self,
        *,
        run_id: str,
        ir_run_id: str,
        evidence_manifest: dict[str, Any],
        task_set: CompiledTaskSet,
        template_path: Path,
        template_adapter: Any,
        mode_requested: str,
        traces: list[RequirementSearchTrace],
        candidates: dict[str, list[CandidateEvidence]],
        answers: list[TargetedAnswer],
        atoms: list[EvidenceAtom],
        validation: TargetedValidationReport,
        local_inferences: list[LocalInferenceRecord],
        cloud_calls: list[dict[str, Any]],
        index_metadata: dict[str, Any],
        catalog: DisclosureCatalog,
        verifications: list[CandidateVerification],
    ) -> dict[str, Path]:
        input_template = self.output_dir / "input" / "task-template.xlsx"
        input_template.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, input_template)
        task_path = write_json(self.output_dir / "input" / "task-set.json", task_set.model_dump(mode="json"))
        profiles_path = write_jsonl(
            self.output_dir / "requirements" / "profiles.jsonl",
            [item.model_dump(mode="json") for item in task_set.requirements],
        )
        query_path = write_jsonl(
            self.output_dir / "retrieval" / "queries.jsonl",
            [item.model_dump(mode="json") for item in traces],
        )
        hit_rows = [candidate.model_dump(mode="json") for rows in candidates.values() for candidate in rows]
        hits_path = write_jsonl(self.output_dir / "retrieval" / "hits.jsonl", hit_rows)
        write_json(self.output_dir / "retrieval" / "local-index.json", index_metadata)
        catalog_path = write_jsonl(
            self.output_dir / "catalog" / "disclosures.jsonl",
            [group.model_dump(mode="json") for group in catalog.groups],
        )
        verification_path = write_jsonl(
            self.output_dir / "assessment" / "verifications.jsonl",
            [item.model_dump(mode="json") for item in verifications],
        )
        coverage_records = self._coverage_records(
            task_set,
            traces,
            candidates,
            answers,
            catalog,
            verifications,
        )
        coverage_path = write_jsonl(
            self.output_dir / "quality" / "search-coverage.jsonl",
            [item.model_dump(mode="json") for item in coverage_records],
        )
        answer_path = write_jsonl(
            self.output_dir / "assessment" / "results.jsonl",
            [answer.model_dump(mode="json") for answer in answers],
        )
        atom_map = {atom.atom_id: atom for atom in atoms}
        selected_path = write_jsonl(
            self.output_dir / "evidence" / "selected-packets.jsonl",
            [
                {
                    "requirement_id": answer.requirement_id,
                    "atoms": [atom_map[atom_id].model_dump(mode="json") for atom_id in answer.selected_atom_ids],
                    "group_ids": answer.selected_group_ids,
                    "slot_coverage": [item.model_dump(mode="json") for item in answer.slot_coverage],
                    "facts": [item.model_dump(mode="json") for item in answer.facts],
                }
                for answer in answers
            ],
        )
        final_path = write_jsonl(
            self.output_dir / "final" / "answers.jsonl",
            [answer.model_dump(mode="json") for answer in answers],
        )
        validation_path = write_json(
            self.output_dir / "quality" / "validation-report.json",
            validation.model_dump(mode="json"),
        )
        local_path = write_jsonl(
            self.output_dir / "audit" / "local-inferences.jsonl",
            [item.model_dump(mode="json") for item in local_inferences],
        )
        cloud_path = write_jsonl(self.output_dir / "audit" / "cloud-calls.jsonl", cloud_calls)
        export_path = self.output_dir / "exports" / "filled-result.xlsx"
        export_result = template_adapter.export(
            input_template,
            export_path,
            [
                TemplateAnswer(
                    requirement_id=answer.requirement_id,
                    source_row=answer.source_row,
                    status=answer.status,
                    location=answer.location,
                    quote=answer.quote,
                    value=answer.value,
                    notes=(
                        f"判断难度：{'容易' if answer.easy_to_judge else '不容易'}；"
                        f"置信度：{answer.confidence:.0%}；{answer.reasoning}"
                    ),
                )
                for answer in answers
            ],
        )
        write_json(
            self.output_dir / "exports" / "export-report.json",
            export_result.model_dump(mode="json"),
        )
        manifest_path = write_json(
            self.output_dir / "manifest.json",
            {
                "package_type": self.PACKAGE_TYPE,
                "package_schema_version": self.PACKAGE_SCHEMA,
                "run_id": run_id,
                "source_ir_run_id": ir_run_id,
                "source_evidence_run_id": evidence_manifest["run_id"],
                "template_adapter": template_adapter.adapter_id,
                "template_sha256": task_set.template_sha256,
                "mode_requested": mode_requested,
                "mode_effective": self._effective_mode(traces),
                "requirement_count": len(task_set.requirements),
                "status_counts": validation.status_counts,
                "cloud_call_count": len(cloud_calls),
                "cloud_cost_cny": 0,
                "local_model_inference_count": sum(1 for item in local_inferences if item.status == "succeeded"),
                "can_export": validation.can_export,
                "entrypoints": {
                    "task_set": "input/task-set.json",
                    "profiles": "requirements/profiles.jsonl",
                    "queries": "retrieval/queries.jsonl",
                    "hits": "retrieval/hits.jsonl",
                    "disclosure_catalog": "catalog/disclosures.jsonl",
                    "results": "assessment/results.jsonl",
                    "verifications": "assessment/verifications.jsonl",
                    "selected_evidence": "evidence/selected-packets.jsonl",
                    "answers": "final/answers.jsonl",
                    "validation_report": "quality/validation-report.json",
                    "search_coverage": "quality/search-coverage.jsonl",
                    "local_inferences": "audit/local-inferences.jsonl",
                    "cloud_calls": "audit/cloud-calls.jsonl",
                    "export": "exports/filled-result.xlsx",
                    "integrity": "integrity/files.json",
                },
            },
        )
        integrity_path = write_file_index(self.output_dir, self.output_dir / "integrity" / "files.json")
        validate_package(
            self.output_dir,
            expected_type=self.PACKAGE_TYPE,
            expected_schema=self.PACKAGE_SCHEMA,
            required_entrypoints={
                "task_set", "profiles", "queries", "hits", "results", "selected_evidence",
                "answers", "validation_report", "local_inferences", "cloud_calls", "export", "integrity",
                "disclosure_catalog", "verifications", "search_coverage",
            },
        ).require_valid()
        return {
            "manifest": manifest_path,
            "task_set": task_path,
            "profiles": profiles_path,
            "queries": query_path,
            "hits": hits_path,
            "disclosure_catalog": catalog_path,
            "results": answer_path,
            "verifications": verification_path,
            "selected_evidence": selected_path,
            "answers": final_path,
            "validation": validation_path,
            "search_coverage": coverage_path,
            "local_inferences": local_path,
            "cloud_calls": cloud_path,
            "export": export_path,
            "integrity": integrity_path,
        }

    @staticmethod
    def _effective_mode(traces: list[RequirementSearchTrace]) -> str:
        return "local_semantic" if any(item.mode_effective == "local_semantic" for item in traces) else "local_strict"

    @staticmethod
    def _coverage_records(
        task_set: CompiledTaskSet,
        traces: list[RequirementSearchTrace],
        candidates: dict[str, list[CandidateEvidence]],
        answers: list[TargetedAnswer],
        catalog: DisclosureCatalog,
        verifications: list[CandidateVerification],
    ) -> list[SearchCoverageRecord]:
        answer_map = {answer.requirement_id: answer for answer in answers}
        trace_map = {trace.requirement_id: trace for trace in traces}
        verification_map: dict[str, list[CandidateVerification]] = {}
        for item in verifications:
            verification_map.setdefault(item.requirement_id, []).append(item)
        group_map = catalog.by_id()
        records = []
        for profile in task_set.requirements:
            rows = candidates.get(profile.requirement_id, [])
            checks = verification_map.get(profile.requirement_id, [])
            answer = answer_map[profile.requirement_id]
            trace = trace_map[profile.requirement_id]
            records.append(
                SearchCoverageRecord(
                    requirement_id=profile.requirement_id,
                    candidate_group_count=len(rows),
                    prelimit_candidate_group_count=trace.candidate_count,
                    candidate_limit=trace.candidate_limit,
                    candidate_limit_reached=trace.candidate_limit_reached,
                    verified_group_count=len(checks),
                    complete_group_count=sum(item.decision == "complete" for item in checks),
                    partial_group_count=sum(item.decision == "partial" for item in checks),
                    irrelevant_group_count=sum(item.decision == "irrelevant" for item in checks),
                    not_applicable_group_count=sum(item.decision == "not_applicable" for item in checks),
                    ambiguous_group_count=sum(item.decision == "ambiguous" for item in checks),
                    index_rejected_count=sum(
                        bool(group_map.get(item.group_id) and group_map[item.group_id].features.index_like)
                        for item in rows
                    ),
                    selected_group_ids=answer.selected_group_ids,
                    terminal_status=answer.status,
                    all_retrieved_candidates_verified=len(checks) == len(rows),
                    search_exhausted=answer.status in {"not_found", "uncertain"}
                    and len(checks) == len(rows)
                    and not trace.candidate_limit_reached,
                    # This refers to the report-level candidate pool, not just the returned top-k.
                    # A truncated pool can support abstention but never a definitive negative.
                )
            )
        return records


class TargetedPackageReader:
    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "manifest.json"
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Targeted manifest not found: {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def validate_integrity(self) -> PackageValidationResult:
        schema = str(self.manifest.get("package_schema_version") or "targeted-recall-package-v1")
        required = {
            "task_set", "profiles", "queries", "hits", "results", "selected_evidence",
            "answers", "validation_report", "local_inferences", "cloud_calls", "export", "integrity",
        }
        if schema == TargetedPackageWriter.PACKAGE_SCHEMA:
            required.update({"disclosure_catalog", "verifications", "search_coverage"})
        return validate_package(
            self.root,
            expected_type=TargetedPackageWriter.PACKAGE_TYPE,
            expected_schema=schema,
            required_entrypoints=required,
        )

    def entrypoint(self, key: str) -> Path:
        relative = (self.manifest.get("entrypoints") or {}).get(key)
        if not relative:
            raise KeyError(f"Targeted entrypoint not declared: {key}")
        return resolve_package_path(self.root, str(relative))

    def answers(self) -> list[dict[str, Any]]:
        return read_jsonl(self.entrypoint("answers"))

    def profiles(self) -> list[dict[str, Any]]:
        return read_jsonl(self.entrypoint("profiles"))

    def hits(self) -> list[dict[str, Any]]:
        return read_jsonl(self.entrypoint("hits"))

    def selected_evidence(self) -> list[dict[str, Any]]:
        return read_jsonl(self.entrypoint("selected_evidence"))

    def disclosure_catalog(self) -> list[dict[str, Any]]:
        if "disclosure_catalog" not in (self.manifest.get("entrypoints") or {}):
            return []
        return read_jsonl(self.entrypoint("disclosure_catalog"))

    def verifications(self) -> list[dict[str, Any]]:
        if "verifications" not in (self.manifest.get("entrypoints") or {}):
            return []
        return read_jsonl(self.entrypoint("verifications"))

    def search_coverage(self) -> list[dict[str, Any]]:
        if "search_coverage" not in (self.manifest.get("entrypoints") or {}):
            return []
        return read_jsonl(self.entrypoint("search_coverage"))

    def validation_report(self) -> dict[str, Any]:
        return json.loads(self.entrypoint("validation_report").read_text(encoding="utf-8"))
