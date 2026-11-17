"""Read clause-declared attributes after capturing values; never regenerate values."""
from pydantic import Field
import re
import unicodedata
from esg_data.hashing import digest
from esg_data.literals import arabic_years
from esg_data.periods import normalize_period
from esg_data.normalize import fold
from esg_contracts.evidence import SearchIntent, TextEvidence
from .decisions import DecisionModel, IdentityClaims, evidence_identity_schema
from .period_binding import references_report_period
from .binding import context_from_claims, literal_categories, mapping_for

INSTRUCTIONS = """Enrich existing raw facts from the attached report context. Never output or alter numerical values.
Only fill requested missing attributes. Omit attributes without explicit support. Empty assignments is valid.
Each assignment names the exact fact IDs to which the cited statement applies. A title or note applies only to its own table/section unless it explicitly describes the report-wide scope. Local entity, method, subgroup and period differences take precedence. A group report title alone does not prove every figure is a group total. Never treat a business segment name as proof of total/subtotal/member.
Each fact includes its read-only reported_value and reported period. Use both to align a local target, commitment or baseline passage to the exact facts it describes. A single row can mix historical results and targets: do not assign one value_role to all its years. A bare measure label proves neither actual nor target. Omit a role without an explicit supporting quote; never infer roles from the report year or a future year alone.
When value_role is requested, name exactly one fact ID per assignment and decide its role separately. Shared attributes may be repeated for the facts to which they apply.
Column source groups gN contain joined physical header parts. Quote their joined text and cite the group from the fact's local_source_ids. When the same supported attribute applies to several facts, return one assignment naming those fact IDs and cite the quote once.
Read each fact's local_source_ids first. Row/column headings identify its local subject; a general Group name must not replace a business, site or subsidiary named there. A passage about another measurement cannot supply this fact's identity. For report_sources, quote the statement establishing coverage of the report or the named measure, not a bare Group or Total word. If applicability is not established, omit the assignment.
Use exact quotes and source IDs. Do not use the clause definition or report-resource metadata as report evidence.
Read an entity name only when the text identifies the statistical subject. Do not substitute the publisher's company.
An inherited reporting period needs an explicit link to 'reporting period' in the fact's local evidence; do not assign a report year to undated facts or historical columns.
Return assignments with fact_ids and identity proposals (axis, value, quote, source_id). Missing attributes do not invalidate the fact."""


class AttributeAssignment(DecisionModel):
    fact_ids: list[str] = Field(min_length=1)
    identity: IdentityClaims = Field(min_length=1)


class ContextDecision(DecisionModel):
    assignments: list[AttributeAssignment]


def missing_attributes(clause, fact):
    """Missing attributes that can be supplemented from this fact's context."""
    result = []
    for axis in clause.purposes.enrichment.identity_axes:
        if axis == "reporting_period" and not references_report_period(" ".join(e.excerpt for e in fact.evidence)):
            continue
        field = getattr(fact.identity, axis, None) or fact.identity.dimensions.get(axis)
        if field is None or field.state in {"not_read", "not_stated"}:
            result.append(axis)
    return result


def matches_context_terms(terms, text):
    text = fold(text)
    for term in terms:
        term = fold(term).strip()
        if term.startswith("re:"):
            if re.search(term[3:],text):
                return True
        elif term.isascii():
            pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(part) for part in term.split()) + r"(?![a-z0-9])"
            if term and re.search(pattern,text):
                return True
        elif term and "".join(term.split()) in "".join(text.split()):
            return True
    return False


