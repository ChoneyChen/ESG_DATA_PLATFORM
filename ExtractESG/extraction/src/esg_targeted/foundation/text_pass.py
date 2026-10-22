"""Second pass for datapoints disclosed only in narrative text.

Candidate paragraphs are retrieved with the same concept vocabulary as the
guards. The model names the paragraph, datapoint and the literal value/unit;
the program verifies both literals occur in that paragraph before binding.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from esg_targeted.foundation import guards as g
from esg_targeted.foundation.catalog import BY_CODE
from esg_targeted.foundation.kernel import Observation, request_digest, scope2_method_from_text, unit_dimension
from esg_targeted.foundation.tables import GROUP_SCOPE_RE, SUBSET_SCOPE_RE, clean_text, parse_number, period_years
from esg_targeted.foundation.textnorm import fold
from esg_targeted.evidence.units import canonical_unit_id

TEXT_GROUPS = {
    "energy": ("E1-5_01", "E1-5_05", "E1-5_09"),
    "ghg": ("E1-6_07", "E1-6_09", "E1-6_10", "E1-6_11"),
    "water": ("E3-4_01", "E3-4_02", "E3-4_03", "E3-4_11", "E3-4_12"),
    "pollution": ("E2-4_02", "E2-4_03", "E2-4_09"),
    "substances": ("E2-5_02",),
}
RETRIEVAL = {
    "E1-5_01": (g.ENERGY_TOTAL_RE,),
    "E1-5_05": (g.RENEWABLE_RE,),
    "E1-5_09": (g.RENEWABLE_RE,),
    "E1-6_07": (g.SCOPE1_RE,),
    "E1-6_09": (g.SCOPE2_RE,),
    "E1-6_10": (g.SCOPE2_RE,),
    "E1-6_11": (g.SCOPE3_RE,),
    "E2-4_02": (g.AIR_POLLUTANT_RE,),
    "E2-4_03": (g.WATER_POLLUTANT_RE,),
    "E2-4_09": (g.POLLUTION_METHOD_RE,),
    "E2-5_02": (g.SUBSTANCE_RE,),
    "E3-4_01": (g.CONSUMPTION_RE,),
    "E3-4_02": (g.RISK_AREA_RE,),
    "E3-4_03": (g.RECYCLE_RE,),
    "E3-4_11": (g.WITHDRAWAL_RE,),
    "E3-4_12": (g.DISCHARGE_RE,),
}
# A value statement prints a number with a unit of the datapoint's kind; a
# paragraph with only years or counts cannot state one of these values.
_UNITS = {
    "energy": r"[mgkt]wh|千瓦时|千瓦時|兆瓦时|兆瓦時|吉瓦时|度电|度電|亿度|億度|万度|萬度|[gtp]j|吉焦|焦耳|标准煤|標準煤|标煤|tce|toe|%|％",
    "ghg": r"吨|噸|t\b|tco|co2|co₂|二氧化碳|碳当量|kt|mt\b|万吨|萬噸",
    "water": r"立方米|m3|m³|吨|噸|升|ml\b|megalit|liter|litre|gallon",
    "pollution": r"吨|噸|t\b|kg|千克|公斤|mg|tonne",
    "substances": r"吨|噸|t\b|kg|千克|公斤|tonne|种|種|项|項",
}
QUANTITY_RE = {group: re.compile(r"\d[\d,，]*(?:\.\d+)?\s*(?:万|萬|亿|億|百万|百萬|千)?\s*(?:" + units + ")", re.I)
               for group, units in _UNITS.items()}
GROUP_OF = {code: group for group, codes in (("energy", ("E1-5_01", "E1-5_05", "E1-5_09")),
                                             ("ghg", ("E1-6_07", "E1-6_09", "E1-6_10", "E1-6_11")),
                                             ("water", ("E3-4_01", "E3-4_02", "E3-4_03", "E3-4_11", "E3-4_12")),
                                             ("pollution", ("E2-4_02", "E2-4_03")),
                                             ("substances", ("E2-5_02",))) for code in codes}
METHOD_WORD_RE = re.compile(r"(?:监测|检测|在线|测量|测算|估算|计算|核算|排放因子|物料衡算|系数|monitor|measur|estimat|calculat|emission\s+factor|mass\s+balance)", re.I)
MAX_BLOCK_CHARS = 360
# Words that make the figure after them a bound or an estimate ("超过55亿度", "近90%").
APPROXIMATE_RE = re.compile(r"(?:近|约|約|超过|超過|逾|超|大约|大約|接近|将近|將近|nearly|approximately|about|around|over|more\s+than|almost)\s*$", re.I)


@dataclass
class TextBlock:
    bid: str
    block_id: str
    page_number: int
    text: str


def retrieve_blocks(pages: list[dict], codes: tuple[str, ...], *, reporting_year: int | None, limit: int = 8) -> list[TextBlock]:
    scored = []
    for payload in pages:
        page_number = int((payload.get("page") or {}).get("page_number") or 0)
        for block in payload.get("blocks") or []:
            if block.get("block_type") in {"table_markdown", "table", "figure", "image", "header", "footer"}:
                continue
            text = clean_text(block.get("text"))
            if len(text) < 12:
                continue
            folded = fold(text)
            narrative = "E2-4_09" in codes and g.is_pollution_method_text(text)
            hits = 0
            for code in codes:
                patterns = RETRIEVAL[code]
                # A value needs a figure of its kind in the paragraph; a method
                # statement about named pollutants is relevant without one.
                if code in GROUP_OF and not QUANTITY_RE[GROUP_OF[code]].search(folded) and not narrative:
                    continue
                if any(p.search(folded) for p in patterns):
                    hits += 1
            if not hits and not narrative:
                continue
            score = hits * 2 + (2 if narrative else 0) + (2 if narrative and METHOD_WORD_RE.search(folded) else 0)
            if reporting_year and str(reporting_year) in text:
                score += 2
            if re.search(r"(?:%|吨|噸|tco2|co2e|二氧化碳当量|mwh|gwh|kwh|千瓦时|立方米|m3|m³|gj|tj)", folded):
                score += 2
            scored.append((score, page_number, block["block_id"], text))
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen, seen = [], set()
    for score, page_number, block_id, text in scored:
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        chosen.append(TextBlock(f"b{len(chosen) + 1}", block_id, page_number, _window(text, codes)))
        if len(chosen) >= limit:
            break
    return chosen


def _window(text: str, codes: tuple[str, ...]) -> str:
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    folded = fold(text)
    first = min((m.start() for code in codes for p in RETRIEVAL[code] for m in [p.search(folded)] if m), default=0)
    start = max(0, first - 80)
    return text[start:start + MAX_BLOCK_CHARS]


def build_text_request(group: str, codes: tuple[str, ...], blocks: list[TextBlock], reporting_year: int | None):
    year_text = str(reporting_year) if reporting_year else "the latest reporting period"
    targets = [f"- {c} {BY_CODE[c].label}: {BY_CODE[c].definition}" for c in codes]
    rules = [
        "facts: one item per directly stated value. value = the number exactly as written (keep thousands separators); unit = the unit exactly as written next to it.",
        "Do not compute, convert or combine numbers. Skip values for prior years, targets, changes, intensities, subsidiaries or projects.",
        "Scope 2 values count as E1-6_09 only if the text says location-based and E1-6_10 only if it says market-based.",
    ]
    if "E2-4_09" in codes:
        rules.append(
            "E2-4_09 is always a methodology statement: value=null, unit=null. Emit one E2-4_09 item for each "
            "paragraph that explains how air pollutant amounts are measured, monitored, calculated or estimated "
            "(formula, emission factor, guideline, monitoring scheme). Do not skip it because it has no number.")
    rules.append("If nothing matches, facts is []. Output only the JSON object.")
    instructions = "\n".join([
        "You read short paragraphs from a company sustainability report and extract target ESG datapoints that are stated directly in the text.",
        f"Reporting period: {year_text}. Only extract values for that period and for the whole reporting company/group.",
        "TARGET DATAPOINTS:", *targets, *rules,
    ])
    context = "\n".join(f"{b.bid} (page {b.page_number}): {b.text}" for b in blocks)
    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array", "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "block": {"type": "string", "enum": [b.bid for b in blocks]},
                        "target": {"type": "string", "enum": list(codes)},
                        "value": {"anyOf": [{"type": "string", "maxLength": 24}, {"type": "null"}]},
                        "unit": {"anyOf": [{"type": "string", "maxLength": 24}, {"type": "null"}]},
                    },
                    "required": ["block", "target", "value", "unit"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    }
    return instructions, context, schema, 64 + 40 * min(12, len(blocks) * 2)


def _find_value(text: str, value: str) -> int:
    """Locate a value even when OCR spacing differs ("257 万" vs "257万")."""
    index = text.find(value)
    if index >= 0:
        return index
    compact = _compact(value)
    if not compact:
        return -1
    # Map the compacted position back to the raw string.
    offset = 0
    for position, char in enumerate(text):
        if _SPACE_RE.match(char):
            continue
        if text[position:position + len(compact)] == compact or _compact(text[position:]).startswith(compact):
            if not text[offset:position].replace(" ", "") or True:
                return position
        offset = position
    return -1


def _sentence_around(text: str, value: str) -> str:
    index = _find_value(text, value)
    if index < 0:
        return text
    start = max(text.rfind("。", 0, index), text.rfind(". ", 0, index), text.rfind("；", 0, index), -1) + 1
    ends = [pos for pos in (text.find("。", index), text.find("；", index), text.find(". ", index)) if pos >= 0]
    end = min(ends) if ends else len(text)
    return text[start:end + 1]


def _value_clause(text: str, value: str, *, before: int = 40, after: int = 16) -> str:
    """The short clause a number belongs to, not the whole sentence.

    A sentence may state the value and also a year-on-year change of a
    different number ("雨水回用量达257万立方米，较2024年增加162万立方米").
    Judging the concept from the whole sentence turns the value into a change.
    """
    index = _find_value(text, value)
    if index < 0:
        return text
    # Stop at the sentence boundary, then keep one neighbouring clause: a verb
    # such as 减少 can govern a comma-separated list ("减少 CO2 143 万吨、粉尘 38 吨").
    low = max(0, index - before)
    hard = max(text.rfind(mark, low, index) for mark in "。；;")
    cuts = sorted({text.rfind(mark, max(low, hard + 1), index) for mark in "，,、"})
    cuts = [position for position in cuts if position >= 0]
    start = (cuts[-2] if len(cuts) >= 2 else cuts[-1]) + 1 if cuts else index
    end = min(len(text), index + len(value) + after)
    for mark in "。；;，,、":
        position = text.find(mark, index + len(value))
        if 0 <= position < end:
            end = position
    return text[start:end]


def page_scope2_methods(pages: list[dict]) -> dict[int, str]:
    """Pages whose text states exactly one Scope 2 method (location- or market-based)."""
    methods = {}
    for payload in pages:
        texts = [clean_text(b.get("text")) for b in payload.get("blocks") or []
                 if b.get("block_type") not in {"table_markdown", "table", "header", "footer"}]
        texts = [t for t in texts if g.SCOPE2_RE.search(fold(t)) or "scope 2" in fold(t)]
        method = scope2_method_from_text(*texts) if texts else None
        if method:
            methods[int((payload.get("page") or {}).get("page_number") or 0)] = method
    return methods


_SPACE_RE = re.compile(r"\s+")


def _compact(text: str) -> str:
    """Paragraph and OCR spacing must not decide whether a value is verbatim."""
    return _SPACE_RE.sub("", text or "")


def _verbatim(part: str, text: str) -> bool:
    return part in text or _compact(part) in _compact(text)


def _split_printed_unit(value: str | None, unit: str | None, text: str) -> tuple[str | None, str | None]:
    """Separate a number from its printed unit, including duplicated model units.

    A model sometimes answers "257万立方米" as the value with "万立方米 立方米"
    as the unit. The value must stay a bare number and the unit must be the one
    the paragraph actually prints.
    """
    if unit and " " in unit.strip():
        pieces = [piece for piece in unit.split() if piece]
        printed = [piece for piece in pieces if piece in text]
        unit = (max(printed, key=len) if printed else
                max(pieces, key=lambda piece: len(piece) if piece in (value or "") else 0))
    if value and unit and value.endswith(unit):
        value = value[: -len(unit)].strip()
    return value, unit


def bind_text(payload: dict, blocks: list[TextBlock], reporting_year: int | None,
              page_methods: dict[int, str] | None = None) -> list[Observation]:
    by_bid = {b.bid: b for b in blocks}
    observations = []
    for fact in payload.get("facts") or []:
        block = by_bid.get(fact.get("block"))
        code = fact.get("target")
        if block is None or code not in BY_CODE:
            continue
        metric = BY_CODE[code]
        value_raw = (fact.get("value") or "").strip() or None
        unit_raw = (fact.get("unit") or "").strip() or None
        if value_raw and value_raw.endswith(("%", "％")) and not unit_raw:
            value_raw, unit_raw = value_raw[:-1].strip(), value_raw[-1]
        value_raw, unit_raw = _split_printed_unit(value_raw, unit_raw, block.text)
        flags = ["from_narrative_text"]
        status = "review"
        if metric.kind == "narrative":
            if g.is_pollution_method_text(block.text):
                observations.append(_obs(code, "review", None, None, None, None, block, block.text, flags))
            continue
        if value_raw is None or not _verbatim(value_raw, block.text):
            flags.append("value_not_verbatim_in_paragraph")
            observations.append(_obs(code, "rejected", value_raw, None, unit_raw, None, block, block.text, flags))
            continue
        if unit_raw and not _verbatim(unit_raw, block.text):
            flags.append("unit_not_verbatim_in_paragraph")
            unit_raw = None
        sentence = _sentence_around(block.text, value_raw)
        clause = _value_clause(block.text, value_raw)
        number = parse_number(value_raw.replace(" ", ""))
        dimension = unit_dimension(unit_raw)
        guard_code = "SCOPE2" if code in ("E1-6_09", "E1-6_10") else code
        guard = g.check(guard_code, clause, None, unit_raw, dimension)
        flags.extend(guard.flags)
        if code in ("E1-6_09", "E1-6_10"):
            stated = scope2_method_from_text(sentence)
            page_method = (page_methods or {}).get(block.page_number)
            if stated is None and page_method:
                code, stated = page_method, page_method
                flags.append("scope2_method_from_page_text")
                guard = g.GuardResult(guard.ok, guard.status or ("review" if guard.ok else "rejected"), guard.flags)
            if stated != code:
                flags.append("scope2_method_not_stated_in_sentence")
                guard = g.GuardResult(False, "rejected", guard.flags)
        years = period_years(sentence)
        if reporting_year and years and reporting_year not in years:
            flags.append("sentence_names_other_period")
            status = "rejected"
        elif not guard.ok or number is None:
            status = "rejected"
        elif reporting_year and (reporting_year in years or re.search(r"报告期|本年|年内|reporting\s+period|this\s+year|in\s+20\d\d", fold(sentence))):
            status = "accepted" if guard.status is None else "review"
        if unit_raw is None:
            flags.append("unit_missing")
            if status == "accepted":
                status = "review"
        if APPROXIMATE_RE.search(block.text[:max(0, block.text.find(value_raw))][-8:]):
            flags.append("value_is_approximate")
            if status == "accepted":
                status = "review"
        basis = "unknown"
        if SUBSET_SCOPE_RE.search(fold(sentence)) and not GROUP_SCOPE_RE.search(fold(sentence)):
            # One site's or business's figure in a sentence is not the group's
            # value; it stays visible as a rejected candidate.
            basis = "subset"
            flags.append("sentence_limited_to_part_of_group")
            status = "rejected"
        observations.append(_obs(code, status, value_raw, str(number) if number is not None else None, unit_raw,
                                 reporting_year, block, sentence, flags, basis))
    return observations


def _obs(code, status, value_raw, value, unit_raw, period_year, block: TextBlock, sentence: str, flags,
         basis: str = "unknown") -> Observation:
    rejected_by = next((f for f in reversed(flags) if f != "from_narrative_text"), "concept") if status == "rejected" else None
    metric = BY_CODE[code]
    return Observation(
        code=code, metric_id=metric.metric_id, package_id=metric.package_id, status=status,
        value_raw=value_raw, value=value, unit_raw=unit_raw, unit_id=canonical_unit_id(unit_raw) if unit_raw else None,
        period=str(period_year) if period_year else None, period_year=period_year, entity_basis=basis,
        member=g.pollutant_key(sentence) if metric.group in ("air", "water_pollutants") else None,
        table_id=block.block_id, page_number=block.page_number, row_id=block.bid, row_label=sentence[:200],
        cell_id=None, column=None, note_block_id=block.block_id, proposed_code=code, flags=list(flags),
        rejected_by=rejected_by,
    )


def text_task_digest(group: str, codes: tuple[str, ...], pages: list[dict], reporting_year: int | None) -> str | None:
    blocks = retrieve_blocks(pages, codes, reporting_year=reporting_year)
    if not blocks:
        return None
    instructions, context, schema, _ = build_text_request(group, codes, blocks, reporting_year)
    return request_digest(instructions, context, schema)


def run_text_task(model, group: str, codes: tuple[str, ...], pages: list[dict], reporting_year: int | None) -> dict:
    blocks = retrieve_blocks(pages, codes, reporting_year=reporting_year)
    record = {"task_id": f"text:{group}", "table_id": f"text:{group}", "page_number": 0, "groups": [group],
              "codes": list(codes), "lines": len(blocks), "blocks": [b.__dict__ for b in blocks]}
    if not blocks:
        record.update(status="no_candidates", observations=[])
        return record
    instructions, context, schema, max_tokens = build_text_request(group, codes, blocks, reporting_year)
    record["context_chars"] = len(context)
    record["request_sha"] = request_digest(instructions, context, schema)
    started = time.perf_counter()
    try:
        raw, telemetry = model.generate_structured(context=context, instructions=instructions, template=schema, max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001
        record.update(status="model_failed", error=f"{type(exc).__name__}: {exc}"[:500], elapsed_seconds=round(time.perf_counter() - started, 2))
        return record
    record.update(raw_output=raw, input_tokens=telemetry.get("input_tokens"), output_tokens=telemetry.get("generation_tokens"),
                  elapsed_seconds=round(time.perf_counter() - started, 2))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        record.update(status="invalid_json", error=str(exc))
        return record
    observations = bind_text(payload, blocks, reporting_year, page_scope2_methods(pages))
    record.update(status="ok", payload=payload, observations=[o.to_dict() for o in observations])
    if any(o.code == "E2-4_09" and o.status != "rejected" for o in observations):
        record["method_notes"] = [{"block_id": o.note_block_id, "text": o.row_label, "page_number": o.page_number}
                                  for o in observations if o.code == "E2-4_09"]
    return record
