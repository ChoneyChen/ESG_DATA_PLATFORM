"""Compact native-text hints for reading printed rows and their notes."""

from collections import defaultdict
import re
import unicodedata

from esg_data.literals import parse_number
from esg_data.normalize import fold


ANNUAL_HEADER = re.compile(r"(?<![\d.,])(?:19|20)\d{2}(?:[ \t]+(?:19|20)\d{2})+(?![\d.,])")
NUMBER = r"[−–-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?\s*[%％]?"
CELL = rf"(?:{NUMBER}|N[./]?A|[-–—/／])"
NOTE_MARKER = re.compile(r"(?:\bnotes?\s+|注(?:释)?\s*)(\d{1,2})(?!\d)", re.I)
NOTE_LINE = re.compile(r"^\s*(?:[（(](\d{1,2})[)）]|(?:note\s+|注\s*)(\d{1,2})\s*[:：])\s*(.+)", re.I)
EMPTY_PRINTED = re.compile(r"(?:[-–—－/／]+|n[/.]?a|none|null|无)[.。]?", re.I)
YEAR = re.compile(r"(?<!\d)(?:19|20|21)\d{2}(?!\d)")


def quote_key(text):
    normalized = fold(unicodedata.normalize("NFKC", text))
    return "".join(character for character in normalized if character.isalnum())


def annual_headers(page_text):
    """Return exact year columns in print order, excluding dates and prose ranges."""
    return [list(years) for years in dict.fromkeys(
        tuple(match[0].split()) for match in ANNUAL_HEADER.finditer(page_text)
        if 2 <= len(match[0].split()) <= 8 and len(set(match[0].split())) == len(match[0].split())
    )]


def annual_cells(page_text, label, years, *, note_marker=None, excluded_words=()):
    """Recognize an unambiguous row as a reading hint, never as an extracted fact."""
    if not label or not years or len(needle := quote_key(label)) < 4:
        return None
    name = re.compile(r"[^a-z0-9\u3400-\u9fff]*".join(map(re.escape, needle)), re.I)
    columns = re.compile(r"(?<![\w./-])" + r"\s+".join(f"({CELL})" for _ in years) + r"(?![\w.,/-])", re.I)
    lines = page_text.splitlines()
    candidates = set()
    for index, line in enumerate(lines):
        # The row heading may wrap. Requiring its initial letters on the first
        # line allows matching once without scanning adjacent unrelated rows.
        if not quote_key(line).startswith(needle[:4]):
            continue
        region = "\n".join(lines[index:index + 9])
        normalized = fold(unicodedata.normalize("NFKC", region))
        match = name.match(normalized.lstrip())
        if not match:
            continue
        tail = normalized.lstrip()[match.end():]
        # Replace reference markers with whitespace of the same length. Their
        # number is not a measurement, and original offsets still locate a citation.
        unmarked = NOTE_MARKER.sub(lambda marker: " " * len(marker[0]), tail)
        if cells := columns.search(unmarked):
            label_and_prefix = normalized.lstrip()[:match.end() + cells.start()]
            if note_marker and note_marker not in NOTE_MARKER.findall(label_and_prefix):
                continue
            qualifiers = quote_key(tail[:cells.start()])
            if any(quote_key(word) in qualifiers for word in excluded_words):
                continue
            candidates.add(tuple(cell.strip() for cell in cells.groups()))
    if len(candidates) != 1:
        return None
    return dict(zip(years, candidates.pop(), strict=True))


def _reported_annual_rows(items, years, quantitative_keys):
    """Group the provider's annual measurements by clause and source label."""
    grouped = defaultdict(dict)
    for item in items:
        if not isinstance(item, dict) or item.get("clause_key") not in quantitative_keys:
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        measurements = item.get("measurements")
        for row in measurements if isinstance(measurements, list) else [item]:
            if not isinstance(row, dict) or not isinstance(row.get("period"), str):
                continue
            period = row["period"].strip()
            if period in years:
                grouped[(item["clause_key"], label)][period] = row.get("value")
    return grouped


def declared_annual_rows(page_text, years, clauses):
    """Use a clause opt-in for exact, complete printed quantity rows."""
    expected = {}
    for key, clause in clauses.items():
        vocabulary = clause.reading.vocabulary
        for label in vocabulary.get("annual_read_labels", ()):
            cells = annual_cells(page_text, label, years, excluded_words=vocabulary.get("annual_read_exclusions", ()))
            if cells and all(parse_number(cell) is not None for cell in cells.values()):
                identity = (key, tuple(cells.items()))
                expected.setdefault(identity, {"clause_key": key, "label": label, "periods": years, "printed_cells": cells})
    return list(expected.values())[:16]


def missing_annual_periods(items, page_text, years, quantitative_keys, declared_rows=()):
    """Find omitted cells confirmed by a transcribed column or explicit clause label."""
    grouped = _reported_annual_rows(items, years, quantitative_keys)
    missing = []
    for (key, label), observed in grouped.items():
        if len(observed) == len(years) or not (printed := annual_cells(page_text, label, years)):
            continue
        if not any(isinstance(raw, str) and (point := parse_number(raw)) is not None
                   and point == parse_number(printed[year]) for year, raw in observed.items()):
            continue
        periods = [year for year in years if year not in observed and not EMPTY_PRINTED.fullmatch(printed[year])]
        if periods:
            missing.append({"clause_key": key, "label": label, "periods": periods,
                            "printed_cells": {period: printed[period] for period in periods}})
    # A named, exact native row may be absent from the first provider response.
    # It becomes a reading hint only; no fact is created from it locally.
    represented = {(row["clause_key"], year, parse_number(cell))
                   for row in missing for year, cell in row["printed_cells"].items()}
    represented.update((key, year, parse_number(raw)) for (key, _), measurements in grouped.items()
                       for year, raw in measurements.items() if isinstance(raw, str))
    for row in declared_rows:
        missing_cells = {year: cell for year, cell in row["printed_cells"].items()
                         if (row["clause_key"], year, parse_number(cell)) not in represented}
        if missing_cells:
            missing.append({**row, "periods": list(missing_cells), "printed_cells": missing_cells})
    return missing


def numbered_notes(page_text):
    notes = []
    for line in page_text.splitlines():
        if match := NOTE_LINE.match(line):
            notes.append((match[1] or match[2], match[3].strip()))
    return notes


def applicable_numbered_note(label, period, page_text, note, marker):
    """Authorize a numbered absence only on a row that actually cites the note."""
    if not label or not period or not (key := quote_key(label)):
        return False
    stated_years = set(YEAR.findall(note))
    if stated_years and not stated_years.intersection(YEAR.findall(period)):
        return False
    # A disclosure on a notes-only page cannot prove the row that borrows it.
    for years in annual_headers(page_text):
        matching = [year for year in YEAR.findall(period) if year in years]
        cells = annual_cells(page_text, label, years, note_marker=marker)
        if len(matching) == 1 and cells and EMPTY_PRINTED.fullmatch(cells[matching[0]]):
            return True
    return False
