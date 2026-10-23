"""Persistent, bounded report-fact Agent with explicit standalone compatibility.

Physical understanding, requirement mapping and navigation are separate tasks.
There is deliberately no automatic whole-table legacy fallback on failure.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

from esg_targeted.contracts import SemanticDecision
from esg_targeted.evidence.regions import EvidenceRegionCompiler
from esg_targeted.ids import safe_filename, stable_id
from esg_targeted.models.errors import ModelCancelled, ModelRunFailure
from esg_targeted.models.report_semantics import (
    INTERPRETATION_VERSION, MAP_INSTRUCTIONS, PLAN_INSTRUCTIONS, UNDERSTAND_INSTRUCTIONS,
    AgentPlan, ObjectMapping, TableUnderstanding, expand_understanding, mapping_template,
    parse_model_json, plan_template, project_interpretation, semantic_card, understanding_template,
    validate_mapping_payload, validate_understanding_payload, PlannerFrontierError,
    validate_plan_frontier, normalize_navigation_query,
)
from esg_targeted.models.source_facts import project_source_facts
from esg_targeted.report_facts import NEUTRAL_FIELD_DEFINITIONS, EvidenceGap, RequirementMapping, source_facts, adapt_direct_core_facts
from esg_targeted.io import read_json, write_json
from esg_targeted.results.deduplicator import ExactFactDeduplicator
from esg_targeted.storage.execution_supervisor import supervised_model_call
from esg_targeted.evidence.source_reading import document_reading_context
from esg_targeted.evidence.related_context import linked_explanatory_packet
from esg_targeted.runtime_budget import BudgetExceeded, ReportRuntimeBudget, task_input_fingerprint
from esg_targeted.results.stages import publish_stage
from esg_targeted.models.task_context import compact_task_context, complete_read_groups, repair_diagnostics, unshown_source_groups
from esg_targeted.standards.runtime_snapshot import metric_binding_identity
from esg_targeted.evidence.chart_support import CHART_READING_VERSION, POINT_TEXT_POLICY, chart_policy, legacy_chart_source
from esg_targeted.models.token_usage import resolve_token_usage
from esg_targeted.models.token_admission import admit_model_input, bind_runtime_admission
from esg_targeted.evidence.source_frames import (SOURCE_FRAME_VERSION, MAX_FRAME_ATTEMPTS, source_frames,
    pending_read_groups, mapping_cards, needs_text_frames, text_state, next_text_frame, verify_text_frame,
    record_text_frame, text_complete, reading_frontier_fingerprint)


class ProtocolFailure(ValueError):
    category = "protocol"

    def __init__(self, message, *, stage="schema"):
        super().__init__(message)
        self.stage = stage


# Versioned routing policies. Tests and checkpoint reuse must compare against
# these constants instead of repeating the literals.
REPORT_SOURCE_AGENT_VERSION = "report-fact-agent-v9"
IMPLICIT_AGENT_VERSION = "report-fact-agent-v3"


class DisclosureSession:
    def __init__(self, *, package, metrics, request, artifact_store, job_id, max_context_chars,
                 inventory=None, job_store=None, search_report=None, call_timeout_seconds=180,
                 execution_strategies=None):
        self.package, self.request = package, request
        self.selected_metrics = {metric.metric_id: metric for metric in metrics}
        self.execution_strategies = execution_strategies
        self.store, self.job_id, self.jobs = artifact_store, job_id, job_store
        self.inventory, self.search_report = inventory, search_report
        self.call_timeout = call_timeout_seconds
        self.topics = defaultdict(list)
        if package.format_version in {"2.0", "2.1"} and request.extraction_mode == "auto" and request.semantic_fill:
            for metric in metrics:
                if execution_strategies is None or execution_strategies.get(metric.metric_id, {}).get("route", "source_agent") == "source_agent":
                    self.topics[metric.disclosure_topic].append(metric)
        self.pools, self.sources, self.topic_results = {}, {}, {}
        self._mapping_evidence, self._mapping_failures = {}, {}
        self.topic_sizes = {topic: sum(m.disclosure_topic == topic for m in package.metrics) for topic in self.topics}
        # Report sessions share physical-source interpretation even when a
        # requirement is a numeric singleton. Semantic ownership must not depend
        # on how many siblings a standard package happens to define. Version the
        # routing policy so old direct-fill checkpoints cannot become accepted
        # report-source interpretations merely by resuming a job.
        self.report_source_route = bool(getattr(request, "report_session_id", None))
        self.agent = {"version": REPORT_SOURCE_AGENT_VERSION if self.report_source_route else IMPLICIT_AGENT_VERSION, "steps": [], "model_calls": 0,
                      "interpreted_objects": 0, "mapped_rows": 0, "planning_rounds": 0,
                      "executed_actions": 0, "navigation": {}, "planning_history": [],
                      "unresolved_metrics": [], "state": "planning"}
        if self.store.exists(self.job_id, "agent/state.json"):
            previous = self.store.read_json(self.job_id, "agent/state.json")
            if previous.get("version") == self.agent["version"]:
                self.agent = {**self.agent, **previous, "state": "resuming"}
        self.plan = {"version": "disclosure-plan-agent-v1", "mode": request.extraction_mode,
                     "routing_policy": "report_shared_sources_v1" if self.report_source_route else "standalone_compatible_v1",
                     "shared_object_budget": request.shared_object_budget,
                     "per_metric_gap_top_n": request.retrieval_object_top_n,
                     "agent_max_steps": getattr(request, "agent_max_steps", 8),
                     "topics": [], "relations": [r.model_dump(mode="json") for r in package.relations],
                     "projections": []}
        if execution_strategies is not None:
            self.plan["routing_policy"] = "compiled_evidence_strategy_v1"
            self.plan["execution_strategies"] = execution_strategies
        session_id = getattr(request, "report_session_id", None)
        from esg_targeted.budget_scope import package_model_budget
        self.runtime_budget = package_model_budget(self.store, request, job_id)
        self._current_calls, self._last_call_id = [], None
        self.document_context = document_reading_context(inventory) if inventory else []
        self._linked_context = []
        self._invalidate_legacy_chart_sources(include_semantic_versions=self.report_source_route)
        selected = {m.metric_id for m in metrics}
        for topic, members in self.topics.items():
            self.plan["topics"].append({"topic_id": topic, "metric_ids": [m.metric_id for m in members],
                "route": "report_fact_agent" if self._agent_topic(topic) else "per_metric",
                "roles": {m.metric_id: m.disclosure_role for m in members},
                "unselected_dependencies": sorted({d for m in members for d in m.depends_on} - selected)})
        self._save()

    def eligible(self, metric_id):
        if self.execution_strategies is not None and metric_id in self.execution_strategies:
            return self.execution_strategies[metric_id]["route"] == "source_agent"
        return any(self._agent_topic(topic) and any(m.metric_id == metric_id for m in members)
                   for topic, members in self.topics.items())

    def _agent_topic(self, topic):
        if self.execution_strategies is not None and topic in self.topics:
            return any(self.execution_strategies.get(m.metric_id, {}).get("route", "source_agent") == "source_agent"
                       for m in self.topics[topic])
        # Topics are populated only for modern auto-mode semantic extraction.
        # Explicit per_metric mode and legacy standalone contracts remain direct.
        return bool(topic in self.topics and (self.report_source_route or
            self.topic_sizes.get(topic, 0) > 1 or any(m.fact_grain for m in self.topics[topic])))

    def _save(self):
        self.store.write_json(self.job_id, "disclosures/plan.json", self.plan)
        self.store.write_json(self.job_id, "agent/state.json", self.agent)
        if self.jobs:
            current = self.jobs.get(self.job_id)
            self.jobs.update(self.job_id, summary={**(current.summary or {}), "agent": self.agent})

    def _event(self, stage, detail):
        if self.jobs:
            self.jobs.update(self.job_id, stage=stage)
            self.jobs.add_event(self.job_id, stage, "info", detail.get("message") or stage, detail)
        self._save()

    def expand(self, metric, packet, prepare_metric):
        members = self.topics.get(metric.disclosure_topic, [])
        if not self._agent_topic(metric.disclosure_topic):
            return packet
        topic = metric.disclosure_topic
        if topic not in self.pools:
            packets = [prepare_metric(m)[3] for m in sorted(members, key=lambda m: m.disclosure_role != "anchor")]
            objects = {}
            for source_packet in packets:
                for obj in source_packet.budget.get("selected_retrieval_objects", []):
                    entry = objects.setdefault(obj["id"], {**obj, "metrics": [], "groups": []})
                    entry["metrics"].append(source_packet.metric["metric_id"])
                    entry["groups"] = list(dict.fromkeys([*entry["groups"], *obj["selected_group_ids"]]))
            self.pools[topic] = {"packets": packets, "objects": objects, "members": members}
            plan = next(p for p in self.plan["topics"] if p["topic_id"] == topic)
            plan.update(unique_candidate_objects=len(objects), shared_objects=list(objects.values()),
                        per_metric_objects={p.metric["metric_id"]: p.budget.get("selected_retrieval_objects", []) for p in packets})
            self._save()
        pool = self.pools[topic]
        wanted = {g for p in pool["packets"] for g in p.allowed_group_ids}
        merged = self._include(packet, wanted, pool["packets"])
        explanatory_ids = {target for relation in self.package.relations
            if relation.relation_type.value == "explains" and metric.metric_id in relation.source_metric_ids
            for target in relation.target_metric_ids if target in self.selected_metrics}
        if explanatory_ids:
            related = [prepare_metric(self.selected_metrics[mid])[3] for mid in sorted(explanatory_ids)]
            merged = linked_explanatory_packet(merged, related, self.inventory)
        merged.budget.update(disclosure_topic=topic, search_completeness="bounded_agent_evidence")
        return merged

    def _include(self, packet, wanted, packets=()):
        spans = {s.span_id: s for p in [packet, *packets] for s in p.spans
                 if s.context_group_id in wanted or s.span_id in p.context_only_span_ids}
        # Once a physical object is chosen, understand the complete table rather
        # than the metric-specific target subset. This is not a qualification gate.
        if self.inventory:
            objects = {self._reading_key([s])[1] for s in spans.values()
                       if s.context_group_id in wanted}
            full_groups = {s.context_group_id for s in self.inventory.spans
                           if self._reading_key([s])[1] in objects}
            wanted = wanted | full_groups
            spans.update({s.span_id: spans.get(s.span_id, s) if spans.get(s.span_id, s).structural_context.get("reading_object_id") else s
                for s in self.inventory.spans if s.context_group_id in wanted})
        candidates = {c.candidate_id: c for p in [packet, *packets] for c in p.candidates if c.context_group_id in wanted}
        if self.inventory:
            candidates.update({c.candidate_id: c for c in self.inventory.candidates if c.context_group_id in wanted})
        if self.inventory:
            note_ids = {bid for span in spans.values() for bid in span.structural_context.get("footnote_block_ids", [])}
            spans.update({s.span_id: s for s in self.inventory.spans if note_ids.intersection(s.object_ids)})
            context_refs = {entry["span_id"] for entry in self.document_context}
            spans.update({s.span_id: s for s in self.inventory.spans if s.span_id in context_refs})
        context_ids = list(dict.fromkeys([*(sid for p in [packet, *packets] for sid in p.context_only_span_ids
                                        if sid in spans and spans[sid].context_group_id not in wanted),
                                        *(s.span_id for s in spans.values() if s.context_group_id not in wanted)]))
        return packet.model_copy(update={"spans": sorted(spans.values(), key=lambda s: (s.page_index, s.span_id)),
            "candidates": list(candidates.values()), "allowed_group_ids": sorted(wanted),
            "context_only_span_ids": context_ids,
            "alias_map": {**packet.alias_map,
                          "groups": {f"G{i}": g for i, g in enumerate(sorted(wanted), 1)},
                          "spans": {f"S{i}": sid for i, sid in enumerate(sorted(spans), 1)},
                          "candidates": {f"C{i}": cid for i, cid in enumerate(sorted(candidates), 1)},
                          "context": {f"L{i}": sid for i, sid in enumerate(context_ids, 1)}}}, deep=True)

    @staticmethod
    def _target_packet(packet):
        # Build a source-binding contract, NOT a model prompt. The model receives
        # the compact object view below, without legacy row/cell instructions.
        reverse_spans = {sid: alias for alias, sid in packet.alias_map.get("spans", {}).items()}
        reverse_groups = {gid: alias for alias, gid in packet.alias_map.get("groups", {}).items()}
        columns = sorted({s.structural_context.get("col_index") for s in packet.spans
                          if s.span_type == "table_cell" and isinstance(s.structural_context.get("col_index"), int)})
        targets = EvidenceRegionCompiler._target_value_cells(packet.spans, packet.candidates, columns, reverse_spans, reverse_groups)
        context = json.loads(packet.model_context)
        context["region"] = {"target_value_cells": targets}
        return packet.model_copy(update={"model_context": json.dumps(context, ensure_ascii=False),
            "alias_map": {**packet.alias_map, "target_cells": {t["id"]: t["cell_id"] for t in targets}}}, deep=True)

    def _objects(self, packet):
        by_group = defaultdict(list)
        for span in packet.spans:
            if span.context_group_id in packet.allowed_group_ids:
                by_group[span.context_group_id].append(span)
        reverse_groups = {gid: alias for alias, gid in packet.alias_map["groups"].items()}
        targets = json.loads(packet.model_context)["region"]["target_value_cells"]
        cells_by_group = defaultdict(list)
        for cell in targets:
            cells_by_group[cell["group"]].append(cell)
        objects = {}
        for gid, spans in by_group.items():
            kind, object_id, image = self._reading_key(spans)
            entry = objects.setdefault(object_id, {"object_id": object_id, "kind": kind, "image_path": image or None, "rows": [], "columns": {}})
            explanatory_metrics = {mid for span in spans for mid in span.structural_context.get("linked_explanatory_metrics", [])}
            if explanatory_metrics:
                entry["linked_explanatory_metrics"] = sorted(set(entry.get("linked_explanatory_metrics", [])) | explanatory_metrics)
            cells = cells_by_group[reverse_groups[gid]]
            row_spans = [s for s in spans if s.span_type in {"table_row", "block", "page_text", "figure_text"}]
            label = next((c["row_label"] for c in cells if c.get("row_label")), "")
            text = next((s.text for s in row_spans), spans[0].text if spans else "")
            row = {"group": reverse_groups[gid], "group_id": gid, "label": label,
                   "locators": [loc.model_dump(mode="json") for span in row_spans for loc in span.locators],
                   "text": text[:5000] if kind != "table" or not cells else "",
                   "page": min(s.page_index for s in spans), "cells": [],
                   "image_path": EvidenceRegionCompiler._region_key(spans)[2] or None}
            row["row_index"] = next((s.structural_context["row_index"] for s in spans
                if isinstance(s.structural_context.get("row_index"), int)), None)
            if row_spans:
                row["span_id"] = row_spans[0].span_id
            row["support_segments"] = [{"span_id": s.span_id, "text": s.text,
                "locators": [loc.model_dump(mode="json") for loc in s.locators]}
                for s in spans if s.span_type in {"block", "sentence", "page_text", "table_row"} or
                    s.structural_context.get("literal_policy") == POINT_TEXT_POLICY]
            if kind == "figure":
                structure = next((s.structural_context for s in spans if s.span_type == "figure_text"), {})
                row.update(grid_reliable=False, chart_source_policy=chart_policy(structure))
                entry.setdefault("chart_source_policy", chart_policy(structure))
                entry.setdefault("chart_spec", structure.get("chart_spec"))
            for c in cells:
                cell_span = next((span for span in spans if span.span_id == c.get("span_id") or
                                  (c["cell_id"] in span.object_ids and span.span_type == "table_cell")), None)
                logical_column = cell_span.structural_context.get("logical_col_index") if cell_span else None
                column = f"c{logical_column if logical_column is not None else c.get('col_index')}" if kind == "table" else c["id"]
                entry["columns"].setdefault(column, {"column": column, "headers": c.get("column_headers", []),
                                                     "alignment_uncertain": bool(c.get("header_alignment_uncertain"))})
                row["cells"].append({"target_cell": c["id"], "cell_id": c["cell_id"], "column": column,
                                     "raw": c["visible_value"], "unit": c.get("unit_context"),
                                     "span_id": c.get("span_id") or (cell_span.span_id if cell_span else None),
                                     "candidate_id": c.get("candidate_id"), "source_kind": c.get("source_kind", "table"),
                                     "char_start": c.get("char_start"), "char_end": c.get("char_end"), "value_forms": c.get("value_forms", []),
                                     "column_headers": c.get("column_headers", []),
                                     "section_headers": c.get("section_headers", []),
                                     "locators": [loc.model_dump(mode="json") for loc in (cell_span.locators if cell_span else [])],
                                     "grid_reliable": bool(cell_span and cell_span.structural_context.get("literal_policy") == POINT_TEXT_POLICY) if kind == "figure" else
                                         cell_span.structural_context.get("evidence_capabilities", {}).get("cell_grid", True) if cell_span else kind != "figure"})
                if not row.get("unit"):
                    row["unit"] = c.get("unit_context")
            entry["rows"].append(row)
        for obj in objects.values():
            obj["rows"].sort(key=lambda row: (row["page"], row["row_index"] is None,
                row["row_index"] if row["row_index"] is not None else 0, row["group_id"]))
            if obj["kind"] == "figure":
                # These are output slots for independently read, pending visual
                # candidates, never physical columns or prefilled chart values.
                for index in range(1, 9):
                    obj["columns"].setdefault(f"visual-slot-{index}", {"column": f"visual-slot-{index}", "headers": [], "alignment_uncertain": False})
            obj["columns"] = list(obj["columns"].values())
            obj["summary"] = " | ".join((r["label"] or r["text"])[:130] for r in obj["rows"][:10])
            obj["physical_object_ids"] = sorted({s.structural_context.get("table_id") or s.structural_context.get("figure_id")
                for s in packet.spans if self._reading_key([s])[1] == obj["object_id"]
                and (s.structural_context.get("table_id") or s.structural_context.get("figure_id"))})
            if not obj["physical_object_ids"]:
                obj["physical_object_ids"] = [obj["object_id"]] if obj["kind"] == "block" else sorted({s.structural_context.get("page_id") or loc.ir_object_id for s in packet.spans
                    if self._reading_key([s])[1] == obj["object_id"] for loc in s.locators
                    if s.structural_context.get("page_id") or loc.ir_object_type == "page"})
            obj["linked_context"] = []
            if self.inventory:
                pages = {r["page"] for r in obj["rows"]}
                object_spans = [s for s in self.inventory.spans if self._reading_key([s])[1] == obj["object_id"]]
                footnotes = {bid for span in object_spans for bid in span.structural_context.get("footnote_block_ids", [])}
                notes = [s for s in self.inventory.spans if s.span_type == "block"
                         and (footnotes.intersection(s.object_ids) or (s.page_index in pages and not s.has_table_structure))]
                notes.sort(key=lambda span: (not bool(footnotes.intersection(span.object_ids)), span.page_index, span.span_id))
                obj["linked_context"] = [{"span_id": s.span_id, "page": s.page_index, "text": s.text[:1800],
                    "locators": [loc.model_dump(mode="json") for loc in s.locators]} for s in notes[:6]]
        return objects

    @staticmethod
    def _reading_key(spans):
        explicit = next((s.structural_context for s in spans
            if s.structural_context.get("reading_object_id")), None)
        if explicit:
            return explicit.get("reading_object_type", "narrative"), explicit["reading_object_id"], explicit.get("image_path") or ""
        logical = next((span.structural_context for span in spans if span.structural_context.get("logical_table_id")), None)
        if logical:
            return "table", logical["logical_table_id"], logical.get("logical_image_path") or ""
        return EvidenceRegionCompiler._region_key(spans)

    def _cards(self, members):
        metrics = {m.metric_id: m for m in self.package.metrics}
        cards = []
        for metric in members:
            card = semantic_card(metric, [e for e in self.package.elements if e.metric_id == metric.metric_id], metric.confusable_metric_ids)
            card["sibling_distinctions"] = [{"metric_id": sid, "definition": metrics[sid].description}
                                           for sid in metric.confusable_metric_ids[:8] if sid in metrics]
            card["relations"] = [{"type": r.relation_type.value, "sources": r.source_metric_ids,
                                  "targets": r.target_metric_ids, "members": r.member_metric_ids,
                                  "identity_axes": r.identity_axes, "description": r.description}
                                 for r in self.package.relations if metric.metric_id in
                                 [*r.source_metric_ids, *r.target_metric_ids, *r.member_metric_ids]]
            cards.append(card)
        return cards

    def _navigation_cards(self, members):
        """Navigation selects a source; binding and full semantics follow later."""
        return [{key: card[key] for key in ("metric_id", "source_datapoint_id", "clause_kind",
                    "extension_id", "name", "positive_definition", "requested_scope", "identity_axes",
                    "interpretation_guidance", "exclusions", "semantic_distinctions", "sibling_distinctions")}
                for card in self._cards(members)]

    @staticmethod
    def _object_fingerprints(objects):
        # Aliases and retrieval scores can change without introducing evidence.
        # Raw text, exact cells, headers and real source IDs define novelty.
        return {oid: stable_id("navigation-evidence", oid, [{
            "group_id": row["group_id"], "text": row.get("text"), "label": row.get("label"),
            "page": row.get("page"), "cells": [{key: cell.get(key) for key in
                ("cell_id", "raw", "unit", "column_headers", "section_headers", "span_id")}
                for cell in sorted(row.get("cells", []), key=lambda cell: str(cell.get("cell_id")))]}
                for row in sorted(obj["rows"], key=lambda row: row["group_id"])],
            [{key: context.get(key) for key in ("span_id", "page", "text")}
                for context in obj.get("linked_context", [])]) for oid, obj in objects.items()}

    def _navigation_frontier(self, objects, result, nav, inspect_count, blocked_objects):
        unread = sorted(oid for oid in objects if pending_read_groups(objects[oid], self.sources.get(oid)) and oid not in blocked_objects)
        pending = sorted(oid for oid in objects if oid in self.sources and oid not in blocked_objects and
            self._mapping_failures.get((self._current_topic, oid, self._mapping_fingerprint(self.sources[oid])), 0) < 2 and
            (oid not in result["mappings"] or self._mapping_evidence.get((self._current_topic, oid)) != self._mapping_fingerprint(self.sources[oid])))
        contexts = sorted(oid for oid in objects if oid in self.sources and oid not in blocked_objects and
            bool(pending_read_groups(objects[oid], self.sources[oid])))
        repairs = sorted(oid for oid, obj in objects.items() if oid not in blocked_objects and (
            any(not cell.get("grid_reliable", True) for row in obj["rows"] for cell in row.get("cells", [])) or
            any(column.get("alignment_uncertain") for column in obj["columns"]) or
            any(g.get("kind") in {"header", "source"} for g in self.sources.get(oid, {}).get("gaps", []))))
        read_required = bool(unread and (inspect_count == 0 or nav.get("search_requires_read")))
        mapping_required = any(oid not in result["mappings"] for oid in pending)
        actions = {}
        if unread:
            actions["inspect_object"] = {"object_ids": unread, "group_ids": {
                oid: pending_read_groups(objects[oid], self.sources.get(oid)) for oid in unread}, "cell_ids": {}}
        if pending:
            actions["map_object"] = {"object_ids": pending}
        if contexts:
            # Only unresolved fragment IDs belong in navigation. Whole-table
            # row/cell authorities stay in the selected source-reading task.
            groups = {oid: pending_read_groups(objects[oid], self.sources[oid]) for oid in contexts}
            actions["read_object_context"] = {"object_ids": contexts, "group_ids": groups, "cell_ids": {}}
        if repairs:
            actions["request_local_repair"] = {"object_ids": repairs, "group_ids": {}}
        if self.search_report and not read_required and not mapping_required:
            actions["search_report"] = {}
        if not read_required and not mapping_required:
            actions["finish"] = {}
        return {"version": "navigation-frontier-v1", "actions": actions, "read_required": read_required,
                "default_search_metric_id": self.pools[self._current_topic]["members"][0].metric_id,
                "metric_ids": [member.metric_id for member in self.pools[self._current_topic]["members"]],
                "seen_queries": [{"metric_id": row["metric_id"], "query": row["query"]}
                    for row in nav.get("seen_queries", [])],
                "mapping_required": mapping_required,
                "reason": "Choose one unread object before further search" if read_required else
                    "Map the preserved report meaning to this topic before searching again" if mapping_required else
                    "Choose a source, an unresolved context, or a genuinely new gap query"}

    def _navigation_catalog(self, objects, readable_ids, packet):
        scores = {hit.span_id: hit.final_score for hit in packet.retrieval_hits}
        catalog = []
        for oid in readable_ids:
            obj = objects[oid]
            def row_score(row):
                refs = [s.get("span_id") for s in row.get("support_segments", [])] + [c.get("span_id") for c in row.get("cells", [])]
                return max((scores.get(ref, 0) for ref in refs), default=0)
            ranked = sorted(obj["rows"], key=lambda row: -row_score(row))
            samples = list(dict.fromkeys((row["label"] or row["text"])[:160] for row in ranked))[:8]
            unread_menu = []
            for row in ranked:
                if row["group_id"] not in pending_read_groups(obj, self.sources.get(oid)):
                    continue
                item = {"group_id": row["group_id"], "label": (row["label"] or row["text"])[:100],
                        "cell_count": len(row.get("cells", []))}
                if needs_text_frames(row):
                    state = (self.sources.get(oid) or {}).get("text_coverage", {}).get(row["group_id"])
                    frame = next_text_frame(row, state)["source_text_frame"]
                    item["next_text_window"] = {key: frame[key] for key in ("source_ref", "start", "end", "total_length", "frame_id")}
                unread_menu.append(item)
            catalog.append({"object_id": oid, "kind": obj["kind"], "summary": " | ".join(samples),
                "row_count": len(obj["rows"]), "cell_count": sum(len(r.get("cells", [])) for r in obj["rows"]),
                "page_indices": sorted({r["page"] for r in obj["rows"]}), "image_available": bool(obj.get("image_path")),
                "already_understood": oid in self.sources,
                "linked_explanatory_metrics": obj.get("linked_explanatory_metrics", []),
                "unread_group_menu": unread_menu,
                "reading_policy": SOURCE_FRAME_VERSION, "max_rows_per_read": 4})
        return sorted(catalog, key=lambda item: not bool(item.get("linked_explanatory_metrics")))

    @staticmethod
    def _mapping_fingerprint(source):
        return stable_id("mapping-evidence", source.get("version"), source.get("rows"), source.get("gaps"))

    def _stage(self):
        if self.store.exists(self.job_id, "input/standard-package.json"):
            publish_stage(self.store, self.job_id, self.request, self.package)

    def _retired_source_ids(self):
        retired = set(self.store.read_json(self.job_id, "agent/source-invalidations.json").get("source_fact_ids", [])) if self.store.exists(self.job_id, "agent/source-invalidations.json") else set()
        if self.request.report_session_id:
            shared = self.store.report_session_path(self.request.report_session_id) / "source-invalidations.json"
            if shared.is_file():
                retired |= set(read_json(shared).get("source_fact_ids", []))
        return retired

    def _invalidate_legacy_chart_sources(self, *, include_semantic_versions=False):
        """Preserve historical caches; retire incompatible mutable candidates.

        The chart-only compatibility entry point preserves current table reads.
        Report v8 explicitly rejects all prior neutral interpretation versions.
        """
        roots = [self.store.job_dir(self.job_id)]
        if self.request.report_session_id:
            roots.append(self.store.report_session_dir(self.request.report_session_id))
        retired = set()
        for root in roots:
            source_dir = root / ("agent/sources" if root == roots[0] else "sources")
            for path in source_dir.glob("*.json"):
                source = read_json(path)
                legacy_semantics = include_semantic_versions and source.get("version") != INTERPRETATION_VERSION
                if legacy_chart_source(source) or legacy_semantics:
                    old_ids = [f["source_fact_id"] for f in source.get("facts", [])]
                    retired.update(old_ids)
                    history = root / ("agent/source-history" if root == roots[0] else "source-history")
                    write_json(history / f"{stable_id('chart-policy-retired', source.get('source_key'), source)}.json",
                        {**source, "interpretation_status": "superseded", "superseded_source_fact_ids": old_ids,
                         "superseded_reason": "source_interpretation_policy_changed" if legacy_semantics else "chart_reading_policy_changed",
                         "replacement_interpretation_version": INTERPRETATION_VERSION,
                         "replacement_chart_reading_policy": CHART_READING_VERSION})
        if retired:
            for root in roots:
                path = root / ("agent/source-invalidations.json" if root == roots[0] else "source-invalidations.json")
                previous = read_json(path) if path.is_file() else {"source_fact_ids": []}
                write_json(path, {**previous, "source_fact_ids": sorted(set(previous["source_fact_ids"]) | retired),
                    "chart_reading_policy": CHART_READING_VERSION})

    def _call(self, model, *, tool, context, instructions, template, image_path=None, max_tokens=2048, cancel_check=None):
        prompt_context = compact_task_context(context)
        serialized = json.dumps({"context": prompt_context, "contract": template, "instructions": instructions}, ensure_ascii=False)
        input_chars = len(serialized)
        admission = admit_model_input(model, context=prompt_context, instructions=instructions,
            template=template, image_path=image_path)
        call_scope = {"context": context, "contract": template, "instructions": instructions,
                      "provider": getattr(model, "provider_name", getattr(model, "provider", self.request.semantic_provider)),
                      "model": str(getattr(model, "model_id", getattr(model, "model_path", "unknown")))}
        try:
            reservation = self.runtime_budget.begin_call(tool, input_tokens=admission.tokens,
                max_output_tokens=max_tokens, scope=task_input_fingerprint(tool, call_scope), is_format_repair=tool == "repair_format",
                input_token_basis=admission.basis, input_admission=admission.to_dict())
        except BudgetExceeded as exc:
            self.agent["budget_boundary"] = exc.boundary
            raise
        self.agent.pop("budget_boundary", None)
        self._last_call_id = reservation["call_id"]
        call = {"attempt": self.agent["model_calls"] + 1, "tool": tool, "route": tool, "budget_call_id": reservation["call_id"],
                "provider": getattr(model, "provider_name", getattr(model, "provider", self.request.semantic_provider)),
                "role": "semantic_brain" if self.request.roles else "semantic",
                "model_id": str(getattr(model, "model_id", getattr(model, "model_path", "unknown"))),
                "visual_used": bool(image_path), "object_id": context.get("object_id"), "phase": "started",
                "started_at": datetime.now(timezone.utc).isoformat()}
        self.agent["model_calls"] += 1
        self.agent["current_call"] = dict(call)
        relative = f"agent/calls/{call['attempt']:04d}-{safe_filename(tool)}.json"
        self.store.write_json(self.job_id, relative, {**call, "input": context, "template": template})
        self._event(tool, {**call, "message": f"{tool}: {context.get('object_id') or context.get('topic_id', '')}"})
        start = time.monotonic()
        last_progress = [0.0, None]
        def progress(details):
            if self.jobs:
                self.jobs.heartbeat(self.job_id)
            if details.get("phase") != last_progress[1] or time.monotonic() - last_progress[0] >= 5:
                last_progress[:] = [time.monotonic(), details.get("phase")]
                self.agent["current_call"] = {**call, **details, "elapsed_seconds": round(time.monotonic() - start, 3)}
                self._save()
        try:
            kwargs = dict(context=prompt_context, instructions=instructions, template=template,
                          image_path=image_path, max_tokens=max_tokens, cancel_check=cancel_check, progress_callback=progress)
            if self.jobs:
                with supervised_model_call(self.jobs.path, self.job_id, tool, timeout_seconds=self.call_timeout):
                    with bind_runtime_admission(model, self.runtime_budget, reservation["call_id"]):
                        raw, telemetry = model.generate_structured(**kwargs)
            else:
                with bind_runtime_admission(model, self.runtime_budget, reservation["call_id"]):
                    raw, telemetry = model.generate_structured(**kwargs)
            call.update(telemetry, raw_output=raw, phase="completed")
            usage = resolve_token_usage(telemetry, input_reservation=admission.tokens, input_reservation_basis=admission.basis,
                                        output_reservation=max_tokens, raw_output=raw)
            call.update(usage.to_dict())
            self.runtime_budget.finish_call(reservation["call_id"], **usage.finish_kwargs(), outcome="returned")
        except Exception as exc:
            call.update({**getattr(exc, "telemetry", {}), "phase": "failed", "error": str(exc),
                         "failure_category": getattr(exc, "category", "runtime"), "raw_output": getattr(exc, "raw_output", ""),
                         "cleaned_output": getattr(exc, "cleaned_output", "")})
            usage = resolve_token_usage(getattr(exc, "telemetry", {}), input_reservation=admission.tokens, input_reservation_basis=admission.basis,
                                        output_reservation=max_tokens, raw_output=getattr(exc, "raw_output", None))
            call.update(usage.to_dict())
            self.runtime_budget.finish_call(reservation["call_id"], **usage.finish_kwargs(),
                outcome="error", failure_signature=f"{call['failure_category']}:{str(exc)[:400]}")
            raise
        finally:
            call["elapsed_seconds"] = round(time.monotonic() - start, 3)
            call["input_chars"] = input_chars
            self.store.write_json(self.job_id, relative, {**call, "input": context, "prompt_input": prompt_context, "template": template})
            self._current_calls.append({**call, "artifact": relative})
            self.agent["current_call"] = {k: v for k, v in call.items() if k not in {"raw_output", "cleaned_output"}}
            self.agent["runtime_budget"] = self.runtime_budget.snapshot()
            self._event(tool, {k: v for k, v in call.items() if k not in {"raw_output", "cleaned_output"}})
        return raw

    def _validation(self, issues=()):
        if self._last_call_id:
            signature = "; ".join(f"{getattr(i, 'stage', 'semantic')}:{getattr(i, 'path', '')}:{getattr(i, 'message', str(i))}" for i in issues)
            self.runtime_budget.record_validation(self._last_call_id, outcome="error" if issues else "success", failure_signature=signature)

    def _understand(self, obj, model, cancel_check, *, group_ids=None, cell_ids=None, context_kind=None):
        object_id = obj["object_id"]
        linked_context = obj.get("linked_context", self._linked_context)
        canonical_rows = [{k: v for k, v in row.items() if k not in {"group", "cells"}} | {"cells": sorted(
            [{k: v for k, v in cell.items() if k != "target_cell"} for cell in row.get("cells", [])],
            key=lambda cell: str(cell.get("cell_id")))} for row in sorted(obj["rows"], key=lambda row: row["group_id"])]
        identity = {"document_id": self.inventory.document_id if self.inventory else getattr(self, "_document_id", "unknown"),
            "ir_run_id": self.inventory.ir_run_id if self.inventory else self.request.ir_run_id,
            "ir_revision": self.inventory.ir_revision if self.inventory else getattr(self, "_ir_revision", 1),
            "object_id": object_id, "interpretation_version": INTERPRETATION_VERSION,
            "neutral_field_digest": stable_id('neutral-fields', NEUTRAL_FIELD_DEFINITIONS),
            "provider": getattr(model, "provider_name", getattr(model, "provider", self.request.semantic_provider)),
            "model": str(getattr(model, "model_id", getattr(model, "model_path", "unknown"))),
            "evidence_signature": stable_id("object-evidence", object_id, obj.get("kind"), obj.get("columns"), canonical_rows, linked_context, self.document_context),
            "visual_enabled": self.request.visual_fallback}
        if obj.get("kind") == "figure":
            identity["chart_reading_policy"] = CHART_READING_VERSION
        source_key = stable_id("source", identity)
        path = f"agent/sources/{safe_filename(object_id)}.json"
        session_id = getattr(self.request, "report_session_id", None)
        shared_path = (self.store.report_session_dir(session_id) / "sources" / f"{source_key}.json") if session_id else None
        saved = self.sources.get(object_id)
        if not saved or saved.get("source_identity") != identity:
            saved = read_json(shared_path) if shared_path and shared_path.is_file() else (
                self.store.read_json(self.job_id, path) if self.store.exists(self.job_id, path) else None)
        if saved and (saved.get("version") != INTERPRETATION_VERSION or saved.get("source_identity") != identity):
            saved = None
        explicit_read = bool(group_ids or cell_ids or context_kind)
        if saved and saved.get("complete") and not explicit_read:
            self.sources[object_id] = saved
            self.store.write_json(self.job_id, path, saved)
            return saved
        rows = list(saved.get("rows", [])) if saved else []
        processed = set(saved.get("processed_groups", [])) if saved else set()
        blocked_read_groups = set(saved.get("blocked_read_groups", [])) if saved else set()
        read_attempts = dict(saved.get("read_attempts", {})) if saved else {}
        text_coverage = dict(saved.get("text_coverage", {})) if saved else {}
        gaps = list(saved.get("gaps", [])) if saved else []
        used_visual = bool(saved and saved.get("visual_used"))
        def persist():
            pending = [r["group_id"] for r in obj["rows"] if r["group_id"] not in processed]
            current_gaps = [g for g in gaps if g.get("kind") != "uninterpreted_row" and g.get("status", "open") != "resolved"]
            if pending:
                current_gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "rows", pending), kind="uninterpreted_row",
                    object_id=object_id, group_ids=pending, reason="Uninterpreted source rows: " + ", ".join(pending), next_action="inspect_object").model_dump(mode="json"))
            source = {"version": INTERPRETATION_VERSION, "source_key": source_key, "source_identity": identity,
                      "object_id": object_id, "rows": rows, "linked_context": linked_context, "gaps": current_gaps,
                      "errors": [g["reason"] for g in current_gaps], "processed_groups": sorted(processed),
                      "blocked_read_groups": sorted(blocked_read_groups), "read_attempts": read_attempts,
                      "text_coverage": text_coverage,
                      "source_frame_version": SOURCE_FRAME_VERSION,
                      "complete": not current_gaps and not pending, "visual_used": bool(used_visual)}
            source["facts"] = source_facts(source, identity, obj)
            source["quality_summary"] = {quality: sum(f.get("quality_status") == quality for f in source["facts"])
                                         for quality in ("grounded", "needs_confirmation", "unbound")}
            source["identity_questions"] = [{"source_fact_id": f["source_fact_id"], "source_row_id": f["source_row_id"], **issue}
                for f in source["facts"] for issue in f.get("quality_issues", []) if issue.get("code", "").startswith(("identity", "period", "unit"))]
            prior = self.sources.get(object_id) or saved
            if prior and prior.get("facts") != source["facts"]:
                old_by_id = {f["source_fact_id"]: f for f in prior.get("facts", [])}
                new_ids = {f["source_fact_id"] for f in source["facts"]}
                retired = sorted(old_by_id.keys() - new_ids)
                if retired:
                    self.store.write_json(self.job_id, f"agent/source-history/{stable_id('revision', prior.get('source_key'), retired)}.json",
                        {**prior, "interpretation_status": "superseded", "superseded_source_fact_ids": retired})
                    previous = self.store.read_json(self.job_id, "agent/source-invalidations.json") if self.store.exists(self.job_id, "agent/source-invalidations.json") else {"source_fact_ids": []}
                    previous["source_fact_ids"] = sorted((set(previous["source_fact_ids"]) | set(retired)) - new_ids)
                    self.store.write_json(self.job_id, "agent/source-invalidations.json", previous)
                    if session_id:
                        shared_invalidations = self.store.report_session_dir(session_id) / "source-invalidations.json"
                        shared = read_json(shared_invalidations) if shared_invalidations.is_file() else {"source_fact_ids": []}
                        shared["source_fact_ids"] = sorted((set(shared["source_fact_ids"]) | set(retired)) - new_ids)
                        write_json(shared_invalidations, shared)
            self.sources[object_id] = source
            self.store.write_json(self.job_id, path, source)
            if shared_path:
                write_json(shared_path, source)
            self._stage()
            return source
        pending_rows = [r for r in obj["rows"] if explicit_read or r["group_id"] not in processed]
        if self.report_source_route:
            pending_rows = [r for r in pending_rows if r["group_id"] not in blocked_read_groups]
            if not cell_ids and context_kind is None:
                seen_cells = {r["group_id"]: {cell.get("cell_id") for cell in r.get("cells", []) if cell.get("cell_id")}
                              for r in rows if r["kind"] == "quantitative"}
                pending_rows = [{**r, "cells": [c for c in r.get("cells", []) if c.get("cell_id") not in seen_cells.get(r["group_id"], set())]}
                                if r.get("cells") and r["group_id"] in seen_cells else r for r in pending_rows]
        if group_ids:
            allowed = {r["group_id"] for r in obj["rows"]} | {r["group"] for r in obj["rows"]}
            if not set(group_ids) <= allowed:
                raise ProtocolFailure("Context read referenced an unknown source row", stage="reference")
            pending_rows = [r for r in pending_rows if r["group_id"] in group_ids or r["group"] in group_ids]
        if cell_ids:
            allowed = {c["cell_id"] for r in obj["rows"] for c in r.get("cells", [])} | {c["target_cell"] for r in obj["rows"] for c in r.get("cells", [])}
            if not set(cell_ids) <= allowed:
                raise ProtocolFailure("Context read referenced an unknown physical cell", stage="reference")
            pending_rows = [{**r, "cells": [c for c in r.get("cells", []) if c["cell_id"] in cell_ids or c["target_cell"] in cell_ids]}
                            for r in pending_rows if any(c["cell_id"] in cell_ids or c["target_cell"] in cell_ids for c in r.get("cells", []))]
        originals = {row["group_id"]: row for row in obj["rows"]}
        window_rows = []
        for row in pending_rows:
            if not needs_text_frames(originals[row["group_id"]]):
                window_rows.append(row)
                continue
            state = text_state(originals[row["group_id"]], text_coverage.get(row["group_id"]))
            text_coverage[row["group_id"]] = state
            if any(channel.get("ambiguous_reference") for channel in state["channels"].values()):
                gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "ambiguous_text_reference", row["group_id"]),
                    kind="context", object_id=object_id, group_ids=[row["group_id"]],
                    reason="source_text_reference_conflict: one original span identifies different texts; ambiguous channels remain unread",
                    next_action="finish").model_dump(mode="json"))
            if any(not channel.get("source_ref") for channel in state["channels"].values()):
                gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "unverifiable_text_span", row["group_id"]),
                    kind="source", object_id=object_id, group_ids=[row["group_id"]],
                    reason="source_text_span_unverifiable: bounded row text has no original reference proving its complete source",
                    next_action="finish").model_dump(mode="json"))
            window = next_text_frame(originals[row["group_id"]], state)
            if window is not None:
                window_rows.append(window)
        pending_rows = window_rows
        frames = source_frames(pending_rows, max_rows=4 if self.report_source_route else 8,
                               max_chars=8000 if self.report_source_route else 14000)
        # One navigator action chooses one frame. Remaining physical rows stay
        # in the frontier and coverage denominator, never auto-drained here.
        if self.report_source_route:
            frames = frames[:1]
        chunks = [(obj.get("image_path") or part[0].get("image_path"), part) for part in frames]
        attempts = 0
        while chunks:
            if self.report_source_route and attempts >= MAX_FRAME_ATTEMPTS:
                failed_groups = [r["group_id"] for _, remaining in chunks for r in remaining]
                for _, remaining in chunks:
                    for failed in remaining:
                        if failed.get("source_text_frame"):
                            record_text_frame(text_coverage[failed["group_id"]], failed["source_text_frame"], success=False)
                        else:
                            blocked_read_groups.add(failed["group_id"])
                gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "frame_attempt_budget", failed_groups),
                    kind="generation_incomplete", object_id=object_id, group_ids=failed_groups,
                    reason="Bounded source-frame recovery exhausted; other source frames remain available", next_action="finish").model_dump(mode="json"))
                persist()
                break
            part_image, part_rows = chunks.pop(0)
            attempts += 1
            for row in part_rows:
                read_attempts[row["group_id"]] = read_attempts.get(row["group_id"], 0) + 1
            supplied_image = part_image if part_image and self.request.visual_fallback and Path(part_image).is_file() else None
            context = {**obj, "rows": part_rows, "field_definitions": NEUTRAL_FIELD_DEFINITIONS,
                       "bound_source_identity": {key: identity[key] for key in ("document_id", "ir_run_id", "ir_revision")},
                       "linked_context": linked_context, "document_context": self.document_context,
                       "requested_context_kind": context_kind or "bounded_source_frame",
                       "visual_evidence_supplied": bool(supplied_image),
                       "visual_evidence_group_ids": [r["group_id"] for r in part_rows] if supplied_image else [],
                       "read_scope": {"groups": [r["group"] for r in part_rows], "image_role": "context_only_use_only_supplied_rows"}}
            source_kind = obj.get("kind", "unknown")
            context["source_capabilities"] = {"source_kind": source_kind,
                "column_identity": "physical_table_grid" if source_kind == "table" else "occurrence_ids_only",
                "numerical_reading": "requires_supplied_visual_confirmation" if source_kind == "figure" else "exact_supplied_occurrences",
                "chart_payload_role": "interpretation_hint_not_independent_point_evidence" if source_kind == "figure" else "not_applicable"}
            if source_kind == "figure":
                context["source_capabilities"].update(numerical_reading="exact_supported_point_text_or_pending_independent_visual_read",
                    independent_visual_quality="needs_confirmation", chart_reading_policy=CHART_READING_VERSION)
            context.pop("image_path", None)
            context.pop("summary", None)
            try:
                raw = self._call(model, tool="read_object_context" if context_kind else "source_understanding", context=context,
                    instructions=UNDERSTAND_INSTRUCTIONS, template=understanding_template(context=context),
                    image_path=supplied_image, max_tokens=2400, cancel_check=cancel_check)
                understanding, issues = validate_understanding_payload(raw, context)
                if any(i.stage in {"syntax", "schema"} for i in issues):
                    self._validation(issues)
                    # Preserve independently valid siblings. Repair only named
                    # bad groups when every error can be assigned to a row.
                    bad_groups = set()
                    try:
                        bad_entries = json.loads(raw).get("rows", [])
                        for issue in issues:
                            match = re.fullmatch(r"rows\[(\d+)\](?:\..*)?", issue.path)
                            if not match:
                                bad_groups = set()
                                break
                            bad_groups.add(bad_entries[int(match[1])]["group"])
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        bad_groups = set()
                    repair_context = {**context, "rows": [row for row in part_rows if not bad_groups or row["group"] in bad_groups]}
                    repaired_raw = self._repair(raw, issues, model=model, task="source_understanding", context=repair_context,
                        template=understanding_template(context=repair_context), cancel_check=cancel_check)
                    repaired, repair_issues = validate_understanding_payload(repaired_raw, repair_context)
                    combined = {}
                    for collection in ("rows", "columns", "cell_overrides"):
                        combined[collection] = list({json.dumps(item.model_dump(), sort_keys=True, ensure_ascii=False): item
                            for item in [*getattr(understanding, collection), *getattr(repaired, collection)]}.values())
                    understanding = TableUnderstanding(**combined,
                        missing_context=list(dict.fromkeys([*understanding.missing_context, *repaired.missing_context])))
                    issues = repair_issues
                expanded, errors = expand_understanding(understanding, context)
                unbound = [f"semantic:source proof unbound for {r['group_id']}" for r in expanded if r.get("quality_status") == "unbound" or
                           (r["kind"] == "quantitative" and any(c.get("quality_status") == "unbound" for c in r.get("cells", [])))]
                self._validation([*issues, *unbound, *errors])
                understood = complete_read_groups(expanded)
                frame_understood = set(understood)
                window_groups = {r["group_id"] for r in part_rows if r.get("source_text_frame")}
                for window in part_rows:
                    if not window.get("source_text_frame"):
                        continue
                    gid = window["group_id"]
                    success = gid in frame_understood and verify_text_frame(originals[gid], window)
                    record_text_frame(text_coverage[gid], window["source_text_frame"], success=success)
                    if success:
                        bound_cells = {cell["cell_id"] for reading in expanded if reading["group_id"] == gid and reading["kind"] == "quantitative"
                            for cell in reading.get("cells", []) if cell.get("cell_id") and cell.get("physical_sources") and cell.get("quality_status") != "unbound"}
                        text_coverage[gid]["presented_cell_ids"] = sorted(set(text_coverage[gid]["presented_cell_ids"]) |
                            bound_cells)
                        if any(reading["group_id"] == gid and reading["kind"] == "context" for reading in expanded):
                            text_coverage[gid]["structure_seen_cell_ids"] = sorted(set(text_coverage[gid]["structure_seen_cell_ids"]) |
                                {cell["cell_id"] for cell in window.get("cells", []) if cell.get("cell_id")})
                    if not success:
                        gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "window_binding", window["source_text_frame"]["frame_id"]),
                            kind="context", object_id=object_id, group_ids=[gid],
                            reason="Text window was not fully bound; character coverage did not advance", next_action="inspect_object").model_dump(mode="json"))
                    if not text_complete(text_coverage[gid]):
                        understood.discard(gid)
                    elif not {cell.get("cell_id") for cell in originals[gid].get("cells", [])} <= set(text_coverage[gid]["presented_cell_ids"]) | set(text_coverage[gid]["structure_seen_cell_ids"]):
                        understood.discard(gid)
                        if next_text_frame(originals[gid], text_coverage[gid]) is None:
                            gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "unpresented_text_cells", gid),
                                kind="context", object_id=object_id, group_ids=[gid],
                                reason="source_text_cells_not_presented: original occurrences lack a fully visible matching character range",
                                next_action="finish").model_dump(mode="json"))
                unshown = unshown_source_groups(context, compact_task_context(context))
                if unshown:
                    understood.difference_update(unshown)
                    blocked_read_groups.update(unshown)
                    for row in expanded:
                        if row["group_id"] not in unshown or row["kind"] == "context":
                            continue
                        issue = {"code": "source_text_not_fully_presented",
                                 "message": "Candidate passage is bound, but its undisplayed source tail requires confirmation."}
                        row.setdefault("quality_issues", []).append(issue)
                        if row.get("quality_status") != "unbound":
                            row["quality_status"] = "needs_confirmation"
                        for cell in row.get("cells", []):
                            cell.setdefault("quality_issues", []).append(issue)
                            if cell.get("quality_status") != "unbound":
                                cell["quality_status"] = "needs_confirmation"
                    gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "unshown_text", sorted(unshown)),
                        kind="context", object_id=object_id, group_ids=sorted(unshown),
                        reason="source_text_not_fully_presented: bounded provider view omits original text/segments; only visible passages were read",
                        next_action="finish").model_dump(mode="json"))
                for row in expanded:
                    if row["group_id"] not in (frame_understood if row["group_id"] in window_groups else understood):
                        gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, "binding", row["source_id"]),
                            kind="context", object_id=object_id, group_ids=[row["group_id"]],
                            reason="该来源片段尚未通过原文绑定；已通过的兄弟片段保留，整组不标为完成",
                            next_action="inspect_object").model_dump(mode="json"))
                valid = expanded  # unbound candidates stay inspectable and pending
                for row in valid:
                    if row["group_id"] not in window_groups:
                        continue
                    if row["kind"] == "assertion":
                        positions = [{key: item.get(key) for key in ("span_id", "char_start", "char_end", "ir_object_id", "evidence_excerpt")}
                                     for item in row.get("physical_sources", [])]
                        row["source_id"] = row["group_id"] + ":" + stable_id("passage", positions or
                            [next(r["source_text_frame"]["frame_id"] for r in part_rows if r["group_id"] == row["group_id"]), row.get("statement")])
                    elif row["kind"] == "quantitative":
                        # The parent physical group aggregates coverage. Distinct
                        # bounded interpretations retain their own atomic meaning,
                        # never applying the latest window to earlier cells.
                        row["source_id"] = row["group_id"] + ":" + stable_id("quantity-meaning",
                            row.get("source_row_label"), row.get("semantics"), row.get("fields"), row.get("unit"))
                    else:
                        row["source_id"] = row["group_id"] + ":context"
                if cell_ids or self.report_source_route:
                    for prior in rows:
                        fresh = next((r for r in valid if r["source_id"] == prior["source_id"] and r["kind"] == "quantitative"), None)
                        if prior["kind"] == "quantitative" and fresh:
                            replaced = {c.get("cell_id") for c in fresh["cells"]}
                            fresh["cells"] = [c for c in prior.get("cells", []) if c.get("cell_id") not in replaced] + fresh["cells"]
                replaced_groups = {r["group_id"] for r in valid}
                replacing = replaced_groups - window_groups
                old_rows = [r for r in rows if r["group_id"] not in replacing]
                by_id = {r["source_id"]: r for r in old_rows}
                for fresh in valid:
                    prior = by_id.get(fresh["source_id"])
                    if prior and fresh["kind"] == "quantitative" and fresh["group_id"] in window_groups:
                        # Identical interpretations may be emitted in several
                        # legal rows. Preserve every returned physical cell once.
                        combined = {cell.get("cell_id"): cell for cell in prior.get("cells", [])}
                        for cell in fresh.get("cells", []):
                            old_cell = combined.get(cell.get("cell_id"))
                            if old_cell is None:
                                combined[cell.get("cell_id")] = cell
                            elif old_cell != cell:
                                old_cell.setdefault("quality_issues", []).append({"code": "source_cell_interpretation_conflict",
                                    "message": "Repeated readings disagree about one original cell; the earlier bound interpretation is retained."})
                                old_cell["quality_status"] = "needs_confirmation"
                                old_cell.setdefault("source_semantics", {}).setdefault("source_interpretation", {}).setdefault("alternative_interpretations", []).append(
                                    {key: cell.get(key) for key in ("value", "unit", "identity_fields", "identity_evidence", "source_semantics")})
                        fresh["cells"] = list(combined.values())
                    if prior and fresh["kind"] == "assertion" and fresh["group_id"] in window_groups:
                        if all(prior.get(key) == fresh.get(key) for key in ("statement", "statement_fragments", "fields", "unit", "semantics")):
                            continue  # same original character range in overlap
                        fresh.setdefault("semantics", {}).setdefault("source_interpretation", {}).setdefault("alternative_interpretations", []).append(
                            {key: prior.get(key) for key in ("fields", "unit", "semantics")})
                        fresh.setdefault("quality_issues", []).append({"code": "passage_interpretation_conflict",
                            "message": "Overlapping readings disagree about the same physical passage."})
                        if fresh.get("quality_status") != "unbound":
                            fresh["quality_status"] = "needs_confirmation"
                    by_id[fresh["source_id"]] = fresh
                rows = list(by_id.values())
                for physical in obj["rows"]:
                    if physical["group_id"] not in understood or not physical.get("cells"):
                        continue
                    read = [r for r in rows if r["group_id"] == physical["group_id"]]
                    if any(r["kind"] == "quantitative" for r in read) and not {c.get("cell_id") for c in physical["cells"]} <= {c.get("cell_id") for r in read for c in r.get("cells", [])}:
                        understood.discard(physical["group_id"])
                processed.difference_update(replaced_groups)
                processed.update(understood)
                gaps = [g for g in gaps if not (g.get("group_ids") and set(g["group_ids"]) <= understood)]
                unresolved = [r["group_id"] for r in part_rows if r["group_id"] not in understood]
                for issue in issues:
                    if unresolved:
                        gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, issue.model_dump(), unresolved), kind="protocol",
                            failure_stage=issue.stage, object_id=object_id, group_ids=unresolved, reason=issue.message, next_action="inspect_object").model_dump(mode="json"))
                for error in errors:
                    if not error.startswith("Uninterpreted source rows:"):
                        gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, error), kind="context", object_id=object_id,
                            group_ids=unresolved, reason=error, next_action="inspect_object").model_dump(mode="json"))
                used_visual |= bool(supplied_image)
                persist()
            except (ValueError, RuntimeError) as exc:
                category = getattr(exc, "category", "protocol" if isinstance(exc, ValueError) else "transport")
                if category in {"provider_account_blocked", "cancelled"}:
                    raise
                if category == "generation_incomplete" and len(part_rows) > 1 and (not self.report_source_route or attempts < MAX_FRAME_ATTEMPTS):
                    middle = len(part_rows) // 2
                    chunks[0:0] = [(part_image, part_rows[:middle]), (part_image, part_rows[middle:])]
                    continue
                gaps.append(EvidenceGap(gap_id=stable_id("gap", source_key, category, [r["group_id"] for r in part_rows]),
                    kind=category if category in {"protocol", "generation_incomplete", "budget"} else "transport", failure_stage=getattr(exc, "stage", None),
                    object_id=object_id, group_ids=[r["group_id"] for r in part_rows], reason=f"Unresolved source part: {exc}", next_action="inspect_object").model_dump(mode="json"))
                if self.report_source_route and category in {"generation_incomplete", "protocol"}:
                    for failed in part_rows:
                        if failed.get("source_text_frame"):
                            record_text_frame(text_coverage[failed["group_id"]], failed["source_text_frame"], success=False)
                        else:
                            blocked_read_groups.add(failed["group_id"])
                persist()
                if category == "budget" or (not rows and category != "generation_incomplete"):
                    raise
        source = persist()
        self.agent["interpreted_objects"] = len(self.sources)
        return source

    def _repair(self, raw, issues, *, model, task, context, template, cancel_check):
        return self._call(model, tool="repair_format", context={"task": task, "original_task": context,
            "invalid_output": raw[:12000],
            "format_errors": repair_diagnostics(issues)},
            instructions="Repair JSON syntax/shape only using the original bounded task, metric cards and allowed references. "
                         "Preserve legal decisions. Do not invent evidence, change exact values or infer missing identities. Return JSON only.",
            template=template, max_tokens=4096, cancel_check=cancel_check)

    def _parse_task(self, raw, contract, *, model, task, template, cancel_check, validate=None, context=None):
        def parse(value):
            parsed = parse_model_json(value, contract)
            if validate:
                validate(parsed)
            return parsed
        try:
            result = parse(raw)
            self._validation()
            return result
        except PlannerFrontierError as exc:
            # Legal JSON with an illegal state transition is not a format repair
            # or an IR defect. Replan against the actual remaining action menu.
            self._validation([str(exc)])
            raise
        except ValueError as exc:
            self._validation([str(exc)])
            repaired = self._repair(raw, [str(exc)], model=model, task=task, context=context or {}, template=template, cancel_check=cancel_check)
            try:
                result = parse(repaired)
                self._validation()
                return result
            except ValueError as error:
                self._validation([str(error)])
                raise ProtocolFailure(str(error)) from error

    def _map(self, obj, source, members, model, cancel_check, *, conflict_cases=None):
        cards = mapping_cards(self._cards(members))
        rows = []
        for r in source["rows"]:
            if r["kind"] == "context":
                continue
            compact = {k: v for k, v in r.items() if k not in {"support_segments", "physical_sources", "identity_evidence", "quality_issues", "statement_fragments"}}
            compact["cells"] = list({json.dumps(c, ensure_ascii=False, sort_keys=True): c for c in [
                {"column": c["column"], "fields": c["fields"], "unit": c.get("unit")} for c in r.get("cells", [])]}.values())
            rows.append(compact)
        matches, coverage = [], {m.metric_id: [] for m in members}
        assessment_failed = False
        chunks = []
        for row in rows:
            if not chunks or len(chunks[-1]) >= 4 or sum(len(json.dumps(r, ensure_ascii=False)) for r in chunks[-1]) + len(json.dumps(row, ensure_ascii=False)) > 8000:
                chunks.append([])
            chunks[-1].append(row)
        queue = [(part, cards[i:i + 6], 0) for part in (chunks or [[]]) for i in range(0, len(cards), 6)]
        while queue:
            part, part_cards, recovery_depth = queue.pop(0)
            context = {"object_id": obj["object_id"], "source_rows": part, "metrics": part_cards,
                       "unresolved_structure": [str(error)[:384] for error in source.get("errors", [])[:8]]}
            if conflict_cases:
                context["conflict_cases"] = conflict_cases
            fragment_path = f"agent/mapping-fragments/{safe_filename(getattr(self, '_current_topic', 'conflicts'))}/{safe_filename(obj['object_id'])}-{task_input_fingerprint('metric_mapping', context)}.json"
            try:
                previous = self.store.read_json(self.job_id, fragment_path) if self.store.exists(self.job_id, fragment_path) else None
                reusable = bool(previous and (previous.get("complete") or previous.get("valid_assessment")) and not conflict_cases)
                if reusable:
                    raw = previous["mapping"]
                else:
                    raw = self._call(model, tool="metric_mapping", context=context, instructions=MAP_INSTRUCTIONS,
                        template=mapping_template(context=context), max_tokens=4096, cancel_check=cancel_check)
                mapping, issues = validate_mapping_payload(raw, context)
                if not reusable:
                    self._validation(issues)
                if not mapping.matches and any(i.stage in {"syntax", "schema"} for i in issues):
                    raw = self._repair(raw, issues, model=model, task="metric_mapping", context=context,
                        template=mapping_template(context=context), cancel_check=cancel_check)
                    mapping, issues = validate_mapping_payload(raw, context)
                    self._validation(issues)
                matches.extend(mapping.matches)
                for item in mapping.coverage:
                    coverage[item.metric_id].append(item)
                for issue in issues:
                    assessment_failed = True
                    self.agent.setdefault("protocol_issues", []).append({"topic_id": getattr(self, "_current_topic", None),
                        "object_id": obj["object_id"], **issue.model_dump()})
                self.store.write_json(self.job_id, fragment_path, {"topic_id": getattr(self, "_current_topic", None),
                    "object_id": obj["object_id"], "input_fingerprint": task_input_fingerprint("metric_mapping", context),
                    "complete": not issues and all(c.status == "reviewed" for c in mapping.coverage),
                    "valid_assessment": not issues,
                    "mapping": mapping.model_dump(mode="json"), "protocol_issues": [i.model_dump() for i in issues]})
                if not issues and all(c.status == "reviewed" for c in mapping.coverage):
                    fingerprint = task_input_fingerprint("metric_mapping", context)
                    self.agent["gaps"] = [g for g in self.agent.get("gaps", []) if g.get("fragment_id") != fingerprint]
                self._stage()
            except (ValueError, RuntimeError) as exc:
                assessment_failed = True
                category = getattr(exc, "category", "protocol" if isinstance(exc, ValueError) else "transport")
                if category in {"provider_account_blocked", "cancelled"}:
                    raise
                if category == "generation_incomplete" and recovery_depth < MAX_FRAME_ATTEMPTS and (len(part) > 1 or len(part_cards) > 1):
                    if len(part) > 1:
                        middle = len(part) // 2
                        queue[0:0] = [(part[:middle], part_cards, recovery_depth + 1), (part[middle:], part_cards, recovery_depth + 1)]
                    else:
                        middle = len(part_cards) // 2
                        queue[0:0] = [(part, part_cards[:middle], recovery_depth + 1), (part, part_cards[middle:], recovery_depth + 1)]
                    continue
                from esg_targeted.models.report_semantics import MappingCoverage
                for card in part_cards:
                    coverage[card["metric_id"]].append(MappingCoverage(metric_id=card["metric_id"], status="uncertain", reason=str(exc)[:512]))
                gap = EvidenceGap(gap_id=stable_id("mapping-gap", fragment_path, category),
                    kind=category if category in {"protocol", "generation_incomplete", "budget"} else "transport",
                    object_id=obj["object_id"], topic_id=getattr(self, "_current_topic", None),
                    metric_ids=[card["metric_id"] for card in part_cards], reason=str(exc), next_action="map_object").model_dump(mode="json")
                self.agent.setdefault("gaps", []).append(gap)
                gap["fragment_id"] = task_input_fingerprint("metric_mapping", context)
                if category == "budget":
                    for _, pending_cards, _ in queue:
                        for card in pending_cards:
                            coverage[card["metric_id"]].append(MappingCoverage(metric_id=card["metric_id"], status="uncertain", reason="Report budget exhausted before this slice"))
                    break
        from esg_targeted.models.report_semantics import MappingCoverage
        result = ObjectMapping(matches=matches, coverage=[MappingCoverage(metric_id=mid,
            status="reviewed" if items and all(i.status == "reviewed" for i in items) else "uncertain",
            reason=("; ".join(dict.fromkeys(i.reason for i in items if i.status == "uncertain")) or ("All bounded source slices reviewed" if items else "No slice reviewed"))[:512])
            for mid, items in coverage.items()])
        self.agent["mapped_rows"] += len(result.matches)
        if not conflict_cases:
            topic = getattr(self, "_current_topic", "conflicts")
            fingerprint = self._mapping_fingerprint(source)
            if assessment_failed:
                key = (topic, obj["object_id"], fingerprint)
                self._mapping_failures[key] = self._mapping_failures.get(key, 0) + 1
            else:
                self._mapping_evidence[(topic, obj["object_id"])] = fingerprint
        return result

    def decide(self, *, packet, model, guard_engine, cancel_check, **_):
        topic = packet.budget.get("disclosure_topic")
        if not topic or not self.eligible(packet.metric["metric_id"]):
            return None
        # No giant shared output / per-metric fallback if a chosen provider does
        # not support the small task protocol. Expose the configuration gap.
        if not callable(getattr(model, "generate_structured", None)):
            decision = SemanticDecision(task_id=packet.task_id, status="ambiguous", fact_groups=[],
                                        uncertainty_code="model_output_invalid", missing_context=["Provider lacks generate_structured"])
            return decision, guard_engine.validate(packet, decision), 0, []
        self._document_id = packet.source_document_id or next((s.document_id for s in packet.spans), "unknown")
        self.agent["current_metric_id"] = packet.metric["metric_id"]
        self._ir_revision = packet.source_ir_revision or next((s.ir_revision for s in packet.spans), 1)
        packet = self._target_packet(packet)
        packet.budget["agent_version"] = self.agent["version"]
        reused = topic in self.topic_results
        if not reused:
            self._current_calls = []
            self.topic_results[topic] = self._run_topic(topic, packet, model, cancel_check)
            calls = list(self._current_calls)
        else:
            calls = []
        result = self.topic_results[topic]
        # Search may have introduced new evidence; make it part of the authority
        # packet before binding. Saved requests/IR remain untouched.
        if result.get("extra_groups"):
            packet = self._target_packet(self._include(packet, set(packet.allowed_group_ids) | set(result["extra_groups"])))
        decisions, audits, bindings = [], [], []
        for object_id, mapping in result["mappings"].items():
            source = self.sources[object_id]
            interpretation = project_interpretation(source, mapping, packet)
            decision, audit = project_source_facts(interpretation, packet, visual_used=bool(source.get("visual_used")))
            decisions.append(decision)
            bindings.extend(audit["projection_bindings"])
            audits.extend({"source_object_id": object_id, "source_key": source.get("source_key"), **m} for m in audit["metric_mappings"])
        candidate_groups = [g for d in decisions for g in d.fact_groups]
        candidate_bindings, remaining_bindings = [], list(bindings)
        for group in candidate_groups:
            matched_index = next((index for index, binding in enumerate(remaining_bindings)
                                  if binding["group"] == group.model_dump(mode="json")), None)
            candidate_bindings.append(remaining_bindings.pop(matched_index) if matched_index is not None else None)
        source_index = {f["source_fact_id"]: f for source in self.sources.values() for f in source.get("facts", [])}
        identity_keys = []
        for index, binding in enumerate(candidate_bindings):
            fact = source_index.get(binding["source_fact_id"]) if binding else None
            identity_keys.append({"source_anchor_id": fact.get("source_anchor_id") or fact["source_fact_id"],
                                  "identity_raw": fact.get("identity_raw", {}), "semantics": fact.get("semantics", {}),
                                  "kind": fact.get("kind")} if fact else {"unbound_projection": index})
        deduplicated = ExactFactDeduplicator().merge(candidate_groups, identity_keys=identity_keys)
        groups = deduplicated.groups
        metric_id = packet.metric["metric_id"]
        applicable = result.get("metric_gaps", {}).get(metric_id, [])
        core_bindings = []
        for index, (group, original_index) in enumerate(zip(groups, deduplicated.source_indices), 1):
            matched = candidate_bindings[original_index]
            if matched:
                core_bindings.append({"core_record_id": stable_id("assert" if matched["record_type"] == "qualitative_assertion" else "obs",
                    packet.task_id, group.group_ref_id, index), "source_fact_id": matched["source_fact_id"],
                    "metric_id": metric_id, "match": "match", "record_type": matched["record_type"],
                    "package_id": self.package.manifest.package_id, "package_version": self.package.manifest.package_version,
                    "package_digest": self.package.compilation.source_digest,
                    **metric_binding_identity(self.package, metric_id)})
        missing = list(dict.fromkeys([*applicable, *(s for d in decisions for s in d.missing_context)]))
        incomplete = bool(missing or any(d.status in {"partial", "ambiguous"} for d in decisions))
        decision = SemanticDecision(task_id=packet.task_id,
            status=("partial" if incomplete else "found") if groups else ("ambiguous" if incomplete or not decisions else "not_found"),
            fact_groups=groups, selected_evidence_span_ids=list(dict.fromkeys(s for d in decisions for s in d.selected_evidence_span_ids)),
            uncertainty_code=("model_output_invalid" if result.get("failure_category") in {"protocol", "generation_incomplete", "budget", "transport", "timeout",
                "planner_stalled", "planner_round_budget", "planner_action_budget"}
                              else "insufficient_evidence") if incomplete or not decisions else "none", missing_context=missing)
        packet.budget.update(search_completeness="bounded_agent_evidence", agent_version=self.agent["version"])
        guard = guard_engine.validate(packet, decision)
        self.store.write_json(self.job_id, f"packets/{safe_filename(packet.metric['metric_id'])}.agent.json", packet.model_dump(mode="json"))
        self.store.write_json(self.job_id, f"disclosures/projections/{safe_filename(packet.metric['metric_id'])}.json",
                              {"metric_id": packet.metric["metric_id"], "metric_mappings": audits, "core_bindings": core_bindings, "shared_reused": reused})
        self.plan["projections"].append({"metric_id": packet.metric["metric_id"], "shared_reused": reused,
                                        "fact_count": len(groups), "missing_context": missing})
        if incomplete:
            self.agent["unresolved_metrics"] = list(dict.fromkeys([*self.agent["unresolved_metrics"], packet.metric["metric_id"]]))
        self._save()
        # The workflow must use the expanded binding packet for materialization.
        self.last_packet = packet
        return decision, guard, len(calls), calls

    def _run_topic(self, topic, packet, model, cancel_check):
        self._current_topic = topic
        attempt = 1 + sum(s.get("topic_id") == topic and s.get("topic_start", False) for s in self.agent["steps"])
        step_start = len(self.agent["steps"])
        cached_path = f"agent/topics/{safe_filename(topic)}.json"
        if self.store.exists(self.job_id, cached_path):
            saved = self.store.read_json(self.job_id, cached_path)
            if saved.get("version") == self.agent["version"] and saved.get("complete"):
                mappings = {oid: ObjectMapping.model_validate(value) for oid, value in saved["mappings"].items()}
                cached_sources = {oid: self.store.read_json(self.job_id, f"agent/sources/{safe_filename(oid)}.json") for oid in mappings}
                if not any(s.get("version") != INTERPRETATION_VERSION or legacy_chart_source(s) for s in cached_sources.values()) and not any(f["source_fact_id"] in self._retired_source_ids() for s in cached_sources.values() for f in s.get("facts", [])):
                    self.sources.update(cached_sources)
                    return {**saved, "mappings": mappings}
                if self.request.report_session_id:
                    for oid, old in cached_sources.items():
                        shared_path = self.store.report_session_path(self.request.report_session_id) / "sources" / f"{old.get('source_key')}.json"
                        if shared_path.is_file():
                            latest = read_json(shared_path)
                            if latest.get("source_identity") == old.get("source_identity") and not any(f["source_fact_id"] in self._retired_source_ids() for f in latest.get("facts", [])):
                                self.sources[oid] = latest
                                self.store.write_json(self.job_id, f"agent/sources/{safe_filename(oid)}.json", latest)
        members = self.pools[topic]["members"]
        obsolete = set(self.agent.setdefault("topic_gap_ids", {}).pop(topic, []))
        self.agent["gaps"] = [g for g in self.agent.get("gaps", []) if g["gap_id"] not in obsolete]
        objects = self._objects(packet)
        self._linked_context = [{"page": s.page_index, "text": s.text[:1800]} for s in packet.spans
                                if s.span_id in packet.context_only_span_ids][:6]
        result = {"mappings": {}, "missing_context": [], "metric_gaps": {}, "failed": False, "extra_groups": [], "failure_category": None}
        navigation_v5 = self.report_source_route
        navigation_scope = stable_id("navigation-scope", self.agent["version"],
            self.inventory.document_id if self.inventory else self._document_id,
            self.inventory.ir_run_id if self.inventory else self.request.ir_run_id,
            self.inventory.ir_revision if self.inventory else self._ir_revision,
            self.request.semantic_provider, self.request.semantic_search)
        previous_nav = self.agent.setdefault("navigation", {}).get(topic)
        if not previous_nav or previous_nav.get("source_scope") != navigation_scope:
            if previous_nav:
                self.agent.setdefault("navigation_history", []).append({"topic_id": topic, **previous_nav})
            self.agent["navigation"][topic] = {"source_scope": navigation_scope,
                "seen_queries": [], "extra_group_ids": [], "search_requires_read": False}
        nav = self.agent["navigation"][topic]
        if navigation_v5 and nav["extra_group_ids"]:
            # Resume actual previously retrieved groups from this immutable IR;
            # this expands source authority, not automatic source interpretation.
            packet = self._target_packet(self._include(packet, set(packet.allowed_group_ids) | set(nav["extra_group_ids"])))
            objects = self._objects(packet)
        seen, feedback, unresolved_actions, blocked_objects = set(), [], {}, set()
        max_steps = getattr(self.request, "agent_max_steps", 8)
        action_count, inspect_count, stalled_rounds = 0, 0, 0
        planner_failure = None
        planning_limit = max_steps
        for step in range(1, max_steps + 1):
            if cancel_check and cancel_check():
                raise ModelCancelled()
            catalog = [{"object_id": o["object_id"], "kind": o["kind"], "summary": o["summary"],
                        "row_count": len(o["rows"]), "already_understood": o["object_id"] in self.sources,
                        "row_ids": [r["group_id"] for r in o["rows"]],
                        "cell_ids": [c["cell_id"] for r in o["rows"] for c in r.get("cells", [])]}
                       for o in objects.values() if ("inspect_object", o["object_id"]) not in seen and o["object_id"] not in blocked_objects]
            context = {"topic_id": topic, "metrics": self._cards(members), "candidate_objects": catalog,
                       "objects": [{"object_id": oid} for oid in objects],
                       "understood_objects": [{"object_id": oid, "structure_complete": s.get("complete", False),
                                               "mapping_pending": oid not in result["mappings"] or any(c.status == "uncertain" for c in result["mappings"][oid].coverage), "gaps": s.get("gaps", []),
                                               "row_ids": [r["group_id"] for r in objects[oid]["rows"]],
                                               "quality_summary": s.get("quality_summary", {}), "identity_questions": s.get("identity_questions", [])[:30],
                                               "pending_repair": oid in blocked_objects}
                                              for oid, s in self.sources.items() if oid in objects],
                       "previous_results": feedback[-6:], "remaining_steps": max_steps - action_count,
                       "initial_object_budget": self.request.shared_object_budget,
                       "remaining_initial_object_budget": max(0, self.request.shared_object_budget - inspect_count)}
            frontier = None
            if navigation_v5:
                frontier = self._navigation_frontier(objects, result, nav, inspect_count, blocked_objects)
                readable = frontier["actions"].get("inspect_object", {}).get("object_ids", [])
                context.update(metrics=self._navigation_cards(members),
                    candidate_objects=self._navigation_catalog(objects, readable, packet),
                    understood_objects=[{key: obj[key] for key in ("object_id", "structure_complete", "mapping_pending", "gaps", "pending_repair")}
                        for obj in context["understood_objects"]], legal_action_frontier=frontier,
                    seen_queries=nav["seen_queries"],
                    planning_rounds_remaining=planning_limit - step + 1,
                    executed_actions_remaining=max_steps - action_count,
                    previous_results=feedback[-4:])
                context.pop("remaining_steps")
                nav.update(planning_rounds_used=step, executed_actions=action_count,
                           remaining_planning_rounds=planning_limit - step + 1,
                           remaining_executed_actions=max_steps - action_count,
                           legal_action_frontier=frontier,
                           evidence_fingerprints=self._object_fingerprints(objects))
                self._save()
            try:
                self.agent["planning_rounds"] += 1
                raw = self._call(model, tool="agent_plan", context=context, instructions=PLAN_INSTRUCTIONS,
                                 template=plan_template(context=context), max_tokens=1600, cancel_check=cancel_check)
                plan = self._parse_task(raw, AgentPlan, model=model, task="agent_plan", template=plan_template(context=context),
                                        cancel_check=cancel_check, context=context,
                                        validate=(lambda value: validate_plan_frontier(value, frontier)) if frontier else None)
            except PlannerFrontierError as exc:
                stalled_rounds += 1
                rejection = {"tool": "agent_plan", "status": "illegal_frontier_action", "reason": str(exc),
                    "planning_round": step, "legal_next_actions": frontier["actions"],
                    "seen_queries": frontier["seen_queries"], "read_required": frontier["read_required"]}
                feedback.append(rejection)
                self.agent.setdefault("planning_history", []).append({"topic_id": topic, "attempt": attempt, **rejection})
                nav.update(stalled_rounds=stalled_rounds, remaining_planning_rounds=planning_limit - step)
                self._save()
                if stalled_rounds >= 2:
                    planner_failure = "planner_stalled"
                    result["missing_context"].append("Planner repeatedly chose unavailable actions without reading the supplied evidence")
                    break
                continue
            except ModelRunFailure as exc:
                if exc.category in {"provider_account_blocked", "cancelled"}:
                    raise
                result["failed"] = True
                result["failure_category"] = exc.category
                result["missing_context"].append(str(exc))
                break
            except (ValueError, RuntimeError) as exc:
                result["failed"] = True
                result["failure_category"] = getattr(exc, "category", "protocol" if isinstance(exc, ValueError) else "transport")
                result["missing_context"].append(str(exc))
                break
            if not plan.actions:
                result["missing_context"].extend(plan.missing_context or ["Navigator returned no evidence action"])
                if navigation_v5:
                    planner_failure = "planner_stalled"
                break
            finished, progressed = False, False
            for action in plan.actions[:2]:
                if navigation_v5:
                    # Earlier actions can expose new evidence or complete a
                    # source. A second action must still be legal in that new
                    # state; a search cannot carry a stale permission to finish.
                    current_frontier = self._navigation_frontier(objects, result, nav, inspect_count, blocked_objects)
                    try:
                        validate_plan_frontier(AgentPlan(actions=[action]), current_frontier)
                    except PlannerFrontierError as exc:
                        rejection = {"tool": "agent_plan", "status": "illegal_frontier_action",
                            "reason": str(exc), "planning_round": step,
                            "rejected_action": action.model_dump(mode="json"),
                            "legal_next_actions": current_frontier["actions"],
                            "seen_queries": current_frontier["seen_queries"],
                            "read_required": current_frontier["read_required"]}
                        feedback.append(rejection)
                        self.agent.setdefault("planning_history", []).append({"topic_id": topic, "attempt": attempt, **rejection})
                        nav["legal_action_frontier"] = current_frontier
                        self._save()
                        break
                if action.tool == "finish":
                    result["missing_context"].extend(plan.missing_context)
                    finished = True
                    break
                if action_count >= max_steps:
                    result["missing_context"].append("Bounded evidence action budget exhausted")
                    if navigation_v5:
                        planner_failure = "planner_action_budget"
                    finished = True
                    break
                key = (action.tool, action.object_id if action.tool != "search_report" else (action.query or "").strip())
                if navigation_v5 and action.tool == "search_report":
                    key = (action.tool, action.metric_id or members[0].metric_id, normalize_navigation_query(action.query))
                if action.tool == "read_object_context" or (navigation_v5 and action.tool == "inspect_object"):
                    key += (getattr(action, "context_kind", None), tuple(action.group_ids), tuple(action.cell_ids))
                    key += (tuple(reading_frontier_fingerprint(objects.get(action.object_id, {"rows": []}), self.sources.get(action.object_id))),)
                if navigation_v5 and action.tool == "map_object":
                    key += (self._mapping_fingerprint(self.sources.get(action.object_id, {})),)
                if key in seen:
                    feedback.append({"tool": action.tool, "object_id": action.object_id,
                        "metric_id": action.metric_id, "query": action.query,
                        "status": "duplicate_action", "gap": "Choose another object or query",
                        **({"legal_next_actions": frontier["actions"]} if frontier else {})})
                    continue
                seen.add(key)
                action_count += 1
                self.agent["executed_actions"] = self.agent.get("executed_actions", 0) + 1
                entry = {"step": len(self.agent["steps"]) + 1, "tool": action.tool, "object_id": action.object_id,
                         "topic_id": topic, "attempt": attempt, "topic_start": len(self.agent["steps"]) == step_start,
                         "metric_ids": [action.metric_id] if action.metric_id else [m.metric_id for m in members], "reason": action.reason, "status": "running",
                         "evidence_phase": "initial" if inspect_count < self.request.shared_object_budget else "gap",
                         "query": action.query or "", "context_kind": action.context_kind if action.tool == "read_object_context" else None}
                started = time.monotonic()
                self.agent["steps"].append(entry)
                self.agent["state"] = "running"
                self._event("agent_gap_search" if action.tool == "search_report" else "source_understanding", entry)
                try:
                    if action.metric_id and action.metric_id not in {m.metric_id for m in members}:
                        raise ProtocolFailure("Action metric reference lies outside this topic", stage="reference")
                    if action.tool != "search_report" and action.object_id in objects:
                        authority = objects[action.object_id]
                        groups = {r["group_id"] for r in authority["rows"]} | {r["group"] for r in authority["rows"]}
                        cells = {c["cell_id"] for r in authority["rows"] for c in r.get("cells", [])} | {c["target_cell"] for r in authority["rows"] for c in r.get("cells", [])}
                        if not set(action.group_ids) <= groups or not set(action.cell_ids) <= cells:
                            raise ProtocolFailure("Action row/cell reference lies outside its physical object", stage="reference")
                    if action.tool == "search_report":
                        if not self.search_report or not (action.query or "").strip():
                            raise ValueError("Search tool requires a nonempty query and configured report index")
                        search_metric_id = action.metric_id or members[0].metric_id
                        if search_metric_id not in {m.metric_id for m in members}:
                            raise ValueError("Search tool must address one of the supplied metrics")
                        before = self._object_fingerprints(objects)
                        found = self.search_report(search_metric_id, action.query)
                        extra = set(found.allowed_group_ids)
                        packet = self._target_packet(self._include(packet, set(packet.allowed_group_ids) | extra, [found]))
                        result["extra_groups"] = list(dict.fromkeys([*result["extra_groups"], *packet.allowed_group_ids]))
                        objects = self._objects(packet)
                        self._linked_context = [{"page": s.page_index, "text": s.text[:1800]} for s in packet.spans
                                                if s.span_id in packet.context_only_span_ids][:6]
                        entry.update(status="completed", query=action.query, search_metric_id=search_metric_id, found_objects=list(objects))
                        if navigation_v5:
                            after = self._object_fingerprints(objects)
                            changed = sorted(oid for oid, fingerprint in after.items() if before.get(oid) != fingerprint)
                            retrieved = sorted({self._reading_key([span])[1] for span in found.spans
                                if span.context_group_id in found.allowed_group_ids})
                            entry.update(retrieved_object_ids=retrieved, new_object_ids=sorted(set(after) - set(before)),
                                changed_evidence_object_ids=changed, new_object_count=len(set(after) - set(before)),
                                new_evidence_count=len(changed), evidence_catalog_fingerprint=stable_id("navigation-catalog", after),
                                evidence_progress=bool(changed))
                            nav["seen_queries"].append({"metric_id": search_metric_id,
                                "query": normalize_navigation_query(action.query), "query_raw": action.query,
                                "new_object_count": entry["new_object_count"], "new_evidence_count": len(changed),
                                "evidence_catalog_fingerprint": entry["evidence_catalog_fingerprint"]})
                            nav.update(search_requires_read=True, extra_group_ids=packet.allowed_group_ids)
                    else:
                        if action.object_id not in objects:
                            raise ValueError("Inspect tool referenced an unknown physical object")
                        # Initial object budget is separate from bounded gap actions.
                        # Extra objects are chosen by the model, never auto-read en masse.
                        obj = objects[action.object_id]
                        if action.metric_id and action.metric_id not in {m.metric_id for m in members}:
                            raise ValueError("Object action referenced a requirement outside this topic")
                        if action.tool == "request_local_repair":
                            if not action.reason.strip():
                                raise ValueError("Local repair needs a concrete source/header reason")
                            prior_gaps = self.sources.get(action.object_id, {}).get("gaps", [])
                            unreliable = any(not cell.get("grid_reliable", True) for row in obj["rows"] for cell in row.get("cells", [])) or any(c.get("alignment_uncertain") for c in obj["columns"])
                            structural_gap = any(g["kind"] in {"header", "source"} for g in prior_gaps)
                            if not unreliable and not structural_gap:
                                raise ProtocolFailure("Local IR repair requires an observed source/grid/header defect; protocol and identity failures belong to semantic reading", stage="semantic")
                            gap = EvidenceGap(gap_id=stable_id("repair-gap", self.job_id, action.object_id, action.gap_kind, action.reason),
                                kind=action.gap_kind, object_id=action.object_id, object_ids=obj.get("physical_object_ids") or [action.object_id],
                                metric_ids=entry["metric_ids"], group_ids=action.group_ids,
                                topic_id=topic, attempt=attempt,
                                reason=action.reason, next_action="request_local_repair").model_dump(mode="json")
                            self.agent.setdefault("gaps", []).append(gap)
                            self.agent.setdefault("pending_repairs", []).append(gap)
                            self.store.write_json(self.job_id, "agent/repair-requests.json", {"requests": self.agent["pending_repairs"]})
                            blocked_objects.add(action.object_id)
                            for mid in entry["metric_ids"]:
                                result["metric_gaps"].setdefault(mid, []).append(action.reason)
                            entry.update(status="needs_repair", gap=gap)
                        elif action.tool == "read_object_context" and action.context_kind == "footnote":
                            entry.update(status="completed", content=obj.get("linked_context", []),
                                         context_available=bool(obj.get("linked_context")))
                        else:
                            if action.object_id in blocked_objects:
                                raise ValueError("This evidence object is suspended pending local repair")
                            if action.tool == "map_object" and action.object_id not in self.sources:
                                raise ValueError("Map tool requires an already understood object")
                            source = self.sources[action.object_id] if action.tool == "map_object" else self._understand(obj, model, cancel_check,
                                group_ids=action.group_ids, cell_ids=action.cell_ids,
                                context_kind=action.context_kind if action.tool == "read_object_context" else None)
                            if action.tool == "inspect_object":
                                inspect_count += 1
                                nav["search_requires_read"] = False
                            mapping = self._map(obj, source, members, model, cancel_check)
                            result["mappings"][action.object_id] = mapping
                            self.store.write_json(self.job_id, f"agent/mappings/{safe_filename(topic)}-{safe_filename(action.object_id)}.json", mapping.model_dump(mode="json"))
                            mapping_partial = any(c.status == "uncertain" for c in mapping.coverage)
                            entry.update(status="partial" if mapping_partial else "completed", source_status="complete" if source.get("complete") else "partial",
                                mapping_status="partial" if mapping_partial else "complete", missing_context=[c.reason for c in mapping.coverage if c.status == "uncertain"],
                                coverage_summary=[{"metric_id": c.metric_id, "object_reviewed": c.status == "reviewed",
                                                   "matched_rows": sum(m.metric_id == c.metric_id and m.match == "match" for m in mapping.matches),
                                                   "uncertain_rows": sum(m.metric_id == c.metric_id and m.match == "uncertain" for m in mapping.matches),
                                                   "missing_context": c.reason if c.status == "uncertain" else ""}
                                                  for c in mapping.coverage])
                            # An uncertain but valid assessment is retained.
                            # Further mapping needs changed source evidence,
                            # rather than repeating the same partial output.
                    progressed = progressed or (bool(entry.get("evidence_progress"))
                        if navigation_v5 and action.tool == "search_report" else True)
                    unresolved_actions.pop(key, None)
                    if action.tool in {"inspect_object", "read_object_context"} and self.sources.get(action.object_id, {}).get("complete"):
                        unresolved_actions = {k: v for k, v in unresolved_actions.items() if not (len(k) > 1 and k[1] == action.object_id)}
                except ModelRunFailure as exc:
                    if exc.category in {"provider_account_blocked", "cancelled"}:
                        raise
                    unresolved_actions[key] = {"reason": str(exc), "metric_ids": entry["metric_ids"], "category": exc.category}
                    seen.discard(key)
                    entry.update(status="failed", missing_context=[str(exc)], failure_category=exc.category)
                except (ValueError, RuntimeError) as exc:
                    category = getattr(exc, "category", "protocol" if isinstance(exc, ValueError) else "transport")
                    unresolved_actions[key] = {"reason": str(exc), "metric_ids": entry["metric_ids"], "category": category}
                    seen.discard(key)
                    entry.update(status="failed", missing_context=[str(exc)], failure_category=category)
                    if category == "budget":
                        finished = True
                finally:
                    entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    feedback.append(dict(entry))
                    if navigation_v5:
                        nav.update(executed_actions=action_count, remaining_executed_actions=max_steps - action_count,
                            remaining_planning_rounds=planning_limit - step)
                    self._save()
            if finished:
                break
            if navigation_v5:
                stalled_rounds = 0 if progressed else stalled_rounds + 1
                self.agent.setdefault("planning_history", []).append({"topic_id": topic, "attempt": attempt,
                    "planning_round": step, "status": "advanced" if progressed else "no_evidence_progress",
                    "executed_actions": action_count, "stalled_rounds": stalled_rounds})
                if stalled_rounds >= 2:
                    planner_failure = "planner_stalled"
                    result["missing_context"].append("Planning made no evidence or mapping progress in consecutive rounds")
                    break
            if not progressed and step == max_steps:
                result["missing_context"].append("Evidence action budget exhausted without progress")
        else:
            if navigation_v5:
                planner_failure = "planner_action_budget" if action_count >= max_steps else "planner_round_budget"
                result["missing_context"].append("Planning round limit reached; report-wide completeness remains unknown")
            else:
                result["missing_context"].append("Bounded evidence action budget exhausted; report-wide completeness unknown")
        if planner_failure:
            result["failed"] = True
            result["failure_category"] = planner_failure
            for member in members:
                result["metric_gaps"].setdefault(member.metric_id, []).extend(result["missing_context"])
            nav.update(stop_reason=planner_failure, executed_actions=action_count,
                       remaining_executed_actions=max_steps - action_count,
                       remaining_planning_rounds=max(0, planning_limit - step))
        result["failed"] = result["failed"] or bool(unresolved_actions)
        if not planner_failure and any(g.get("topic_id") == topic and g.get("kind") == "budget" for g in self.agent.get("gaps", [])):
            result["failure_category"] = "budget"
        gap_causes = [{"category": planner_failure or result["failure_category"], "reason": reason,
            "metric_ids": [m.metric_id for m in members],
            "failure_stage": "planning" if planner_failure else None,
            "next_action": "inspect_object" if planner_failure and any(oid not in self.sources for oid in objects)
                else "replan" if planner_failure else "search_report"}
            for reason in dict.fromkeys(result["missing_context"])]
        result["missing_context"].extend(v["reason"] for v in unresolved_actions.values())
        # Operation failures apply only to the requirements addressed by that
        # action. Mapping coverage and source row gaps remain independently scoped.
        for failure in unresolved_actions.values():
            if not planner_failure:
                result["failure_category"] = failure["category"]
            gap_causes.append({"category": failure["category"], "reason": failure["reason"],
                "metric_ids": failure["metric_ids"], "failure_stage": "execution", "next_action": "replan"})
            for mid in failure["metric_ids"]:
                result["metric_gaps"].setdefault(mid, []).append(failure["reason"])
        if not result["mappings"]:
            for member in members:
                result["metric_gaps"].setdefault(member.metric_id, []).extend(result["missing_context"] or ["No source mapping completed within the evidence budget"])
        topic_gap_ids = []
        for cause in gap_causes:
            gap = EvidenceGap(gap_id=stable_id("topic-gap", topic, cause),
                kind=cause["category"] if cause["category"] in {"budget", "protocol", "generation_incomplete", "transport",
                    "planner_stalled", "planner_round_budget", "planner_action_budget"} else "context", reason=cause["reason"],
                topic_id=topic, attempt=attempt, metric_ids=cause["metric_ids"],
                failure_stage=cause["failure_stage"], next_action=cause["next_action"]).model_dump(mode="json")
            self.agent.setdefault("gaps", []).append(gap)
            topic_gap_ids.append(gap["gap_id"])
        self.agent["topic_gap_ids"][topic] = topic_gap_ids
        self.agent["state"] = "partial" if result["failed"] or result["missing_context"] else "topic_complete"
        self.store.write_json(self.job_id, cached_path, {**result, "version": self.agent["version"],
            "complete": bool(result["mappings"]) and not result["failed"] and not result["missing_context"] and
                        all(self.sources[oid].get("complete") for oid in result["mappings"]) and all(all(c.status == "reviewed" for c in m.coverage) for m in result["mappings"].values()),
            "mappings": {oid: m.model_dump(mode="json") for oid, m in result["mappings"].items()}})
        self._save()
        return result

    def refresh_invalidated_mappings(self, metric_ids, *, model, cancel_check):
        """Reuse identical mapping slices and re-evaluate changed semantics."""
        for topic, result in self.topic_results.items():
            members = self.topics.get(topic, [])
            if not set(metric_ids).intersection(m.metric_id for m in members):
                continue
            self._current_topic = topic
            for oid in list(result["mappings"]):
                result["mappings"][oid] = self._map({"object_id": oid}, self.sources[oid], members, model, cancel_check)
                self.store.write_json(self.job_id, f"agent/mappings/{safe_filename(topic)}-{safe_filename(oid)}.json",
                                      result["mappings"][oid].model_dump(mode="json"))

    def direct_projection_sources(self, records, *, model_executions=None):
        from esg_targeted.results.durable_sources import adapt_durable_direct_core_facts
        known_sources, known_bindings = self.projection_sources()
        fresh_sources, fresh_bindings = adapt_durable_direct_core_facts(self.store.job_dir(self.job_id),
            records,self.package,bound_core_ids={b["core_record_id"] for b in known_bindings},
            request=self.request,model_executions=model_executions)
        self.direct_facts = fresh_sources
        self.direct_bindings = fresh_bindings
        return fresh_sources, fresh_bindings

    def projection_sources(self):
        previous = self.store.read_json(self.job_id, "results/source-facts.json") if self.store.exists(self.job_id, "results/source-facts.json") else {}
        mapping = self.store.read_json(self.job_id, "results/metric-source-mappings.json") if self.store.exists(self.job_id, "results/metric-source-mappings.json") else {}
        sources = {f["source_fact_id"]: f for f in [*previous.get("facts", []), *(f for source in self.sources.values() for f in source.get("facts", []))]}
        retired = self._retired_source_ids()
        sources = {fid: f for fid, f in sources.items() if fid not in retired}
        bindings = {b["core_record_id"]: b for b in mapping.get("core_bindings", [])}
        for path in (self.store.job_dir(self.job_id) / "disclosures/projections").glob("*.json"):
            bindings.update({b["core_record_id"]: b for b in read_json(path).get("core_bindings", [])})
        return list(sources.values()), [b for b in bindings.values() if b.get("source_fact_id") in sources]

    def resolve_conflicts(self, *, conflicts, records, outcomes, model, cancel_check):
        """Revisit only conflicting mappings through the same mapping tool."""
        from esg_targeted.results.family_resolution import FamilyConflictResolver
        bindings = []
        for path in (self.store.job_dir(self.job_id) / "disclosures/projections").glob("*.json"):
            bindings.extend(read_json(path).get("core_bindings", []))
        by_core = {b["core_record_id"]: b for b in bindings}
        source_index = {f["source_fact_id"]: (oid, f) for oid, source in self.sources.items() for f in source.get("facts", [])}
        reports, resolved, rejected, resolved_metrics = [], [], [], set()
        self._current_calls = []
        for conflict in conflicts:
            fact_ids = conflict.get("fact_ids", [])
            references = [source_index.get(by_core.get(fid, {}).get("source_fact_id")) for fid in fact_ids]
            if not references or any(ref is None for ref in references) or len({ref[0] for ref in references}) != 1:
                continue
            object_id = references[0][0]
            source = self.sources[object_id]
            members = [m for m in self.package.metrics if m.metric_id in conflict["metric_ids"]]
            case_id = stable_id("mapping-conflict", fact_ids)
            packet = {"cases": [{"case_id": case_id, "facts": [{"fact_id": fid} for fid in fact_ids]}]}
            try:
                mapping = self._map({"object_id": object_id}, source, members, model, cancel_check,
                    conflict_cases=[{"case_id": case_id, "source_row_ids": sorted({ref[1]["source_row_id"] for ref in references}),
                        "columns": sorted({ref[1].get("column") for ref in references if ref[1].get("column")}),
                        "metric_ids": conflict["metric_ids"], "incompatible_dimensions": conflict.get("incompatible_dimensions", {})}])
                coverage = {c.metric_id: c.status for c in mapping.coverage}
                decisions = []
                for fid, ref in zip(fact_ids, references):
                    fact, mid = ref[1], by_core[fid]["metric_id"]
                    matches = [m for m in mapping.matches if m.source_id == fact["source_row_id"] and m.metric_id == mid
                               and (not m.columns or fact.get("column") in m.columns)]
                    effective = next((m for m in matches if m.columns), matches[0] if matches else None)
                    decisions.append((fid, effective.match if effective else ("different" if coverage.get(mid) == "reviewed" else "uncertain")))
                decided = all(coverage.get(m.metric_id) == "reviewed" for m in members) and all(match != "uncertain" for _, match in decisions)
                removed = [fid for fid, match in decisions if match == "different"] if decided else []
                if decided:
                    FamilyConflictResolver._remove_rejected_facts(records, removed)
                    resolved.append(case_id)
                    rejected.extend(removed)
                    affected = {m.metric_id for m in members}
                    resolved_metrics.update(affected)
                    for topic, result in self.topic_results.items():
                        prior = result["mappings"].get(object_id)
                        if prior:
                            result["mappings"][object_id] = ObjectMapping(matches=[m for m in prior.matches if m.metric_id not in affected] + mapping.matches,
                                coverage=[c for c in prior.coverage if c.metric_id not in affected] + mapping.coverage)
                            self.store.write_json(self.job_id, f"agent/mappings/{safe_filename(topic)}-{safe_filename(object_id)}.json", result["mappings"][object_id].model_dump(mode="json"))
                call = self._current_calls[-1] if self._current_calls else {}
                reports.append({"family_id": "shared-source-mapping", "packet": packet, "status": "completed" if decided else "partial",
                    "decision": mapping.model_dump(mode="json"), "provider": call.get("provider"), "model_id": call.get("model_id"),
                    "raw_output": call.get("raw_output"), "decided_case_ids": [case_id] if decided else [], "rejected_fact_ids": removed})
            except ModelRunFailure as exc:
                if exc.category in {"provider_account_blocked", "cancelled"}:
                    raise
                reports.append({"family_id": "shared-source-mapping", "packet": packet, "status": "failed", "error": str(exc)})
            except (ValueError, RuntimeError) as exc:
                reports.append({"family_id": "shared-source-mapping", "packet": packet, "status": "failed", "error": str(exc)})
        FamilyConflictResolver._refresh_metric_states(records=records, outcomes=outcomes,
            affected_metric_ids=resolved_metrics)
        return {"schema_version": "mapping-conflict-resolution-v1", "attempted": bool(reports), "families": reports,
                "resolved_case_ids": resolved, "rejected_fact_ids": rejected, "route": "metric_mapping"}

    def finalize(self, records, outcomes, *, model_executions=None):
        previous_sources = self.store.read_json(self.job_id, "results/source-facts.json") if self.store.exists(self.job_id, "results/source-facts.json") else {}
        previous_mappings = self.store.read_json(self.job_id, "results/metric-source-mappings.json") if self.store.exists(self.job_id, "results/metric-source-mappings.json") else {}
        objects = [{"source_object_id": obj_id, "identity": {"object_id": obj_id}, "interpretation": source}
                   for obj_id, source in self.sources.items()]
        mappings, core_bindings = list(previous_mappings.get("mappings", [])), list(previous_mappings.get("core_bindings", []))
        for topic, result in self.topic_results.items():
            for object_id, mapping in result["mappings"].items():
                mappings.extend({"source_object_id": object_id, "source_key": self.sources[object_id].get("source_key"), "topic_id": topic, **m.model_dump(mode="json")}
                                for m in mapping.matches)
        # Projection audits explicitly retain different/uncertain combinations,
        # including the sparse mapping contract's model-decided exclusions.
        for path in (self.store.job_dir(self.job_id) / "disclosures/projections").glob("*.json"):
            projection = json.loads(path.read_text(encoding="utf-8"))
            mappings.extend(projection.get("metric_mappings", []))
            core_bindings.extend(projection.get("core_bindings", []))
        direct_facts = getattr(self, "direct_facts", [])
        core_bindings.extend(getattr(self, "direct_bindings", []))
        additional_facts, additional_bindings = adapt_direct_core_facts(records, self.package,
            bound_core_ids={b["core_record_id"] for b in core_bindings}, request=self.request,
            model_executions=model_executions, target_cells=getattr(self, "_direct_targets", {}))
        direct_facts.extend(additional_facts)
        core_bindings.extend(additional_bindings)
        source_rows, conflicting_source_ids, contract_gaps = {}, set(), []
        retired = self._retired_source_ids()
        for fact in [*previous_sources.get("facts", []), *(f for source in self.sources.values() for f in source.get("facts", [])), *direct_facts]:
            fid = fact["source_fact_id"]
            if fid in retired:
                continue
            if fid in source_rows and source_rows[fid] != fact:
                conflicting_source_ids.add(fid)
                contract_gaps.append(EvidenceGap(gap_id=stable_id("source-contract-gap", fid), kind="contract",
                    object_id=fact.get("source_object_id"), reason=f"Source fact {fid} has incompatible payloads under one ID",
                    next_action="inspect_object").model_dump(mode="json"))
                continue
            source_rows[fid] = fact
        object_rows = {}
        for obj in [*previous_sources.get("source_objects", []), *objects]:
            interpretation = obj.get("interpretation", {})
            key = interpretation.get("source_key") or stable_id("historical-source", obj.get("source_object_id"), interpretation.get("version"))
            object_rows[key] = obj
        gap_rows = {g["gap_id"]: g for g in [*previous_sources.get("gaps", []),
            *(g for source in self.sources.values() for g in source.get("gaps", [])), *self.agent.get("gaps", []), *contract_gaps]}
        # Gaps from a successfully re-read object are superseded by its current
        # interpretation, while pinned prior-revision evidence stays intact.
        resolved_objects = {oid for oid, source in self.sources.items() if source.get("complete")}
        live_gap_ids = {g["gap_id"] for g in [*self.agent.get("gaps", []), *(g for source in self.sources.values() for g in source.get("gaps", []))]}
        gap_rows = {gid: gap for gid, gap in gap_rows.items() if gap.get("kind") == "contract" or gid in live_gap_ids
                    or (gap.get("object_id") and gap.get("object_id") not in resolved_objects)}
        self.store.write_json(self.job_id, "results/source-facts.json", {"schema_version": "report-source-facts-v2",
            "source_objects": list(object_rows.values()), "facts": list(source_rows.values()), "gaps": list(gap_rows.values())})
        accepted_ids = {f.get("observation_id") or f.get("assertion_id") for f in [*records.get("quantitative_observations", []), *records.get("qualitative_assertions", [])]}
        preserved_ids = {b["core_record_id"] for b in previous_mappings.get("core_bindings", [])}
        binding_rows = {}
        for binding in core_bindings:
            if binding["core_record_id"] not in accepted_ids:
                continue
            if binding.get("source_fact_id") in conflicting_source_ids and binding["core_record_id"] not in preserved_ids:
                continue
            prior = binding_rows.get(binding["core_record_id"])
            if prior and prior != binding:
                continue  # preserve the pinned record's original source contract
            binding_rows[binding["core_record_id"]] = binding
        core_bindings = list(binding_rows.values())
        mappings = list({json.dumps(m, sort_keys=True, ensure_ascii=False): m for m in mappings}.values())
        self.store.write_json(self.job_id, "results/metric-source-mappings.json", {"schema_version": "metric-source-mappings-v4", "mappings": mappings,
            "core_bindings": core_bindings})
        facts = defaultdict(list)
        for fact in [*records.get("quantitative_observations", []), *records.get("qualitative_assertions", [])]:
            facts[fact["metric_id"]].append({"fact_id": fact.get("observation_id") or fact.get("assertion_id"), "value_origin": fact.get("value_origin", "reported")})
        views = [{"metric_id": mid, "relation_id": r.relation_id, "value_origin": "view",
                  "source_fact_refs": {s: facts[s] for s in r.source_metric_ids}, "reported_fact_refs": facts[mid],
                  "view_status": "source_reference_only"}
                 for r in self.package.relations if r.relation_type.value == "presentation_of" for mid in r.target_metric_ids]
        self.store.write_json(self.job_id, "results/disclosure-views.json", {"schema_version": "disclosure-views-v2", "views": views})
        self.agent["state"] = "partial" if self.agent["unresolved_metrics"] or gap_rows else "complete"
        self._save()
