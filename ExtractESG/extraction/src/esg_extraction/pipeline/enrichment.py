"""Read clause-declared attributes after capturing values; never regenerate values."""
from pydantic import Field
from esg_data.hashing import digest
from esg_contracts.evidence import SearchIntent
from .decisions import DecisionModel, IdentityClaims, evidence_identity_schema
from .period_binding import references_report_period
from .binding import context_from_claims, literal_categories, mapping_for

INSTRUCTIONS = """Enrich existing raw facts from the attached report context. Never output or alter numerical values.
Only fill requested missing attributes. Omit attributes without explicit support. Empty assignments is valid.
Each assignment names the exact fact IDs to which the cited statement applies. A title or note applies only to its own table/section unless it explicitly describes the report-wide scope. Local entity, method, subgroup and period differences take precedence. A group report title alone does not prove every figure is a group total. Never treat a business segment name as proof of total/subtotal/member.
Read each fact's local_source_ids first. Row/column headings identify its local subject; a general Group name must not replace a business, site or subsidiary named there. A passage about another measurement cannot supply this fact's identity. For report_sources, quote the statement establishing coverage of the report or the named measure, not a bare Group or Total word. If applicability is not established, omit the assignment.
Use exact quotes and source IDs. Do not use the clause definition or report-resource metadata as report evidence.
Read an entity name only when the text identifies the statistical subject. Do not substitute the publisher's company.
An inherited reporting period needs an explicit link to 'reporting period' in the fact's local evidence; do not assign a report year to undated facts or historical columns.
Return assignments with fact_ids and identity proposals (axis, value, quote, source_id). Missing attributes do not invalidate the fact."""


class AttributeAssignment(DecisionModel):
    fact_ids: list[str] = Field(min_length=1)
    identity: IdentityClaims = Field(default_factory=list)


class ContextDecision(DecisionModel):
    assignments: list[AttributeAssignment] = Field(default_factory=list)


def missing_attributes(clause, fact):
    result = []
    for axis in clause.purposes.enrichment.identity_axes:
        field = getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)
        if field is None or field.state in {"not_read", "not_stated"}:
            result.append(axis)
    return result


def context_frame(documents, sources, clause, facts, profile, frames):
    missing = {axis for fact in facts for axis in missing_attributes(clause, fact)}
    report_attributes = {axis for axis in missing if profile["attributes"].get(axis)}
    terms = list(dict.fromkeys(term for axis in sorted(report_attributes)
        for term in profile["attributes"][axis]))
    texts, seen, levels, characters = [], {}, {}, 0
    fact_sources = {fact.source_fact_id: [] for fact in facts}
    objects, template = [], None

    def add(text, snapshot, level, maximum):
        nonlocal characters
        key = (snapshot, text.object_id, text.char_start, text.text)
        if key in seen:
            return seen[key]
        if characters + len(text.text) > maximum:
            return None
        alias = f"c{len(texts)+1}"
        texts.append(text.model_copy(update={"source_snapshot":snapshot,"span_id":alias}))
        characters += len(text.text)
        seen[key], levels[alias] = alias, level
        return alias

    # Only the frames containing this fact's value/statement supply local context.
    # Headers and notes remain physical references, never previous model answers.
    for frame in frames:
        for fact in facts:
            if frame.source_snapshot != f"{fact.ir_run_id}:{fact.ir_revision}":
                continue
            anchors = {e.object_id for e in fact.evidence if e.role in {"value","statement"}}
            rows = [(row,[cell for cell in row.cells
                if (cell.source_object_id or cell.cell_id) in anchors]) for row in frame.rows]
            rows = [(row,cells) for row,cells in rows if cells]
            if not rows and not any(text.object_id in anchors for text in frame.texts):
                continue
            template = frame
            local = [(text,"table") for text in frame.title_sources]
            sections = {row.section for row,cells in rows if row.section}
            local.extend((text,"footnote" if frame.kind == "table" else "table")
                for text in frame.texts if text.object_type != "cell" or text.text in sections)
            for row,cells in rows:
                local.extend((text,"table") for text in [*row.label_sources,*row.unit_sources])
                local.extend((text,"table") for cell in cells
                    for text in frame.column_sources.get(cell.column_id,[]))
            for text,level in local:
                alias = add(text,frame.source_snapshot,level,7000)
                if alias and alias not in fact_sources[fact.source_fact_id]:
                    fact_sources[fact.source_fact_id].append(alias)

    references = []
    if terms:
        intent = SearchIntent(target_id=clause.clause_key,contract_digest=clause.clause_digest,
            data_kind="qualitative",positive_terms=terms,exact_terms=terms,source_priority=["text"],limit=8)
        for source in sources.values():
            page = documents.search_page(source.ir_run_id,intent.model_copy(update={"source_snapshot":source.snapshot_id}))
            references.extend((ref.score,source,ref) for ref in page.objects if ref.kind == "text")
    report_sources = []
    for _,source,ref in sorted(references,key=lambda item:item[0],reverse=True)[:6]:
        frame = documents.read(source.ir_run_id,ref,0)
        template = frame
        for text in frame.texts:
            alias = add(text,source.snapshot_id,"report",14000)
            if alias and alias not in report_sources:
                report_sources.append(alias)
        objects.append({"source_snapshot":source.snapshot_id,"object_id":ref.object_id})
    requested = missing if any(fact_sources.values()) else report_attributes
    observation = {"requested_attributes":sorted(requested),"terms":terms,"objects":objects,
        "fact_sources":fact_sources,"report_sources":report_sources,
        "report_attributes":sorted(report_attributes),"source_levels":levels}
    if not texts:
        return None,observation
    combined = template.model_copy(update={"kind":"text","source_snapshot":None,"rows":[],"columns":{},
        "column_sources":{},"title":"","title_sources":[],"texts":texts,"digest":digest([t.model_dump(mode="json") for t in texts])})
    return combined,observation


