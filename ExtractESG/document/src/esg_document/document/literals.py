"""Neutral literal inventory; semantics and clause matching belong to the reader."""

import re
from esg_contracts.evidence import EvidenceRow, EvidenceCell, TextEvidence
from esg_data.units import UNIT_PATTERN, is_unit_expression, NUMERIC_SCALE
from esg_data.text_search import sentence_ranges
from esg_data.normalize import fold

NUMBER = r"[-+−]?\d(?:[\d,，]*\d)?(?:\.\d+)?(?:[eE][-+]?\d+|\s*[×xX*]\s*10\s*(?:\^\s*[-+]?\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?"
LITERAL = re.compile(
    r"(?:不少于|不低于|不超过|不高于|至少|至多|大于|小于|低于|高于|多于|少于|≥|≤|>=|<=|>|<|≈|约|約|~)?\s*"
    + NUMBER
    + r"(?:\s*(?:至|到|[-~～—–])\s*"
    + NUMBER
    + r")?\s*[%％‰‱]?"
)
UNIT = re.compile(rf"^\s*({UNIT_PATTERN})(?![A-Za-z])", re.I)


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
        kind = literal_kind(text.text, literal_start, literal_end, unit)
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
                        source_object_type="text_block",
                        char_start=text.char_start + literal_start,
                        char_end=text.char_start + literal_end,
                        bbox=text.bbox,
                    )
                ],
            )
        )
    return result


def literal_kind(text, start, end, unit):
    """Physical candidate typing, independent of any clause or target vocabulary.

    A digit in a formula/identifier is not a measurement. Ambiguous prose still
    reaches the reader; a missing unit alone never rejects a genuine count.
    """
    before, after = text[:start], text[end:]
    literal = text[start:end].strip()
    if re.fullmatch(NUMERIC_SCALE, literal) and unit:
        # A parenthesized scale in a heading describes the unit. The actual
        # quantity remains in the table or chart beside that heading.
        opening = max(before.rfind("("), before.rfind("（"))
        closing = re.match(r"\s*" + re.escape(unit) + r"\s*[)）]", after)
        if opening >= 0 and closing and is_unit_expression(literal + " " + unit):
            return "identifier"
    if not unit:
        if ((not before.strip() or re.search(r"[\n。；]\s*$", before))
            and re.match(r"\s*[.)、]\s*\S", after)):
            return "identifier"
        if re.search(r"\b(?:No\.?|suite|room)\s*$", before, re.I):
            return "identifier"
        if after.lstrip().startswith("号") and re.search(r"地址|街|路|大道|巷", before):
            return "identifier"
        if re.fullmatch(r"(?:19|20|21)\d{2}", text[start:end].strip()) and re.search(r"\b(?:in|since|during|year|FY|as\s+of|end\s+of)\s*$", before, re.I):
            return "date"
        if re.search(r"\b(?:series|model|version|scope|section|page|ISO|IEC|GRI|ESRS|SASB|NSF|IFRS)\s*$", before, re.I):
            return "identifier"
    if after.lstrip().startswith(("年", "月", "日")):
        return "date"
    if re.search(r"[A-Za-zα-ωΑ-Ω]\Z", before):
        token = re.search(r"[A-Za-zα-ωΑ-Ω][A-Za-zα-ωΑ-Ω\d]*$", before)
        return "formula" if token and re.fullmatch(r"(?:[A-Z][a-z]?)+", token[0]) else "identifier"
    if re.match(r"[A-Za-z]", after) and not unit:
        return "identifier"
    if (not before.strip() or re.search(r"[\n。；]\s*$", before)) and re.match(
        r"\s*(?:由于|注[：:]|备注|说明[：:]|来源[：:]|because\b|due\s+to\b)", after, re.I
    ):
        return "footnote"
    return "measurement" if unit else "unresolved"
