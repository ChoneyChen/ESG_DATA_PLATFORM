"""Neutral literal inventory; semantics and clause matching belong to the reader."""

import re
from dataclasses import dataclass
from esg_contracts.evidence import EvidenceRow, EvidenceCell, TextEvidence
from esg_data.units import UNIT_PATTERN, UNIT_RE, CURRENCY_UNIT, is_unit_expression, NUMERIC_SCALE
from esg_data.periods import MONTH_PATTERN, parse_period
from esg_data.text_search import sentence_ranges
from esg_data.normalize import fold

NUMBER = r"[-+−]?\d(?:(?:\d|[,，]\s*)*\d)?(?:\.\d+)?(?:[eE][-+]?\d+|\s*[×xX*]\s*10\s*(?:\^\s*[-+]?\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?"
LITERAL = re.compile(
    r"(?:不少于|不低于|不超过|不高于|至少|至多|大于|小于|低于|高于|多于|少于|≥|≤|>=|<=|>|<|≈|约|約|~)?\s*"
    + NUMBER
    + r"(?:\s*(?:至|到|[-~～—–])\s*"
    + NUMBER
    + r")?\s*[%％‰‱]?"
)
UNIT = re.compile(rf"^\s*({UNIT_PATTERN})(?![A-Za-z])", re.I)
STANDARD_DESIGNATION = re.compile(
    r"\b(?:ISO(?:\s*/\s*IEC)?|IEC|GRI|ESRS|SASB|NSF|IFRS)\s*[A-Z]?\s*\d+"
    r"(?:\s*[-–:./]\s*\d+)*",
    re.I,
)
PARENTHESIZED_ITEM = re.compile(r"[（(\[]\s*\d{1,3}\s*[）)\]](?=\s+\S)")
URL = re.compile(r"\bhttps?://[^\s<>\"']+", re.I)
NUMBERED_URL_REFERENCES = re.compile(
    r"(?:^|(?<=[\n\r.。:：]))\s*\d{1,3}\s+https?://[^\s<>\"']+"
    r"(?:\s+\d{1,3}\s+https?://[^\s<>\"']+)+", re.I)
CLASSIFICATION_REFERENCE = re.compile(
    r"\b(?:categor(?:y|ies)|scopes?)\s*\d+(?:\s*[-–—]\s*\d+)?"
    r"(?:\s*(?:,|&|and)\s*(?:and\s+)?\d+(?:\s*[-–—]\s*\d+)?)*", re.I)
PAGE_REFERENCE = re.compile(r"\b(?:pp?\.|page\s)\s*\d+(?:\s*[-–—]\s*\d+)?", re.I)
IDENTIFIER_REFERENCE = re.compile(
    r"\b(?:No\.?\s*|(?:suite|room|series|model|version|section|page)\s+)\d+(?:[-–:.]\s*\d+)*", re.I)
YEAR = r"(?:19|20|21)\d{2}"
PERIOD_TOKEN = rf"{YEAR}(?:\s*(?:[-~～—–]|to|through|until|至|到)\s*{YEAR})?"
PERIOD_REFERENCE = re.compile(
    rf"\b(?:in|since|during|year|FY|as\s+of|end\s+of|relative\s+to|"
    rf"(?:compared|comparison)\s+(?:with|to)(?:\s+(?:our|the))?)\s*(?P<period>{PERIOD_TOKEN})(?!\d)", re.I)
FRONTED_YEAR_REFERENCE = re.compile(
    rf"\bby\s*(?P<period>{YEAR})(?!\d)\s*(?=[,，]\s*\S)", re.I)
NAMED_DATE = re.compile(
    rf"\b(?:(?:\d{{1,2}}\s+)?(?:{MONTH_PATTERN})\s+(?:\d{{1,2}},?\s+)?{YEAR}|"
    rf"{YEAR}\s*[-/]\s*\d{{1,2}}\s*[-/]\s*\d{{1,2}})\b", re.I)
