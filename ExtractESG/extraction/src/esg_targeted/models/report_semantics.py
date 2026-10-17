"""Small, separate contracts for report understanding and standard mapping.

Numbers remain in IR. The model owns row/column meaning and may correct an
unreliable OCR cell using the supplied image. No ESG eligibility rules live here.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from esg_targeted.models.response_adapter import decode_first_json_object
from esg_targeted.models.source_facts import (
    MetricCoverage, MetricMapping, SourceCell, SourceInterpretation, SourceRow,
)
from esg_targeted.report_facts import NEUTRAL_FIELD_DEFINITIONS
from esg_targeted.evidence.chart_support import visual_chart_error, is_chart_read

INTERPRETATION_VERSION = "report-semantics-v8"

UNDERSTAND_INSTRUCTIONS = """Understand ONE physical report object, independently of any ESRS request.
First read source_capabilities and distinguish table, narrative and figure.
For a table, interpret physical rows and complete column-header paths before values;
return one row description per G, not per numerical cell. For a figure, occurrence
IDs are not physical columns or years. Chart payload is an interpretation hint:
confirm each selected visual value in the supplied image using cell_overrides,
keep independent series separate, and report missing point/series evidence.
Chart structure and its point ledger are interpretation/audit metadata, never
verbatim report text. Exact supported point text can be read mechanically.
Independent image readings are new pending candidates and cannot upgrade a
context-only or conflicting original point. Visual slots are not physical columns.
Never declare an OCR numerical fragment to be a reliable table grid.
Distinguish total,
subtotal, member; consumption/production; absolute/share/intensity/capacity/change;
observed scope, method, carrier and category. Preserve the original row title.
Interpret period, entity and entity role by column. An entity named 合计 is an
entity aggregate, not a reporting boundary. Never infer a report fact from a
requested standard classification. Omit unsupported fields; unknown business
semantics may be null only where the schema allows it. IDs are never null/empty.
The backend will expand the supplied IR numbers; DO NOT retype those numbers.
fields and semantics are sparse name/value arrays: emit supported entries only.
Use the defined identity-axis names in fields, never a year/header/value as a
field name. Other supported report axes belong in extensions with evidence refs.
Period/entity/role normally belong to columns, not repeated in every row.
If the image contradicts OCR, use cell_overrides only for the specific affected T.
An unlisted visible cell can be added with group + column + value and a short
visual reason. All such values must be visible in THIS supplied image.
For narrative/list rows, provide readable quoted statements/members, never a year
or a numerical value as a statement. A paragraph may contain several assertions
using the same G. Select target_cells only from THIS row's real T menu for
non-table numerical evidence. Quote assertions using statement_fragments with
real support-segment refs and exact text. Use field_refs for cross-row identity.
field_refs is an object from FIELD NAME to an array of supplied span IDs:
{"reporting_entity":["supplied-span-id"],"reporting_period":["supplied-span-id"]}.
The keys are field names, never span IDs; the values are ID arrays, never field
objects or strings. target_cells is an array of supplied T IDs, never objects.
For a physical table return at most ONE quantitative row per G. Its IR row label
is authoritative; an alternative interpretation never replaces that original.
All supplied reliable cells of that row are expanded once by the backend.
Headers, separators, empty rows and definitions use kind=context and a
context_role; reading them completes a context task without inventing a fact.
Context reasons are short diagnoses, never repeated explanatory prose.
source_text_frame names the supplied character window of an original span.
Read only that window; never quote text or choose values/identities from its
undisplayed prefix/tail. The backend alone records covered ranges. The same G
can return with its next unread window. Keep original span refs: frame_id is
reading state and is never an evidence reference.
Preserve method/footnote context; describe unresolved structure
in missing_context rather than rebuilding the entire table. Return JSON only."""

MAP_INSTRUCTIONS = """Map understood REPORT facts to the supplied semantic standard cards.
The report meanings and the requirements are different things. Fixed standard
scope is a requested classification, never evidence. Read definition, fact grain,
value_family and measurement_contract together. unit_dimension identifies the
physical quantity required: mass is the mass of the disclosed item, not the
volume of its receiving medium or its concentration. Read the receiving medium
from the source heading, row and supporting context; a substance name alone
does not establish or disqualify a medium. Preserve reported units; incompatible
quantities cannot become equivalent by renaming. Unknown catalogue membership
or missing identity is uncertain, never an invented exclusion or an assumption.
Use identity axes and sibling distinctions together. positive_definition states the
included meaning; exclusions state excluded meanings. interpretation_guidance
guides reading and is not a hard exclusion. semantic_distinctions identify concepts
that must stay separate. A shared source may serve several
requirements only when each meaning is supported. Do not relabel renewable share
as energy amount, capacity as consumption, Scope 1+2 as Scope 1, location-based as
market-based, or non-biogenic emissions as biogenic. Never derive numbers here.
Return sparse matches: source_id, metric_id, optional columns, match and reason.
Every match needs explicit source support. If its reasoning needs an assumption,
return uncertain and describe what evidence is missing. No positive default exists.
IDs must name supplied sources/metrics, never null or empty. Use [] rather than
null for matches, columns and coverage. An assertion has no physical columns:
its mapping always uses columns=[]. A negative object assessment may return
matches=[] and reviewed coverage; it needs no invented source reference.
Empty columns means all columns of that source row; select columns if methods,
scopes or categories vary by column. Use uncertain when evidence is insufficient.
Every supplied metric needs coverage. reviewed means this OBJECT was assessed;
for a reviewed metric, combinations omitted from matches are explicitly different.
It does NOT mean the entire report was searched. uncertain needs a specific missing
piece of evidence, useful for the next search/read action. Assertions need actual
readable report statements, not copied numerical table cells. Return JSON only."""

PLAN_INSTRUCTIONS = """You are the evidence navigator for an ESG report extraction task.
Choose the next useful tool from inspect_object, map_object, search_report, read_object_context, request_local_repair, finish.
inspect_object reads the coherent text/table and its image, then maps it.
map_object revises only mapping for an already understood object; it never
re-reads or retypes the table. Use it after a mapping-format/reference problem.
Use standard definitions and the current gaps to select evidence, not just scores.
inspect_object reads only ONE bounded source frame. Use the available group_ids
menu to choose relevant physical rows; a large object is never drained implicitly.
read_object_context reads a bounded header, footnote, crop or cell view of one object.
Use context_kind and optional group_ids/cell_ids to name the specific missing view.
request_local_repair records a source/header problem for a new immutable IR revision;
it suspends this object without waiting for a repair worker. It never repairs network
or billing failures. Supply object_id, gap_kind=header|source and a concrete reason.
Inspect main measurements AND independent branches/methods/footnotes as needed.
An object already understood retains its report meaning across topics. Searching
may use a different phrase or locate a method, financial reconciliation or branch.
search_report may name the supplied metric_id whose gap it addresses.
Do not repeat an unsuccessful identical action. finish must explain remaining gaps;
it is not proof of non-disclosure. The candidate budget is bounded, not an
eligibility gate. When legal_action_frontier is supplied, choose only its available
tools and object IDs. A read_required frontier means choose ONE unread object;
do not search again or infer its content from the navigation summary. seen_queries
are completed searches, including searches with no new evidence. A repeated query
or an unchanged evidence catalog is not progress. Counts for planning rounds and
executed actions are separate. Return at most two actions per turn, JSON only."""


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SparseField(OutputContract):
    name: NonEmptyStr
    value: Annotated[str, StringConstraints(max_length=512)] | int | float | bool | None


class IdentityField(SparseField):
    name: Literal[tuple(NEUTRAL_FIELD_DEFINITIONS) + ("ghg_scope", "scope_2_method", "period", "reporting_year", "entity_role", "energy_carrier", "energy_source", "scope3_category", "facility", "location")]


def _expand_sparse_fields(value):
    if isinstance(value, list):
        entries = [SparseField.model_validate(item) for item in value]
        if len({item.name for item in entries}) != len(entries):
            raise ValueError("Sparse fields must not repeat a name")
        return {item.name: item.value for item in entries}
    # Internal snapshots and old callers use dictionaries. Provider output uses
    # the array generated from SparseField, not a separately handwritten shape.
    if isinstance(value, dict):
        return value
    raise ValueError("Sparse fields require an array or a legacy dictionary")


def _expand_identity_fields(value):
    if isinstance(value, list):
        # Validate the generated axis enum too; dictionary snapshots remain a
        # deliberately broader read-only compatibility input.
        for item in value:
            IdentityField.model_validate(item)
    return _expand_sparse_fields(value)


SparseFieldMap = Annotated[dict[str, Any], BeforeValidator(_expand_sparse_fields, json_schema_input_type=list[SparseField])]
IdentityFieldMap = Annotated[dict[str, Any], BeforeValidator(_expand_identity_fields, json_schema_input_type=list[IdentityField])]
FieldReferences = dict[NonEmptyStr, list[NonEmptyStr]]


class SparseFields(OutputContract):
    fields: IdentityFieldMap = Field(default_factory=dict)
    extensions: SparseFieldMap = Field(default_factory=dict)
    field_refs: FieldReferences = Field(default_factory=dict,
        description="Map each supported field NAME to an array of supplied span IDs. Never map a span ID to a field name/value.")


class StatementFragment(OutputContract):
    ref: NonEmptyStr
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2400, strict=True)]


class SemanticRow(SparseFields):
    group: NonEmptyStr
    kind: Literal["quantitative", "assertion", "context"]
    source_row_label: str | None = Field(default="", max_length=512)
    unit: str | None = Field(default="", max_length=128)
    semantics: SparseFieldMap = Field(default_factory=dict)
    statement: str | None = Field(default="", max_length=2400)
    target_cells: list[NonEmptyStr] = Field(default_factory=list, max_length=64)
    statement_fragments: list[StatementFragment] = Field(default_factory=list, max_length=12)
    context_role: Literal["header", "section", "footnote", "empty", "definition", "other"] | None = None
    context_reason: str = Field(default="", max_length=384)


class SemanticColumn(SparseFields):
    column: NonEmptyStr
    unit: str | None = Field(default="", max_length=128)


class OverrideBase(SparseFields):
    value: str | float | int | None = None
    unit: str | None = ""
    reason: str = ""


class ExistingCellOverride(OverrideBase):
    target_cell: NonEmptyStr
    group: NonEmptyStr | None = None
    column: NonEmptyStr | None = None


class VisualCellOverride(OverrideBase):
    target_cell: Literal[None] = None
    group: NonEmptyStr
    column: NonEmptyStr


CellOverride = ExistingCellOverride | VisualCellOverride


class TableUnderstanding(OutputContract):
    rows: list[SemanticRow] = Field(default_factory=list, max_length=16)
    columns: list[SemanticColumn] = Field(default_factory=list, max_length=32)
    cell_overrides: list[CellOverride] = Field(default_factory=list, max_length=64)
    missing_context: list[Annotated[str, StringConstraints(max_length=384)]] = Field(default_factory=list, max_length=8)


class RowMapping(OutputContract):
    source_id: NonEmptyStr
    metric_id: NonEmptyStr
    columns: list[NonEmptyStr] = Field(default_factory=list)
    match: Literal["match", "different", "uncertain"]
    reason: NonEmptyStr = Field(max_length=512)


class MappingCoverage(MetricCoverage):
    model_config = ConfigDict(extra="forbid")
    metric_id: NonEmptyStr
    status: Literal["reviewed", "uncertain"]
    reason: str = Field(max_length=512)


class ObjectMapping(OutputContract):
    matches: list[RowMapping] = Field(default_factory=list)
    coverage: list[MappingCoverage] = Field(min_length=1)


class ActionBase(OutputContract):
    reason: str = Field(default="", max_length=512)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_flat_action(cls, value):
        if isinstance(value, dict):
            # Old templates requested all unrelated fields. Discard only those
            # known legacy fields; unknown properties remain contract errors.
            legacy = {"object_id", "query", "metric_id", "context_kind", "gap_kind", "group_ids", "cell_ids"}
            value = {k: v for k, v in value.items() if k not in legacy or k in cls.model_fields}
            if value.get("metric_id") == "" and "metric_id" in cls.model_fields:
                value.pop("metric_id")
        return value

    def __getattr__(self, name):
        # Existing tool dispatch reads these neutral defaults on every action.
        defaults = {"object_id": None, "query": None, "metric_id": None,
                    "context_kind": "header", "gap_kind": "header", "group_ids": [], "cell_ids": []}
        if name in defaults:
            return defaults[name]
        return super().__getattr__(name)


class ObjectAction(ActionBase):
    object_id: NonEmptyStr
    metric_id: NonEmptyStr | None = None
    group_ids: list[NonEmptyStr] = Field(default_factory=list)
    cell_ids: list[NonEmptyStr] = Field(default_factory=list)


class InspectObjectAction(ObjectAction):
    tool: Literal["inspect_object"]


class MapObjectAction(ActionBase):
    tool: Literal["map_object"]
    object_id: NonEmptyStr
    metric_id: NonEmptyStr | None = None


class SearchReportAction(ActionBase):
    tool: Literal["search_report"]
    query: NonEmptyStr
    metric_id: NonEmptyStr | None = None


class ReadObjectContextAction(ObjectAction):
    tool: Literal["read_object_context"]
    context_kind: Literal["header", "footnote", "crop", "cell", "identity"] = "header"


class RequestLocalRepairAction(ActionBase):
    tool: Literal["request_local_repair"]
    object_id: NonEmptyStr
    metric_id: NonEmptyStr | None = None
    gap_kind: Literal["header", "source"] = "header"
    group_ids: list[NonEmptyStr] = Field(default_factory=list)
    reason: NonEmptyStr


class FinishAction(ActionBase):
    tool: Literal["finish"]


AgentAction = Annotated[InspectObjectAction | MapObjectAction | SearchReportAction | ReadObjectContextAction |
                        RequestLocalRepairAction | FinishAction, Field(discriminator="tool")]


class AgentPlan(OutputContract):
    actions: list[AgentAction] = Field(min_length=1, max_length=2)
    missing_context: list[str] = Field(default_factory=list)


def understanding_template(field_codes: list[str] | None = None, *, context: dict | None = None) -> dict:
    # field_codes remains accepted for old callers; a package must never narrow
    # the report-reading schema. Open dimensions are preserved as named fields.
    schema = TableUnderstanding.model_json_schema()
    if context is not None:
        refs = reference_catalog(context)
        defs = schema["$defs"]
        _enum(defs["SemanticRow"]["properties"]["group"], refs["groups"])
        _enum(defs["SemanticRow"]["properties"]["target_cells"]["items"], refs["cells"])
        if _reliable_physical_table(context):
            # The backend already expands the whole physical row. Requiring
            # the model to echo its numeric cell IDs adds no semantic decision.
            defs["SemanticRow"]["properties"]["target_cells"] = _empty_output_array()
        _enum(defs["StatementFragment"]["properties"]["ref"], refs["segments"])
        _enum(defs["SemanticColumn"]["properties"]["column"], refs["columns"])
        for name in ("SemanticRow", "SemanticColumn", "ExistingCellOverride", "VisualCellOverride"):
            _enum(defs[name]["properties"]["field_refs"]["additionalProperties"]["items"], refs["segments"])
        _enum(defs["ExistingCellOverride"]["properties"]["target_cell"], refs["cells"])
        _enum(defs["VisualCellOverride"]["properties"]["group"], refs["groups"])
        if context.get("kind") == "figure" or context.get("source_capabilities", {}).get("source_kind") == "figure":
            _enum(defs["VisualCellOverride"]["properties"]["column"], refs["columns"])
        # A required selector with enum=[] makes the deployed strict decoder
        # reject the whole schema, even when that union branch is unused. Keep
        # only source selectors that can actually be chosen in this task.
        if not refs["cells"]:
            schema["properties"]["cell_overrides"]["items"] = {"$ref": "#/$defs/VisualCellOverride"}
            defs.pop("ExistingCellOverride")
        if not refs["segments"]:
            defs["SemanticRow"]["properties"]["statement_fragments"] = _empty_output_array()
            defs.pop("StatementFragment")
        if not refs["columns"]:
            schema["properties"]["columns"] = _empty_output_array()
            defs.pop("SemanticColumn")
    return _reachable_schema(schema)


def _empty_output_array():
    return {"type": "array", "maxItems": 0, "items": False}


def _reliable_physical_table(context):
    cells = [cell for row in context.get("rows", []) for cell in row.get("cells", [])]
    return context.get("kind") == "table" and bool(cells) and all(cell.get("grid_reliable", True) for cell in cells)


def _reachable_schema(schema):
    """Keep exactly the contracts referenced by this task's root schema."""
    definitions = schema.get("$defs", {})
    reachable = set()
    def visit(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in reachable and name in definitions:
                    reachable.add(name)
                    visit(definitions[name])
            for key, item in value.items():
                if key != "$defs":
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(schema)
    schema["$defs"] = {name: value for name, value in definitions.items() if name in reachable}
    return schema


def mapping_template(context: dict | None = None, *, metrics=None, source_ids=None, columns=None) -> dict:
    schema = ObjectMapping.model_json_schema()
    refs = reference_catalog(context or {})
    metric_ids = _ids(metrics, "metric_id") if metrics is not None else refs["metrics"]
    allowed_sources = list(source_ids) if source_ids is not None else refs["sources"]
    allowed_columns = list(columns) if columns is not None and not isinstance(columns, dict) else refs["columns"]
    definitions = schema["$defs"]
    if context is not None or metrics is not None:
        for name in ("RowMapping", "MappingCoverage"):
            _enum(definitions[name]["properties"]["metric_id"], metric_ids)
    if (context is not None or source_ids is not None) and not allowed_sources:
        # Empty/context-only frames permit a negative assessment. An unreachable
        # required enum=[] branch makes strict provider grammar compilation fail.
        schema["properties"]["matches"] = _empty_output_array()
        definitions.pop("RowMapping")
        return schema
    if context is not None or source_ids is not None:
        _enum(definitions["RowMapping"]["properties"]["source_id"], allowed_sources)
    if context is not None or columns is not None:
        _enum(definitions["RowMapping"]["properties"]["columns"]["items"], allowed_columns)
    per_source = columns if isinstance(columns, dict) else refs["source_columns"]
    if per_source:
        definitions["RowMapping"]["allOf"] = [
            {"if": {"properties": {"source_id": {"const": source_id}}},
             "then": {"properties": {"columns": {"type": "array", "items": {"enum": choices}}}}}
            for source_id, choices in per_source.items()
        ]
    return _reachable_schema(schema)


def plan_template(context: dict | None = None) -> dict:
    schema = AgentPlan.model_json_schema()
    if context is not None:
        refs = reference_catalog(context)
        for definition in schema["$defs"].values():
            props = definition.get("properties", {})
            if "object_id" in props:
                _enum(props["object_id"], refs["objects"])
            if "metric_id" in props:
                _enum(props["metric_id"], refs["metrics"])
            for field, key in (("group_ids", "groups"), ("cell_ids", "cells")):
                if field in props:
                    _enum(props[field]["items"], refs[key])
        frontier = context.get("legal_action_frontier")
        if frontier:
            # The same menu is checked by validate_plan_frontier at reception.
            # Never leave an unavailable action in the generation grammar.
            allowed = frontier["actions"]
            alternatives = schema["properties"]["actions"]["items"]["oneOf"]
            alternatives[:] = [item for item in alternatives if
                schema["$defs"][item["$ref"].rsplit("/", 1)[-1]]["properties"]["tool"]["const"] in allowed]
            discriminator = schema["properties"]["actions"]["items"].get("discriminator", {})
            if "mapping" in discriminator:
                discriminator["mapping"] = {tool: ref for tool, ref in discriminator["mapping"].items() if tool in allowed}
            for definition in schema["$defs"].values():
                props = definition.get("properties", {})
                tool = props.get("tool", {}).get("const")
                if tool not in allowed:
                    continue
                option = allowed[tool]
                if "object_id" in props:
                    _enum(props["object_id"], option.get("object_ids", []))
                for name in ("group_ids", "cell_ids"):
                    if name in props:
                        choices = sorted({ref for values in option.get(name, {}).values() for ref in values})
                        _enum(props[name]["items"], choices)
                        if not choices:
                            props[name]["maxItems"] = 0
            schema["properties"]["actions"]["maxItems"] = 2
    return _reachable_schema(schema)


class PlannerFrontierError(ValueError):
    """Legal JSON that cannot advance this frozen navigation state."""


def validate_plan_frontier(plan: AgentPlan, frontier: dict) -> None:
    """Receive against the exact frontier used to compile the generation schema."""
    if len(plan.actions) > 2:
        raise PlannerFrontierError("At most two actions belong to one planning round")
    seen_queries = {(row["metric_id"], row["query"]) for row in frontier.get("seen_queries", [])}
    for action in plan.actions:
        option = frontier["actions"].get(action.tool)
        if option is None:
            raise PlannerFrontierError(f"{action.tool} is unavailable; choose a legal action from this frontier")
        if action.metric_id is not None and action.metric_id not in frontier.get("metric_ids", []):
            raise PlannerFrontierError("Action metric lies outside this topic's legal frontier")
        if getattr(action, "object_id", None) is not None:
            if action.object_id not in option.get("object_ids", []):
                raise PlannerFrontierError(f"{action.tool} cannot address object {action.object_id} in this state")
            for name in ("group_ids", "cell_ids"):
                if not set(getattr(action, name, [])) <= set(option.get(name, {}).get(action.object_id, [])):
                    raise PlannerFrontierError(f"{name} must belong to this object's bounded context menu")
        if action.tool == "search_report":
            metric_id = action.metric_id or frontier["default_search_metric_id"]
            if (metric_id, normalize_navigation_query(action.query)) in seen_queries:
                raise PlannerFrontierError("This metric/query was already searched; inspect evidence or choose a new gap query")


def normalize_navigation_query(query: str) -> str:
    # Preserve meaningful numbers, units and wording; normalization is only for
    # exact query identity, never a claim that different meanings are equivalent.
    return " ".join(query.split()).casefold()


def parse_model_json(raw: str, contract):
    return contract.model_validate(decode_first_json_object(raw))


class ProtocolIssue(OutputContract):
    stage: Literal["syntax", "schema", "reference", "semantic", "generation"]
    path: str
    message: str
    metric_id: str | None = None
    source_id: str | None = None


def _ids(items, key):
    if isinstance(items, dict):
        items = list(items.values()) if all(isinstance(v, dict) for v in items.values()) else list(items)
    return sorted({item if isinstance(item, str) else item.get(key)
                   for item in (items or []) if isinstance(item, (str, dict))
                   and (item if isinstance(item, str) else item.get(key))})


def reference_catalog(context: dict) -> dict:
    """The same bounded reference menu feeds provider schemas and validation."""
    rows = context.get("source_rows", context.get("rows", []))
    cells = [cell for row in rows for cell in row.get("cells", [])]
    segment_ids = set()
    def collect(value):
        if isinstance(value, dict):
            for key in ("span_id", "ref_id", "ref"):
                if isinstance(value.get(key), str) and value[key].strip():
                    segment_ids.add(value[key])
            for key, nested in value.items():
                if key in {"chart_source_policy", "chart_spec", "chart_evidence_audit"}:
                    continue  # audit references are not legal source-span IDs
                if isinstance(nested, (list, dict)):
                    collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
    for key in ("support_segments", "linked_context", "document_context", "rows", "source_rows"):
        collect(context.get(key, []))
    source_columns = {row["source_id"]: sorted({cell["column"] for cell in row.get("cells", []) if cell.get("column")})
                      for row in rows if row.get("source_id")}
    result = {
        "objects": _ids(context.get("objects", context.get("object_menu", [])), "object_id"),
        "metrics": _ids(context.get("metrics", context.get("metric_ids", [])), "metric_id"),
        "groups": _ids(rows, "group"), "cells": _ids(cells, "target_cell"),
        "sources": sorted(source_columns), "columns": sorted(set(_ids(cells, "column")) |
            set(_ids(context.get("columns", []), "column"))),
        "segments": sorted(segment_ids), "source_columns": source_columns,
    }
    for key, values in context.get("allowed_references", {}).items():
        if key in result and key != "source_columns":
            result[key] = _ids(values, key.rstrip("s") + "_id")
    return result


def _enum(schema: dict, choices):
    choices = sorted(set(choices))
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            if option.get("type") == "string":
                option["enum"] = choices
    else:
        schema["enum"] = choices


def validate_mapping_payload(raw, context: dict) -> tuple[ObjectMapping, list[ProtocolIssue]]:
    """Validate each sibling independently; a bad reference is no IR defect.

    Empty matches plus complete reviewed coverage is a legal negative decision.
    A narrative assertion accepts an empty columns array and never a fabricated
    semantic field as a physical column. Missing/invalid coverage remains local
    to the corresponding metric rather than deleting valid siblings.
    """
    references = reference_catalog(context)
    allowed_metrics = set(references["metrics"])
    aliases = context.get("source_aliases", {})
    source_columns = {aliases.get(sid, sid): set(cols) for sid, cols in references["source_columns"].items()}
    issues, matches, coverage = [], [], {}
    affected = set()
    if isinstance(raw, ObjectMapping):
        raw = raw.model_dump()
    elif isinstance(raw, str):
        try:
            raw = decode_first_json_object(raw)
        except ValueError as exc:
            issues.append(ProtocolIssue(stage="syntax", path="$", message=str(exc)))
            raw = {}
            affected.update(allowed_metrics)
    if not isinstance(raw, dict):
        issues.append(ProtocolIssue(stage="schema", path="$", message="Mapping must be a JSON object"))
        raw = {}
        affected.update(allowed_metrics)
    for key in raw.keys() - {"matches", "coverage"}:
        issues.append(ProtocolIssue(stage="schema", path=str(key), message="Unknown mapping property"))
    for collection in ("matches", "coverage"):
        entries = raw.get(collection, [])
        if not isinstance(entries, list):
            issues.append(ProtocolIssue(stage="schema", path=collection, message=f"{collection} must be an array, never null"))
            affected.update(allowed_metrics)
            entries = []
        for index, entry in enumerate(entries):
            path = f"{collection}[{index}]"
            mid = entry.get("metric_id") if isinstance(entry, dict) else None
            sid = entry.get("source_id") if isinstance(entry, dict) else None
            try:
                item = (RowMapping if collection == "matches" else MappingCoverage).model_validate(entry)
            except ValidationError as exc:
                issues.append(ProtocolIssue(stage="schema", path=path, message=str(exc),
                    metric_id=mid if isinstance(mid, str) else None, source_id=sid if isinstance(sid, str) else None))
                affected.update([mid] if mid in allowed_metrics else allowed_metrics)
                continue
            if item.metric_id not in allowed_metrics:
                issues.append(ProtocolIssue(stage="reference", path=path + ".metric_id", message="Unknown metric reference", metric_id=item.metric_id))
                continue
            if collection == "coverage":
                if item.metric_id in coverage:
                    issues.append(ProtocolIssue(stage="semantic", path=path, message="Coverage repeats a metric", metric_id=item.metric_id))
                    affected.add(item.metric_id)
                else:
                    coverage[item.metric_id] = item
                continue
            item.source_id = aliases.get(item.source_id, item.source_id)
            if item.source_id not in source_columns:
                issues.append(ProtocolIssue(stage="reference", path=path + ".source_id", message="Unknown source reference",
                                            metric_id=item.metric_id, source_id=item.source_id))
                affected.add(item.metric_id)
                continue
            invalid = set(item.columns) - source_columns[item.source_id]
            if invalid:
                issues.append(ProtocolIssue(stage="reference", path=path + ".columns",
                    message=f"Unknown physical columns {sorted(invalid)}; allowed {sorted(source_columns[item.source_id])}",
                    metric_id=item.metric_id, source_id=item.source_id))
                affected.add(item.metric_id)
                continue
            if len(item.columns) != len(set(item.columns)):
                issues.append(ProtocolIssue(stage="schema", path=path + ".columns", message="Columns must not repeat", metric_id=item.metric_id, source_id=item.source_id))
                affected.add(item.metric_id)
                continue
            matches.append(item)
    conflicted = set()
    for index, item in enumerate(matches):
        for prior_index, prior in enumerate(matches[:index]):
            if (item.source_id, item.metric_id) != (prior.source_id, prior.metric_id):
                continue
            overlap = not item.columns or not prior.columns or bool(set(item.columns) & set(prior.columns))
            if overlap and item.match != prior.match:
                conflicted.update((index, prior_index))
                affected.add(item.metric_id)
                issues.append(ProtocolIssue(stage="semantic", path=f"matches[{index}]", message="Overlapping source/metric decisions conflict",
                                            metric_id=item.metric_id, source_id=item.source_id))
    matches = [item for index, item in enumerate(matches) if index not in conflicted]
    for mid in sorted(allowed_metrics):
        if mid not in coverage:
            issues.append(ProtocolIssue(stage="reference", path="coverage", message="Missing metric coverage", metric_id=mid))
            affected.add(mid)
        if mid in affected:
            reasons = [issue.message for issue in issues if issue.metric_id in (None, mid)]
            # Full diagnostics remain in issues/call artifacts. The bounded
            # coverage contract must not itself fail while isolating bad rows.
            coverage[mid] = MappingCoverage(metric_id=mid, status="uncertain",
                reason=("; ".join(dict.fromkeys(reasons)) or "Mapping protocol invalid")[:512])
    return ObjectMapping(matches=matches, coverage=[coverage[mid] for mid in sorted(allowed_metrics)]), issues


def _visual_value_allowed(context: dict, row: dict, cell: dict | None = None) -> bool:
    if row.get("source_text_frame"):
        return False  # bounded text cannot certify unseen page pixels
    """Backend-supplied image scope and physical proof, never model claims."""
    if visual_chart_error(row, context):
        return False
    if not context.get("visual_evidence_supplied") or not ({row.get("group"), row.get("group_id")}
            & set(context.get("visual_evidence_group_ids", []))):
        return False
    unreliable = cell.get("grid_reliable") is False if cell is not None else (
        row.get("grid_reliable") is False or any(item.get("grid_reliable") is False for item in row.get("cells", [])))
    if not unreliable:
        return False
    for locator in (cell if cell is not None else row).get("locators", []):
        bbox = locator.get("bbox")
        if isinstance(bbox, dict):
            bbox = [bbox.get(key) for key in ("x0", "y0", "x1", "y1")]
        try:
            if bbox and len(bbox) == 4 and float(bbox[2]) > float(bbox[0]) and float(bbox[3]) > float(bbox[1]):
                return True
        except (TypeError, ValueError):
            pass
    return False


def validate_understanding_payload(raw, context: dict) -> tuple[TableUnderstanding, list[ProtocolIssue]]:
    """Keep well-formed source siblings and isolate task-local bad references."""
    if isinstance(raw, TableUnderstanding):
        raw = raw.model_dump()
    elif isinstance(raw, str):
        try:
            raw = decode_first_json_object(raw)
        except ValueError as exc:
            return TableUnderstanding(), [ProtocolIssue(stage="syntax", path="$", message=str(exc))]
    if not isinstance(raw, dict):
        return TableUnderstanding(), [ProtocolIssue(stage="schema", path="$", message="Understanding must be an object")]
    refs = reference_catalog(context)
    groups, cells, columns = set(refs["groups"]), set(refs["cells"]), set(refs["columns"])
    cells_by_group = {row["group"]: {cell["target_cell"] for cell in row.get("cells", [])}
                      for row in context.get("rows", [])}
    source_rows = {row["group"]: row for row in context.get("rows", [])}
    source_cells = {cell["target_cell"]: (row, cell) for row in context.get("rows", []) for cell in row.get("cells", [])}
    valid, issues = {"rows": [], "columns": [], "cell_overrides": []}, []
    for key in raw.keys() - {"rows", "columns", "cell_overrides", "missing_context"}:
        issues.append(ProtocolIssue(stage="schema", path=str(key), message="Unknown understanding property"))
    for collection, contract in (("rows", SemanticRow), ("columns", SemanticColumn), ("cell_overrides", None)):
        entries = raw.get(collection, [])
        if not isinstance(entries, list):
            issues.append(ProtocolIssue(stage="schema", path=collection, message=f"{collection} must be an array"))
            continue
        for index, entry in enumerate(entries):
            path = f"{collection}[{index}]"
            try:
                if collection == "cell_overrides":
                    # The two real binding variants generate the provider's
                    # anyOf, so no nullable orphan selector can pass parsing.
                    item = TableUnderstanding(cell_overrides=[entry]).cell_overrides[0]
                else:
                    item = contract.model_validate(entry)
            except ValidationError as exc:
                issues.append(ProtocolIssue(stage="schema", path=path, message=str(exc)))
                continue
            error = None
            if collection == "rows":
                if _reliable_physical_table(context):
                    item.target_cells = []
                if item.group not in groups:
                    error = "Unknown source group"
                elif set(item.target_cells) - cells_by_group[item.group]:
                    known = [ref for ref in item.target_cells if ref in cells_by_group[item.group]]
                    issues.append(ProtocolIssue(stage="reference", path=path + ".target_cells", message="Unknown target cell or cell belongs to another row"))
                    if known:
                        item.target_cells = known
                    else:
                        continue
            elif collection == "columns" and item.column not in columns:
                error = "Unknown physical column"
            elif collection == "cell_overrides":
                if item.target_cell and item.target_cell not in cells:
                    error = "Unknown target cell"
                elif not item.target_cell and item.group not in groups:
                    error = "Unknown visual source group"
                elif not item.target_cell and is_chart_read(source_rows[item.group], context) and item.column not in columns:
                    error = "Unknown chart visual output slot"
                elif item.target_cell and item.group and item.target_cell not in cells_by_group.get(item.group, set()):
                    error = "Override target cell belongs to another row"
                elif item.value is not None:
                    physical_row, physical_cell = source_cells[item.target_cell] if item.target_cell else (source_rows[item.group], None)
                    if physical_cell is not None and physical_cell.get("grid_reliable", True):
                        if str(item.value) != str(physical_cell["raw"]):
                            issues.append(ProtocolIssue(stage="semantic", path=path + ".value",
                                message="Reliable IR raw value cannot be replaced by model-generated text or a corrected number"))
                            item = item.model_copy(update={"value": None})
                    elif not _visual_value_allowed(context, physical_row, physical_cell):
                        issues.append(ProtocolIssue(stage="reference", path=path + ".value",
                            message="Visual value requires an unreliable grid, supplied image scope and a real physical bbox"))
                        item = item.model_copy(update={"value": None})
            if error:
                issues.append(ProtocolIssue(stage="reference", path=path, message=error))
                continue
            valid[collection].append(item)
    missing = raw.get("missing_context", [])
    if not isinstance(missing, list) or any(not isinstance(item, str) for item in missing):
        issues.append(ProtocolIssue(stage="schema", path="missing_context", message="missing_context must be an array of strings"))
        missing = []
    return TableUnderstanding(**valid, missing_context=missing), issues


def semantic_card(metric, elements, siblings=()) -> dict:
    """Standard semantics, not full binding/identifier boilerplate."""
    fixed = {e.element_code: e.value_contract.fixed_value for e in elements
             if e.value_contract.fixed_value is not None}
    return {"metric_id": metric.metric_id, "source_datapoint_id": metric.source_datapoint_id,
            "clause_kind": getattr(metric, 'clause_kind', 'original_datapoint'),
            "extension_id": getattr(metric, 'extension_id', None),
            "related_source_datapoint_ids": getattr(metric, 'related_source_datapoint_ids', []),
            "allowed_value_origins": metric.allowed_value_origins,
            "data_class": getattr(metric.data_class, "value", metric.data_class),
            "value_family": metric.value_family,
            "cardinality": metric.cardinality.model_dump(mode="json"),
            "name": metric.labels.get("zh") or metric.labels.get("en"),
            "definition": metric.description, "reporting_requirement": metric.reporting_requirement,
            "fact_grain": metric.fact_grain, "measurement_kind": metric.measurement_kind,
            "disclosure_role": metric.disclosure_role, "depends_on": metric.depends_on,
            "identity_axes": metric.identity_axes, "requested_scope": fixed,
            "evidence_roles": metric.evidence_roles, "derivation_policy": metric.derivation_policy,
            "distinguish_from": list(siblings),
            "measurement_contract": metric.measurement_contract.model_dump(mode="json") if metric.measurement_contract else None,
            "positive_definition": getattr(metric.measurement_contract, "positive_definition", None) or metric.description,
            "interpretation_guidance": getattr(metric.measurement_contract, "interpretation_guidance", []) if metric.measurement_contract else [],
            "exclusions": metric.measurement_contract.exclusions if metric.measurement_contract else [],
            "semantic_distinctions": getattr(metric.measurement_contract, "distinguish_from", []) if metric.measurement_contract else []}


def expand_understanding(understanding: TableUnderstanding, context: dict) -> tuple[list[dict], list[str]]:
    """Expand only known IR coordinates; semantic fields come exclusively from the model."""
    from esg_targeted.evidence.source_reading import bind_source_row
    groups = {row["group"]: row for row in context["rows"]}
    cells = {cell["target_cell"]: cell for row in context["rows"] for cell in row.get("cells", [])}
    columns = {column.column: column for column in understanding.columns}
    overrides = {cell.target_cell: cell for cell in understanding.cell_overrides if cell.target_cell}
    output, errors = [], list(understanding.missing_context)
    occurrences = defaultdict(int)
    for row in understanding.rows:
        source = groups.get(row.group)
        if not source:
            errors.append(f"Unknown source group {row.group}")
            continue
        occurrences[source["group_id"]] += 1
        source_id = f"{source['group_id']}:{occurrences[source['group_id']]}"
        raw_label = source.get("label") or None
        interpreted_label = row.source_row_label or ""
        label_key = lambda text: re.sub(r"[\s$\\{}_^]", "", str(text or "")).casefold()
        label_conflict = bool(source.get("label") and interpreted_label and
                              label_key(interpreted_label) not in label_key(source["label"]))
        expanded = {"source_id": source_id, "group_id": source["group_id"],
                    "kind": row.kind, "source_row_label": raw_label,
                    "unit": row.unit or None, "fields": {k: v for k, v in {**row.extensions, **row.fields}.items() if v not in (None, "")},
                    "field_refs": row.field_refs, "statement_fragments": [fragment.model_dump() for fragment in row.statement_fragments],
                    "support_segments": source.get("support_segments", []),
                    "context_role": row.context_role, "context_reason": row.context_reason,
                    "semantics": {**row.semantics, "source_interpretation": {
                        "interpreted_row_label": interpreted_label, "raw_label_authority": "document_ir",
                        "label_conflict": label_conflict}}, "statement": row.statement or None, "cells": []}
        selected = set(row.target_cells)
        row_cells = {cell["target_cell"] for cell in source.get("cells", [])}
        unknown_selected = selected - row_cells
        if unknown_selected:
            errors.append(f"Unknown target cells for {row.group}: {', '.join(sorted(unknown_selected))}")
            continue
        for cell in source.get("cells", []) if row.kind == "quantitative" else []:
            if selected and context.get("kind") != "table" and cell["target_cell"] not in selected:
                continue
            column = columns.get(cell["column"])
            override = overrides.get(cell["target_cell"])
            reliable = cell.get("grid_reliable", True)
            correction = bool(override and override.value is not None)
            if reliable and correction and str(override.value) != str(cell["raw"]):
                errors.append(f"Rejected reliable IR value override for {cell['target_cell']}; original raw value retained")
                override = override.model_copy(update={"value": None})
                correction = False
            if not reliable:
                if not correction or not _visual_value_allowed(context, source, cell):
                    errors.append(f"Unreliable source grid requires visual reading with a supplied image and physical bbox for {cell['target_cell']}")
                    continue
            expanded["cells"].append({
                "cell_id": cell["cell_id"], "column": cell["column"], "target_cell": cell["target_cell"],
                "value": override.value if override and override.value is not None else cell["raw"],
                "unit": (override.unit if override else "") or (column.unit if column else "") or row.unit or cell.get("unit"),
                "fields": {k: v for k, v in {**(column.extensions if column else {}), **(column.fields if column else {}),
                                           **(override.extensions if override else {}), **(override.fields if override else {})}.items() if v not in (None, "")},
                "field_refs": {**(column.field_refs if column else {}), **(override.field_refs if override else {})},
                "visual_override": bool(correction and not reliable), "column_headers": cell.get("column_headers", []),
            })
        if row.kind == "quantitative":
            for override in understanding.cell_overrides:
                if override.target_cell or override.group != row.group or override.value is None:
                    continue
                if not _visual_value_allowed(context, source):
                    errors.append(f"Unlisted visual value requires an unreliable grid, supplied image and physical bbox for {row.group}")
                    continue
                column = columns.get(override.column)
                expanded["cells"].append({"cell_id": None, "column": override.column,
                                         "value": override.value, "unit": override.unit or (column.unit if column else "") or row.unit,
                                         "fields": {k: v for k, v in {**(column.extensions if column else {}), **(column.fields if column else {}),
                                                                    **override.extensions, **override.fields}.items() if v not in (None, "")},
                                         "field_refs": {**(column.field_refs if column else {}), **override.field_refs}, "visual_override": True})
        bound = bind_source_row(expanded, source, context)
        if label_conflict:
            issue = {"code": "source_label_interpretation_conflict", "raw_label": raw_label,
                     "interpreted_label": interpreted_label,
                     "message": "The interpretation differs from the authoritative physical row label."}
            bound.setdefault("quality_issues", []).append(issue)
            if bound.get("quality_status") != "unbound":
                bound["quality_status"] = "needs_confirmation"
            for cell in bound.get("cells", []):
                cell.setdefault("quality_issues", []).append(issue)
                if cell.get("quality_status") != "unbound":
                    cell["quality_status"] = "needs_confirmation"
        output.append(bound)
    if context.get("kind") == "table":
        # Physical row/cell identity wins over repeated semantic descriptions.
        # Keep conflicting alternatives for audit, not as duplicate facts.
        canonical, merged = {}, []
        for row in output:
            if row["kind"] != "quantitative":
                merged.append(row)
                continue
            row["source_id"] = f"{row['group_id']}:1"
            prior = canonical.get(row["group_id"])
            if prior is None:
                canonical[row["group_id"]] = row
                merged.append(row)
                continue
            audit = prior["semantics"].setdefault("source_interpretation", {})
            audit.setdefault("alternative_interpretations", []).append({
                "semantics": row["semantics"], "fields": row["fields"]})
            issue = {"code": "duplicate_physical_row_interpretation",
                     "message": "Repeated semantic rows share one physical grain; alternative meanings require confirmation."}
            prior.setdefault("quality_issues", []).append(issue)
            existing = {cell.get("cell_id") for cell in prior["cells"]}
            prior["cells"].extend(cell for cell in row["cells"] if cell.get("cell_id") not in existing)
            prior["quality_status"] = "needs_confirmation"
            for cell in prior["cells"]:
                cell.setdefault("quality_issues", []).append(issue)
                if cell.get("quality_status") != "unbound":
                    cell["quality_status"] = "needs_confirmation"
        output = merged
    for alias in overrides.keys() - cells.keys():
        errors.append(f"Unknown target cell {alias}")
    absent = set(groups) - {row.group for row in understanding.rows}
    if absent:
        errors.append("Uninterpreted source rows: " + ", ".join(sorted(absent)))
    return output, errors


def project_interpretation(source: dict, mapping: ObjectMapping, packet) -> SourceInterpretation:
    """Rebind stable physical source IDs to this metric packet's short aliases."""
    from esg_targeted.evidence.target_cells import target_cell_contracts
    from esg_targeted.report_facts import source_anchor_id

    def fact_for_cell(facts, original, cell, ordinal):
        anchor = source_anchor_id(source.get("source_key"), original, cell, ordinal)
        exact = [fact for fact in facts if fact.get("source_anchor_id") == anchor] if anchor is not None else []
        if exact:
            return exact[0]["source_fact_id"] if len(exact) == 1 else None
        # Historical interpretations did not persist anchors. Only a unique
        # legacy occurrence can be rebound; equal values are not identity.
        legacy = [fact for fact in facts if not fact.get("source_anchor_id")
                  and fact.get("column") == cell.get("column")]
        return legacy[0]["source_fact_id"] if len(legacy) == 1 else None

    group_aliases = {gid: alias for alias, gid in packet.alias_map.get("groups", {}).items()}
    target_aliases = {cell.cell_id: cell.alias for cell in target_cell_contracts(packet)}
    metric_id = packet.metric["metric_id"]
    coverage = {item.metric_id: item for item in mapping.coverage}
    decided = coverage.get(metric_id)
    rows = []
    for original in source["rows"]:
        if original["kind"] == "context":
            continue
        group = group_aliases.get(original["group_id"])
        if not group:
            continue
        matches = [m for m in mapping.matches if m.source_id == original["source_id"] and m.metric_id == metric_id]
        default = MetricMapping(metric_id=metric_id,
                                match="different" if decided and decided.status == "reviewed" else "uncertain",
                                reason="Model assessed this combination as different" if decided and decided.status == "reviewed" else "Object mapping incomplete")
        general = next((m for m in matches if not m.columns), None)
        row_mapping = MetricMapping(metric_id=metric_id, match=general.match, reason=general.reason) if general else default
        source_cells = []
        source_fact_rows = [f for f in source.get("facts", []) if f.get("source_row_id") == original["source_id"]]
        for ordinal, cell in enumerate(original.get("cells", [])):
            column_match = next((m for m in matches if cell["column"] in m.columns), None)
            cell_mapping = MetricMapping(metric_id=metric_id, match=column_match.match, reason=column_match.reason) if column_match else row_mapping
            alias = target_aliases.get(cell.get("cell_id"))
            # A known IR cell missing from this bounded packet is not an unlisted
            # visual cell. Leave it for the object coverage gap, never misbind it.
            if cell.get("cell_id") and not alias:
                continue
            source_cells.append(SourceCell(source_fact_id=fact_for_cell(source_fact_rows, original, cell, ordinal),
                                           target_cell=alias, value=cell["value"], unit=cell.get("unit"),
                                           fields=cell.get("fields", {}), metric_mappings=[cell_mapping],
                                           field_evidence_span_ids={axis: list(dict.fromkeys(e["span_id"] for e in evidence if e.get("span_id")))
                                                                    for axis, evidence in cell.get("identity_evidence", {}).items()},
                                           quality_status=cell.get("quality_status", "grounded"),
                                           visual_override=bool(cell.get("visual_override"))))
        rows.append(SourceRow(source_fact_id=source_fact_rows[0]["source_fact_id"] if original["kind"] == "assertion" and source_fact_rows else None,
                              source_id=original["source_id"], kind=original["kind"], group=group,
                              source_row_label=original.get("source_row_label"), semantics=original.get("semantics", {}),
                              fields=original.get("fields", {}), unit=original.get("unit"), cells=source_cells,
                              statement=original.get("statement"), metric_mappings=[row_mapping],
                              statement_span_ids=original.get("statement_span_ids", []),
                              statement_fragments=original.get("statement_fragments", []),
                              field_evidence_span_ids={axis: list(dict.fromkeys(e["span_id"] for e in evidence if e.get("span_id")))
                                                       for axis, evidence in original.get("identity_evidence", {}).items()},
                              quality_status=original.get("quality_status", "grounded")))
    result_coverage = list(mapping.coverage)
    relevant_gaps = [gap for gap in source.get("gaps", []) if metric_id in gap.get("metric_ids", [])]
    if relevant_gaps:
        result_coverage = [item.model_copy(update={"status": "uncertain", "reason": "; ".join(g["reason"] for g in relevant_gaps)})
                           if item.metric_id == metric_id else item for item in result_coverage]
    return SourceInterpretation(source_rows=rows, coverage=result_coverage)