def local_note_applies(text, local_text):
    """A numbered table note requires its reference in this fact's headings."""
    marker = re.match(r"\s*([⁰¹²³⁴⁵⁶⁷⁸⁹]+|\$?\s*\^\s*\{+\s*\(?[0-9]+\)?\s*\}+\s*\$?|\([0-9]+\)|\[[0-9]+\])",text)
    if marker is None:
        return True
    number = "".join(re.findall(r"\d",unicodedata.normalize("NFKC",marker[1])))
    references = re.finditer(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+|\$?\s*\^\s*\{+\s*\(?[0-9]+\)?\s*\}+\s*\$?|\[note\s+[0-9]+\]",local_text,re.I)
    for reference in references:
        # Printed unit powers and mathematical exponents are not note references.
        if re.search(r"(?:(?<![A-Za-z])(?:m|km|cm|dm|mm|Nm|ft)|\d)\s*$",local_text[:reference.start()]):
            continue
        if "".join(re.findall(r"\d",unicodedata.normalize("NFKC",reference[0]))) == number:
            return True
    return False


def attribute_source_ids(clause, frame, observation):
    """Build current permissions, including migration of pre-map observations."""
    texts = {text.span_id:text.text for text in frame.texts}
    texts.update({alias:" ".join(part.text for part in parts) for alias,parts in frame.column_sources.items()})
    levels = observation["source_levels"]
    result = {}
    for fid,local in observation["fact_sources"].items():
        local_text = "\n".join(texts.get(sid,"") for sid in local if levels.get(sid) not in {"report","footnote"})
        local = [sid for sid in local if levels.get(sid) != "report" and
            (levels.get(sid) != "footnote" or local_note_applies(texts.get(sid,""),local_text))]
        result[fid] = {}
        for axis in observation["requested_attributes"]:
            attribute = clause.attributes[axis]
            allowed = list(local) if "local" in attribute.context_levels else []
            if "report" in attribute.context_levels:
                allowed.extend(sid for sid in observation["report_sources"]
                    if matches_context_terms(attribute.context_terms,texts.get(sid,"")))
            result[fid][axis] = list(dict.fromkeys(allowed))
    return result


def context_frame(documents, sources, clause, facts, profile, frames):
    missing = {axis for fact in facts for axis in missing_attributes(clause, fact) if profile["levels"][axis]}
    report_attributes = {axis for axis in missing
        if profile["attributes"].get(axis) and "report" in profile["levels"][axis]}
    terms = list(dict.fromkeys(term for axis in sorted(report_attributes)
        for term in profile["attributes"][axis]))
    texts, seen, levels, characters = [], {}, {}, 0
    groups, group_keys = {}, {}
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

    def header_group(parts, snapshot):
        nonlocal characters
        key = (snapshot,tuple((p.object_id,p.char_start,p.text) for p in parts))
        if key in group_keys:
            return group_keys[key]
        size = sum(len(p.text) for p in parts)
        if not parts or characters+size>7000:
            return None
        alias = f"g{len(groups)+1}"
        groups[alias] = [p.model_copy(update={"source_snapshot":snapshot}) for p in parts]
        group_keys[key],levels[alias] = alias,"column_header"
        characters += size
        return alias

    for fact in facts:
        if "local_context_selected" not in fact.flags:
            continue
        for evidence in fact.evidence:
            if evidence.role not in {"row","column_header","unit","table","footnote"}:
                continue
            if evidence.char_start is None:
                continue
            text = TextEvidence(object_id=evidence.object_id,object_type=evidence.object_type,
                page_index=evidence.page_index,text=evidence.excerpt,char_start=evidence.char_start,
                source_snapshot=evidence.source_snapshot,bbox=evidence.bbox)
            alias = add(text,evidence.source_snapshot,evidence.role if evidence.role=="footnote" else "table",7000)
            if alias and alias not in fact_sources[fact.source_fact_id]:
                fact_sources[fact.source_fact_id].append(alias)
        if frames:
            template = frames[0]

    # Only the frames containing this fact's value/statement supply local context.
    # Headers and notes remain physical references, never previous model answers.
    for frame in frames:
        for fact in facts:
            if "local_context_selected" in fact.flags:
                continue
            anchors = {e.object_id for e in fact.evidence if e.role in {"value","statement"}}
            rows = [(row,[cell for cell in row.cells
                if (cell.source_object_id or cell.cell_id) in anchors
                and cell.raw_text == getattr(fact.value,"value_raw",None)]) for row in frame.rows]
            rows = [(row,cells) for row,cells in rows if cells]
            if not rows and not any(text.object_id in anchors
                and getattr(fact.value,"statement_raw",None) in text.text
                for text in frame.texts if fact.kind == "assertion"):
                continue
            template = frame
            local = [(text,"table") for text in frame.title_sources]
            sections = {row.section for row,cells in rows if row.section}
            local.extend((text,"footnote" if frame.kind == "table" else "table")
                for text in frame.texts if (frame.kind == "table" and
                    (text.object_type != "cell" or text.text in sections)) or text.object_id in anchors)
            for row,cells in rows:
                local.extend((text,"table") for text in [*row.label_sources,*row.unit_sources])
                for cell in cells:
                    alias = header_group(frame.column_sources.get(cell.column_id,[]),frame.source_snapshot)
                    if alias and alias not in fact_sources[fact.source_fact_id]:
                        fact_sources[fact.source_fact_id].append(alias)
            local_text = "\n".join([*(text.text for text,level in local if level != "footnote"),
                *(part.text for alias in fact_sources[fact.source_fact_id] for part in groups.get(alias,[]))])
            for text,level in local:
                if level == "footnote" and not local_note_applies(text.text,local_text):
                    continue
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
            if not any(matches_context_terms(profile["attributes"][axis],text.text) for axis in report_attributes):
                continue
            alias = add(text,source.snapshot_id,"report",14000)
            if alias and alias not in report_sources:
                report_sources.append(alias)
        objects.append({"source_snapshot":source.snapshot_id,"object_id":ref.object_id})
    requested = missing if any(fact_sources.values()) else report_attributes
    observation = {"requested_attributes":sorted(requested),"terms":terms,"objects":objects,
        "fact_sources":fact_sources,"report_sources":report_sources,
        "report_attributes":sorted(report_attributes),"source_levels":levels}
    if not texts and not groups:
        return None,observation
    content = {"texts":[t.model_dump(mode="json") for t in texts],
        "column_sources":{key:[p.model_dump(mode="json") for p in parts] for key,parts in groups.items()}}
    combined = template.model_copy(update={"kind":"text","source_snapshot":None,"rows":[],"columns":{},
        "column_sources":groups,"title":"","title_sources":[],"texts":texts,"digest":digest(content)})
    observation["attribute_sources"] = attribute_source_ids(clause,combined,observation)
    return combined,observation


def context_schema(frame, aliases, attributes):
    schema = ContextDecision.model_json_schema()
    schema["$defs"]["AttributeAssignment"]["properties"]["fact_ids"]["items"] = {"type":"string","enum":list(aliases)}
    if "value_role" in attributes:
        # A mixed actual/target row needs a separate decision for every fact.
        schema["$defs"]["AttributeAssignment"]["properties"]["fact_ids"]["maxItems"] = 1
    evidence_identity_schema(schema,[*[t.span_id for t in frame.texts],*frame.column_sources])
    schema["$defs"]["IdentityProposal"]["properties"]["axis"] = {"type":"string","enum":attributes}
    return schema


def bind_attribute_literal(value, quote):
    """Select actual quoted leaves while preserving structured value types."""
    if isinstance(value, list):
        if not value:
            raise ValueError("empty_attribute_value")
        return [bind_attribute_literal(item,quote) for item in value]
    if isinstance(value, dict):
        if not value:
            raise ValueError("empty_attribute_value")
        return {key:bind_attribute_literal(item,quote) for key,item in value.items()}
    if value is None:
        raise ValueError("empty_attribute_value")
    literal = str(value).lower() if isinstance(value,bool) else str(value)
    needle = "".join(fold(literal).split())
    if not needle:
        raise ValueError("empty_attribute_value")
    normalized, positions = [], []
    for index, char in enumerate(quote):
        for folded in fold(char):
            if not folded.isspace():
                normalized.append(folded)
                positions.append(index)
    text = "".join(normalized)
    matches, offset = [], 0
    while (offset := text.find(needle,offset)) >= 0:
        start, end = positions[offset], positions[offset+len(needle)-1]+1
        offset += 1
        if (needle[0].isascii() and needle[0].isalnum() and start and re.match(r"[A-Za-z0-9]",quote[start-1])):
            continue
        if (needle[-1].isascii() and needle[-1].isalnum() and end < len(quote) and re.match(r"[A-Za-z0-9]",quote[end])):
            continue
        if needle[0].isdigit() and start > 1 and quote[start-1] == "." and quote[start-2].isdigit():
            continue
        if needle[-1].isdigit() and end+1 < len(quote) and quote[end] == "." and quote[end+1].isdigit():
            continue
        matches.append((start,end))
    if len(matches) != 1:
        raise ValueError("attribute_value_missing_or_ambiguous_in_quote")
    start, end = matches[0]
    return quote[start:end] if isinstance(value,str) else value


def apply_context(clause, sources, frame, decision, facts, mappings, aliases, observation, requested_by_fact=None, rejected_claims=None):
    updates = 0
    changed_ids = []
    rejected_claims = rejected_claims if rejected_claims is not None else []
    allowed_sources = observation.get("attribute_sources")
    if allowed_sources is None:
        allowed_sources = attribute_source_ids(clause,frame,observation)
    source_levels = {(text.source_snapshot,text.object_id):observation["source_levels"][text.span_id]
        for text in frame.texts}
    source_levels.update({(p.source_snapshot,p.object_id):"column_header"
        for parts in frame.column_sources.values() for p in parts})
    proposals = {}
    for assignment in decision.assignments:
        claims = [claim for claim in assignment.identity
            if claim.axis != "value_role" or len(assignment.fact_ids) == 1]
        for alias in assignment.fact_ids:
            proposals.setdefault(aliases[alias],[]).extend(claims)
    for fid,proposed in proposals.items():
        fact = facts[fid]
        wanted = set(missing_attributes(clause, fact)) & set(observation["requested_attributes"])
        if requested_by_fact is not None:
            wanted &= set(requested_by_fact[fid])
        claims = []
        for claim in proposed:
            if claim.axis not in wanted:
                continue
            if claim.source_id not in allowed_sources.get(fid,{}).get(claim.axis,[]):
                rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                    "reason":"attribute_source_outside_declared_context"})
                continue
            claims.append(claim)
        grounded, category_codes = [], {}
        for claim in claims:
            if claim.axis == "value_role" and not isinstance(claim.value,str):
                rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                    "reason":"attribute_role_requires_one_category"})
                continue
            if claim.axis == "value_role":
                period = normalize_period(fact.identity.reporting_period.value)
                years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)",arabic_years(claim.quote)))
                if period and period.kind in {"annual","fiscal_year"} and period.year and years and str(period.year) not in years:
                    rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                        "reason":"attribute_quote_period_mismatch"})
                    continue
                local_role_source = ("local" in clause.attributes[claim.axis].context_levels
                    and claim.source_id in observation["fact_sources"][fid]
                    and observation["source_levels"].get(claim.source_id) != "report")
                if not local_role_source:
                    if not period or not period.year or str(period.year) not in years:
                        rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                            "reason":"attribute_report_role_requires_explicit_fact_period"})
                        continue
                    try:
                        bind_attribute_literal(getattr(fact.value,"value_raw",None),claim.quote)
                    except ValueError:
                        rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                            "reason":"attribute_report_role_requires_exact_fact_value"})
                        continue
            single, _ = context_from_claims(frame,[claim])
            field = getattr(single,claim.axis,None) or single.dimensions.get(claim.axis)
            if field is None or field.state != "stated":
                rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                    "reason":"attribute_quote_not_bound"})
                continue
            category = clause.attributes[claim.axis].classify(field.model_dump(mode="json"))
            if category and category.state in {"unproven", "conflict"}:
                rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                    "reason":"attribute_category_not_supported_by_quote"})
                continue
            if category and category.state == "recognized":
                category_codes.setdefault(claim.axis,set()).add(category.code)
                claim = claim.model_copy(update={"value":category.code})
            else:
                try:
                    claim = claim.model_copy(update={"value":bind_attribute_literal(claim.value,field.raw_text)})
                except ValueError as exc:
                    rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                        "reason":str(exc)})
                    continue
            if not clause.attributes[claim.axis].accepts_value(claim.value):
                rejected_claims.append({"source_fact_id":fid,"axis":claim.axis,"source_id":claim.source_id,
                    "reason":"attribute_value_outside_declared_pattern"})
                continue
            grounded.append(claim)
        context, flags = context_from_claims(frame,grounded)
        fields, dimensions = {}, dict(fact.identity.dimensions)
        for axis in wanted:
            field = getattr(context,axis,None) or context.dimensions.get(axis)
            if field is not None and field.state == "stated":
                if len(category_codes.get(axis,())) > 1:
                    field = field.model_copy(update={"state":"conflict"})
                category = clause.attributes[axis].classify(field.model_dump(mode="json"))
                if category and category.state in {"unproven", "conflict"}:
                    # An exact quotation can still fail to establish the
                    # proposed category (e.g. a measure label cited as actual).
                    # Keep that optional field unknown; the original proposal
                    # remains visible in the per-call diagnostic.
                    continue
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
        changed_ids.append(fid)
    return updates, changed_ids
