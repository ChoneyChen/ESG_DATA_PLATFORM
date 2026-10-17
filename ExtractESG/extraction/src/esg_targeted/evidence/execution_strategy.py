"""Compile evidence execution from semantic contracts and real selected sources."""
from dataclasses import asdict, dataclass
import hashlib
import json

SCHEMA = "evidence-execution-strategy-v1"


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _dump(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ExecutionStrategy:
    schema_version: str
    metric_id: str
    route: str
    role: str
    reason: str
    contract_digest: str
    source_capability_digest: str
    provider: str | None
    model: str | None
    descriptor_digest: str

    def model_dump(self, **kwargs):
        return asdict(self)


class ExecutionStrategyCompiler:
    @staticmethod
    def is_table_series_contract(metric):
        contract = _get(metric, "measurement_contract")
        cardinality = _get(metric, "cardinality", {})
        maximum = _get(cardinality, "maximum")
        return bool(_get(metric, "data_class") == "quantitative"
            and _get(contract, "template") == "dimensional_table"
            and (maximum is None or isinstance(maximum, int) and maximum > 1)
            and _get(metric, "required_dimension_ids", [])
            and str(_get(metric, "value_family", "") or "").startswith("quantitative_"))

    @staticmethod
    def selected_table_cells(packet):
        if not _get(packet, "source_document_id") or not _get(packet, "source_ir_run_id") or not _get(packet, "source_ir_revision"):
            return []
        budget = _get(packet, "budget", {}) or {}
        objects = budget.get("selected_retrieval_objects", [])
        tables = [obj for obj in objects if obj.get("kind") == "table"]
        if not tables:
            return []
        groups = set(_get(packet, "allowed_group_ids", [])) & {
            group for obj in tables for group in obj.get("selected_group_ids", [])}
        spans = {s.span_id: s for s in _get(packet, "spans", []) if s.context_group_id in groups}
        selected = {}
        for candidate in _get(packet, "candidates", []):
            span = spans.get(candidate.span_id)
            if candidate.candidate_type not in {"number", "quantity", "percentage"} or span is None:
                continue
            if span.span_type != "table_cell" or not span.has_table_structure:
                continue
            cells = sorted({_get(loc, "ir_object_id") for loc in span.locators
                if _get(loc, "ir_object_type") == "cell" and _get(loc, "ir_object_id")})
            if cells:
                selected[(candidate.span_id, candidate.candidate_type)] = {
                    "span_id": candidate.span_id, "group_id": span.context_group_id,
                    "candidate_type": candidate.candidate_type, "cell_ids": cells}
        return [selected[key] for key in sorted(selected)]

    @staticmethod
    def has_selected_table_cells(packet):
        return bool(ExecutionStrategyCompiler.selected_table_cells(packet))

    def compile(self, metric, packet, request, *, model_identity=None, permit_table_series=True):
        table = permit_table_series and self.is_table_series_contract(metric) and self.has_selected_table_cells(packet)
        route, role = ("table_series", "specialist") if table else ("source_agent", "semantic_brain")
        contract = {key: _dump(_get(metric, key)) for key in ("data_class", "measurement_contract", "cardinality",
            "required_dimension_ids", "optional_dimension_ids", "identity_axes", "value_family", "fact_grain",
            "measurement_kind", "required_semantic_discriminators", "excluded_concept_ids", "confusable_metric_ids",
            "description", "reporting_requirement")}
        capabilities = {"document_id": _get(packet, "source_document_id"), "ir_run_id": _get(packet, "source_ir_run_id"),
            "ir_revision": _get(packet, "source_ir_revision"), "table_cells": self.selected_table_cells(packet),
            "objects": [{key: obj.get(key) for key in ("kind", "id", "selected_group_ids")}
                for obj in _get(packet, "budget", {}).get("selected_retrieval_objects", [])]}
        selected = getattr(request.roles, role) if request.roles else None
        data = dict(schema_version=SCHEMA, metric_id=_get(metric, "metric_id"), route=route, role=role,
            reason="Repeating quantitative dimensional contract with actual selected IR table cells" if table else "Source capabilities or semantic grain require report understanding",
            contract_digest=_digest(contract), source_capability_digest=_digest(capabilities),
            provider=selected.provider if selected else request.semantic_provider,
            model=model_identity or (selected.model if selected else request.semantic_model))
        return ExecutionStrategy(**data, descriptor_digest=_digest(data))

    @staticmethod
    def checkpoint_matches(checkpoint, descriptor):
        return checkpoint.get("execution_strategy") == _dump(descriptor)