def context_schema(frame, aliases, attributes):
    schema = ContextDecision.model_json_schema()
    schema["$defs"]["AttributeAssignment"]["properties"]["fact_ids"]["items"] = {"type":"string","enum":list(aliases)}
    evidence_identity_schema(schema,[t.span_id for t in frame.texts])
    schema["$defs"]["IdentityProposal"]["properties"]["axis"] = {"type":"string","enum":attributes}
    return schema


def apply_context(clause, sources, frame, decision, facts, mappings, aliases, observation):
    updates = 0
    source_levels = {(text.source_snapshot,text.object_id):observation["source_levels"][text.span_id]
        for text in frame.texts}
    for assignment in decision.assignments:
        for alias in assignment.fact_ids:
            fid = aliases[alias]
            fact = facts[fid]
            wanted = set(missing_attributes(clause, fact)) & set(observation["requested_attributes"])
            if not references_report_period(" ".join(e.excerpt for e in fact.evidence)):
                wanted.discard("reporting_period")
            local = set(observation["fact_sources"][fid])
            report = set(observation["report_sources"])
            report_attributes = set(observation["report_attributes"])
            claims = [claim for claim in assignment.identity if claim.axis in wanted
                and (claim.source_id in local or
                    (claim.axis in report_attributes and claim.source_id in report))]
            context, flags = context_from_claims(frame,claims)
            fields, dimensions = {}, dict(fact.identity.dimensions)
            for axis in wanted:
                field = getattr(context,axis,None) or context.dimensions.get(axis)
                if field is not None and field.state == "stated":
                    field = field.model_copy(update={"evidence":[e.model_copy(update={
                        "role":source_levels.get((e.source_snapshot,e.object_id),e.role)}) for e in field.evidence]})
                    updates += 1
                    (fields if clause.attributes[axis].source == "identity" else dimensions)[axis] = field
            if not fields and dimensions == fact.identity.dimensions:
                continue
            identity = literal_categories(clause,fact.identity.model_copy(update={**fields,"dimensions":dimensions}),[])
            changed = fact.model_copy(update={"identity":identity,"interpretation_id":digest([fact.interpretation_id,identity.model_dump(mode="json")])})
            previous = mappings[fid]
            source = sources[f"{fact.ir_run_id}:{fact.ir_revision}"]
            mappings[fid] = mapping_for(clause,source,changed,previous.match,previous.reason)
            facts[fid] = changed
    return updates