CHINESE_DATE = re.compile(r"\d{1,4}(?:\s*[-–—至到]\s*\d{1,4})?\s*[年月日]")
COMPOUND_CURRENCY_END = re.compile(rf"(?:/|／|per|每)\s*(?:{CURRENCY_UNIT})\s*$", re.I)
DENOMINATOR_SCALE = re.compile(
    rf"(?:\s*['’]\s*\d[\d,，]*(?![\d,，]|\.\d)|"
    rf"\s*{NUMERIC_SCALE}(?![\d,，]|\.\d)(?=\s*(?:[)）.;；]|$)))")


@dataclass(frozen=True)
class SourceContextSpan:
    start: int
    end: int
    kind: str
    quantity_unit_protects: bool = True


def source_context_spans(text):
    """Locate printed references, periods and unit metadata before reading digits.

    These source offsets never normalize the evidence. In particular a broken
    printed denominator scale is not repaired into a different numeric value.
    """
    spans = [SourceContextSpan(m.start(), m.end(), "identifier", False) for m in URL.finditer(text)]
    for pattern in (STANDARD_DESIGNATION, CLASSIFICATION_REFERENCE, PAGE_REFERENCE, IDENTIFIER_REFERENCE):
        spans.extend(SourceContextSpan(m.start(), m.end(), "identifier") for m in pattern.finditer(text))
    for reference in NUMBERED_URL_REFERENCES.finditer(text):
        spans.extend(SourceContextSpan(reference.start()+m.start(), reference.start()+m.end(), "footnote")
            for m in re.finditer(r"\b\d{1,3}(?=\s+https?://)", reference.group(), re.I))
    for marker in PARENTHESIZED_ITEM.finditer(text):
        if UNIT.match(fold(text[marker.end():])):
            continue
        if not text[:marker.start()].strip():
            spans.append(SourceContextSpan(marker.start(), marker.end(), "footnote"))
        elif PARENTHESIZED_ITEM.search(text[:marker.start()]):
            spans.append(SourceContextSpan(marker.start(), marker.end(), "identifier"))
    note = re.compile(r"(?:\b(?:notes?|footnotes?)|附[註注]|[脚腳][註注]|註|注)\s*(?:[（(\[]\s*)?\d+", re.I)
    spans.extend(SourceContextSpan(m.start(), m.end(), "footnote") for m in note.finditer(text))
    for marker in re.finditer(r"(?:^|[\n。；])\s*(\d{1,3})(?=[.)、]\s*\S)", text):
        spans.append(SourceContextSpan(marker.start(1), marker.end(1), "identifier"))
    for marker in re.finditer(r"(?:^|[\n。；])\s*(\d+)(?=\s*(?:由于|注[：:]|备注|说明[：:]|来源[：:]|because\b|due\s+to\b))", text, re.I):
        spans.append(SourceContextSpan(marker.start(1), marker.end(1), "footnote"))
    for marker in re.finditer(r"\d+(?=号)", text):
        if re.search(r"地址|街|路|大道|巷", text[:marker.start()]):
            spans.append(SourceContextSpan(marker.start(), marker.end(), "identifier"))
    for pattern in (PERIOD_REFERENCE, FRONTED_YEAR_REFERENCE, NAMED_DATE):
        for match in pattern.finditer(text):
            start, end = match.span("period") if "period" in pattern.groupindex else match.span()
            if parse_period(text[start:end]) is not None:
                spans.append(SourceContextSpan(start, end, "date"))
    spans.extend(SourceContextSpan(m.start(), m.end(), "date") for m in CHINESE_DATE.finditer(text))
    for match in UNIT_RE.finditer(text):
        raw = match.group()
        if not is_unit_expression(raw):
            continue
        start, end = match.span()
        # A leading scale can be a real quantity (10,000 MWh). Only an explicit
        # unit heading or enclosing unit parenthesis establishes that metadata.
        scale = re.match(rf"{NUMERIC_SCALE}\s*", raw)
        if scale:
            heading = re.search(r"(?:\bunits?\s*[:：]|[單单]位\s*[:：])\s*$", text[:start], re.I)
            parenthesis = re.search(r"[（(]\s*$", text[:start]) and re.match(r"\s*[）)]", text[end:])
            if not heading and not parenthesis:
                start += scale.end()
        if COMPOUND_CURRENCY_END.search(raw):
            suffix = DENOMINATOR_SCALE.match(text[end:])
            if suffix:
                end += suffix.end()
        spans.append(SourceContextSpan(start, end, "identifier", False))
    return spans


