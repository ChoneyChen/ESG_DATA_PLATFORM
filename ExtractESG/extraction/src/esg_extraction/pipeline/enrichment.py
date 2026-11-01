"""Read clause-declared attributes after capturing values; never regenerate values."""
from pydantic import Field
from typing import Literal
from esg_data.hashing import digest
from esg_contracts.evidence import SearchIntent
from .decisions import DecisionModel, IdentityClaims, evidence_identity_schema
from .period_binding import references_report_period
from .binding import context_from_claims, literal_categories, mapping_for

INSTRUCTIONS = """Enrich existing raw facts from the attached report context. Never output or alter numerical values.
Only fill requested missing attributes. Omit attributes without explicit support. Empty assignments is valid.
Each assignment names the exact fact IDs to which the cited statement applies. A title or note applies only to its own table/section unless it explicitly describes the report-wide scope. Local entity, method, subgroup and period differences take precedence. A group report title alone does not prove every figure is a group total. Never treat a business segment name as proof of total/subtotal/member.
Use exact quotes and source IDs. Do not use the clause definition or report-resource metadata as report evidence.
Read an entity name only when the text identifies the statistical subject. Do not substitute the publisher's company.
An inherited reporting period needs an explicit link to 'reporting period' in the fact's local evidence; do not assign a report year to undated facts or historical columns.
Return assignments with level (table, footnote or report), fact_ids and identity proposals (axis, value, quote, source_id). Missing attributes do not invalidate the fact."""


class AttributeAssignment(DecisionModel):
    level: Literal["table", "footnote", "report"] = "table"
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


def context_frame(documents, sources, clause, facts, profile):
    missing = {axis for fact in facts for axis in missing_attributes(clause, fact) if profile["attributes"].get(axis)}
    terms = list(dict.fromkeys(term for axis in missing for term in profile["attributes"].get(axis,[])))
    if not terms:
        return None, {"requested_attributes":sorted(missing),"terms":[],"objects":[]}
    intent = SearchIntent(target_id=clause.clause_key,contract_digest=clause.clause_digest,
        data_kind="qualitative",positive_terms=terms,exact_terms=terms,source_priority=["text"],limit=8)
    references = []
    for source in sources.values():
        page = documents.search_page(source.ir_run_id,intent.model_copy(update={"source_snapshot":source.snapshot_id}))
        references.extend((ref.score,source,ref) for ref in page.objects if ref.kind == "text")
    texts, template, characters = [], None, 0
    objects = []
    for _,source,ref in sorted(references,key=lambda item:item[0],reverse=True)[:6]:
        frame = documents.read(source.ir_run_id,ref,0)
        template = frame
        for text in frame.texts:
            if characters + len(text.text) > 14000:
                continue
            texts.append(text.model_copy(update={"source_snapshot":source.snapshot_id,
                "span_id":f"c{len(texts)+1}"}))
            characters += len(text.text)
        objects.append({"source_snapshot":source.snapshot_id,"object_id":ref.object_id})
    observation = {"requested_attributes":sorted(missing),"terms":terms,"objects":objects}
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


def apply_context(clause, sources, frame, decision, facts, mappings, aliases, attributes):
    updates = 0
    for assignment in decision.assignments:
        for alias in assignment.fact_ids:
            fid = aliases[alias]
            fact = facts[fid]
            wanted = set(missing_attributes(clause, fact)) & set(attributes)
            if not references_report_period(" ".join(e.excerpt for e in fact.evidence)):
                wanted.discard("reporting_period")
            claims = [claim for claim in assignment.identity if claim.axis in wanted]
            context, flags = context_from_claims(frame,claims)
            fields, dimensions = {}, dict(fact.identity.dimensions)
            for axis in wanted:
                field = getattr(context,axis,None) or context.dimensions.get(axis)
                if field is not None and field.state == "stated":
                    field = field.model_copy(update={"evidence":[e.model_copy(update={"role":assignment.level}) for e in field.evidence]})
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
