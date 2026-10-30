"""Neutral literal inventory; semantics and clause matching belong to the reader."""

import re
from esg_contracts.evidence import EvidenceRow, EvidenceCell, TextEvidence

NUMBER = r"[-+−]?\d[\d,，]*(?:\.\d+)?(?:[eE][-+]?\d+|\s*[×xX*]\s*10\s*(?:\^\s*[-+]?\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?"
LITERAL = re.compile(
    r"(?:不少于|不低于|不超过|不高于|至少|至多|大于|小于|低于|高于|多于|少于|≥|≤|>=|<=|>|<|≈|约|約|~)?\s*"
    + NUMBER
    + r"(?:\s*(?:至|到|[-~～—–])\s*"
    + NUMBER
    + r")?\s*[%％‰‱]?"
)
UNIT = re.compile(
    r"^\s*((?:万|萬|亿|億|百万|百萬|千)?(?:吨二氧化碳当量|吨标准煤|吨|噸|立方米|千瓦时|兆瓦时|小时|人|名|件|起|天|元|MWh|GWh|kWh|tCO2e|kg|tonnes?|m³|m3|hours?|days?|%|％))",
    re.I,
)


def text_spans(text, *, size=500):
    return [
        text.model_copy(
            update={
                "span_id": f"{text.object_id}@{text.char_start + start}:{text.char_start + min(start + size, len(text.text))}",
                "char_start": text.char_start + start,
                "text": text.text[start : start + size],
            }
        )
        for start in range(0, len(text.text), size)
    ]


def numeric_rows(text):
    result = []
    for match in LITERAL.finditer(text.text):
        literal = match.group().strip()
        literal_start = match.start() + len(match.group()) - len(match.group().lstrip())
        literal_end = literal_start + len(literal)
        if not literal:
            continue
        unit_match = UNIT.match(text.text[match.end() :])
        unit = (
            unit_match.group(1)
            if unit_match
            else (literal[-1] if literal.endswith(("%", "％", "‰", "‱")) else None)
        )
        kind = literal_kind(text.text, literal_start, literal_end, unit)
        start = max(0, match.start() - 70)
        end = min(len(text.text), match.end() + 35)
        left = max(text.text.rfind(mark, start, match.start()) for mark in "。；;")
        if left >= start:
            start = left + 1
        ends = [text.text.find(mark, match.end(), end) for mark in "。；;"]
        ends = [position for position in ends if position >= 0]
        if ends:
            end = min(ends)
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
    if after.lstrip().startswith(("年", "月", "日")):
        return "date"
    if re.search(r"[A-Za-zα-ωΑ-Ω]$", before):
        token = re.search(r"[A-Za-zα-ωΑ-Ω][A-Za-zα-ωΑ-Ω\d]*$", before)
        return "formula" if token and re.fullmatch(r"(?:[A-Z][a-z]?)+", token[0]) else "identifier"
    if re.match(r"[A-Za-z]", after) and not unit:
        return "identifier"
    if (not before.strip() or re.search(r"[\n。；]\s*$", before)) and re.match(
        r"\s*(?:由于|注[：:]|备注|说明[：:]|来源[：:]|because\b|due\s+to\b)", after, re.I
    ):
        return "footnote"
    return "measurement" if unit else "unresolved"