def text_spans(text, *, size=500, pack=False):
    """Expose whole source sentences, retaining exact offsets and all characters.

    Size is a packing target. A long sentence stays intact so a fragment cannot
    masquerade as a complete statement; model input limits remain authoritative.
    """
    if size <= 0:
        raise ValueError("Text span size must be positive")
    boundaries = [end for _, end in sentence_ranges(text.text)]
    ranges, start, end = [], 0, 0
    for boundary in boundaries:
        if not pack:
            if boundary > start:
                ranges.append((start, boundary))
            start = end = boundary
            continue
        if boundary - start > size and end > start:
            ranges.append((start, end))
            start = end
        end = boundary
    if end > start:
        ranges.append((start, end))
    return [text.model_copy(update={
        "span_id": f"{text.object_id}@{text.char_start + start}:{text.char_start + end}",
        "char_start": text.char_start + start,
        "text": text.text[start:end],
    }) for start, end in ranges]


def numeric_rows(text):
    result = []
    sentences = sentence_ranges(text.text)
    contexts = source_context_spans(text.text)
    for match in LITERAL.finditer(text.text):
        literal = match.group().strip()
        literal_start = match.start() + len(match.group()) - len(match.group().lstrip())
        literal_end = literal_start + len(literal)
        if not literal:
            continue
        after = text.text[match.end():]
        unit_match = UNIT.match(fold(after))
        unit = (
            after[unit_match.start(1):unit_match.end(1)]
            if unit_match
            else (literal[-1] if literal.endswith(("%", "％", "‰", "‱")) else None)
        )
        kind = literal_kind(text.text, literal_start, literal_end, unit, context_spans=contexts)
        start, end = next(((start, end) for start, end in sentences
            if start <= literal_start < end), (0, len(text.text)))
        row_id = f"{text.object_id}:literal:{text.char_start + literal_start}"
        label = text.model_copy(
            update={"text": text.text[start:end], "char_start": text.char_start + start}
        )
        result.append(
            EvidenceRow(
                row_id=row_id,
                label=text.text[start:end],
                unit_raw=unit,
                label_sources=[label],
                unit_sources=[text] if unit and unit in text.text else [],
                cells=[
                    EvidenceCell(
                        candidate_kind=kind,
                        page_index=text.page_index,
                        cell_id=f"{text.object_id}@{text.char_start + literal_start}:{text.char_start + literal_end}",
                        column_id="value",
                        raw_text=literal,
                        source_object_id=text.object_id,
                        source_object_type="cell" if text.object_type == "cell" else "text_block",
                        char_start=text.char_start + literal_start,
                        char_end=text.char_start + literal_end,
                        bbox=text.bbox,
                    )
                ],
            )
        )
    return result


def literal_kind(text, start, end, unit, *, context_spans=None):
    """Physical candidate typing, independent of any clause or target vocabulary.

    A digit in a formula/identifier is not a measurement. Ambiguous prose still
    reaches the reader; a missing unit alone never rejects a genuine count.
    """
    before, after = text[:start], text[end:]
    contexts = source_context_spans(text) if context_spans is None else context_spans
    for span in contexts:
        if span.start <= start and end <= span.end and not (unit and span.quantity_unit_protects):
            return span.kind
    if re.search(r"[A-Za-zα-ωΑ-Ω]\Z", before):
        token = re.search(r"[A-Za-zα-ωΑ-Ω][A-Za-zα-ωΑ-Ω\d]*$", before)
        return "formula" if token and re.fullmatch(r"(?:[A-Z][a-z]?)+", token[0]) else "identifier"
    if re.match(r"[A-Za-z]", after) and not unit:
        return "identifier"
    return "measurement" if unit else "unresolved"
