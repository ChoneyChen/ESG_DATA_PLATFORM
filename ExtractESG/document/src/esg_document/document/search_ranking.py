"""Rank evidence for an explicit query; keep sibling table concepts separate."""
import re
import unicodedata
from esg_data.normalize import fold
from esg_data.text_search import lexical_score
from esg_data.literals import parse_measure
from esg_data.units import unit_dimension


def query_terms(values):
    return list(dict.fromkeys(part.strip() for value in values
        for part in ([value] if value.startswith("re:") else re.split(r"[。；;，,、|\n]",value))
        if len(part.strip()) >= 2))


def phrase_score(terms, text):
    spaced = unicodedata.normalize("NFKC", fold(text or ""))
    compact = re.sub(r"\s+", "", spaced)
    score = 0
    for term in terms:
        normalized = unicodedata.normalize("NFKC", fold(term)).strip()
        if not normalized:
            continue
        if normalized.isascii():
            pattern = r"(?<![a-z0-9])" + r"\s*".join(re.escape(part) for part in normalized.split()) + r"(?![a-z0-9])"
            hit = bool(re.search(pattern,spaced))
        else:
            hit = re.sub(r"\s+", "", normalized) in compact
        if hit:
            score += min(6,len(term)/3)
    return score


def _score(intent, terms, text, kind, *, negative_text=None, supporting_text=""):
    matcher = lexical_score if intent.matching == "lexical" else phrase_score
    score = 8*matcher(terms,text) + 20*phrase_score(intent.exact_terms,text)
    if kind == "table":
        score += phrase_score(intent.structural_anchors,text) + .2*matcher(terms,supporting_text)
    if score <= 0 or any(not phrase_score(group,text) for group in intent.required_term_groups):
        return 0
    if intent.source_priority and intent.source_priority[0] == kind:
        score *= 4
    score += sum(2 for group in [*intent.topic_term_groups,*intent.role_term_groups] if phrase_score(group,text))
    score += .1*phrase_score(intent.context_terms,text)
    if phrase_score(intent.negative_terms,text if negative_text is None else negative_text):
        score *= .25
    return score


def text_score(intent, terms, text):
    return 0 if phrase_score(intent.excluded_object_terms,text) else _score(intent,terms,text,"text")


def table_score(intent, terms, table, view):
    whole = " ".join([str(table.get("title") or ""),*[str(cell.get("text") or "") for cell in table.get("cells",[])]])
    if phrase_score(intent.excluded_object_terms,whole):
        return 0
    if intent.data_kind != "quantitative" or not view or not view.lines:
        supporting = " ".join(filter(None,[view.title,view.caption,view.heading_above,*[note.text for note in view.notes]])) if view else ""
        return _score(intent,terms,whole,"table",supporting_text=supporting)
    shared = " ".join(filter(None,[view.title,view.caption,view.heading_above,
        " ".join(part for path in view.column_paths.values() for part in path)]))
    best = 0
    for line in view.lines:
        local = " ".join(filter(None,[line.label,line.section]))
        score = _score(intent,terms,shared+" "+local,"table",negative_text=local)
        dimension = unit_dimension(line.unit)
        if dimension and any(parse_measure(cell.text) for cell in line.values.values()):
            if not intent.unit_dimensions or dimension in intent.unit_dimensions:
                score *= 4
        best = max(best,score)
    return best
