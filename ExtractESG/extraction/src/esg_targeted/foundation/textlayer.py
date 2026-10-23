"""Cross-check OCR tables with the PDF's own text layer kept in the IR.

Digitally produced reports carry exact text. When a table's figures agree
with its page's text layer, that page is a reliable reference: a figure absent
from it is a suspected OCR misread, and a label fragment absent from it is
replaced by the unique text-layer fragment that differs only by misread
characters (綠電抵扣 printed, 線電無抵扣 recognised). Pages whose text layer
disagrees with the table (scans, image tables, broken fonts) are left alone.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from esg_targeted.foundation.tables import TableView, is_number_text, number_text
from esg_targeted.foundation.textnorm import TRANSLATION_TABLE

MIN_AGREEMENT = 0.8
MIN_NUMBERS = 4
SPACE_RE = re.compile(r"\s+")
MARKUP_RE = re.compile(r"[$\\{}^_]")
CJK_RE = re.compile(r"[㐀-鿿]")


def fold_chars(text: str) -> str:
    """Length-preserving fold for alignment: traditional→simplified, ASCII lower case."""
    return text.translate(TRANSLATION_TABLE).lower()


def load_native_text(ir_root: Path) -> dict[int, str]:
    path = Path(ir_root) / "observations" / "local-pdf" / "forensics.json"
    if not path.is_file():
        return {}
    pages = (json.loads(path.read_text(encoding="utf-8")).get("forensics") or {}).get("pages") or []
    return {int(p["page_index"]): p["native_text"] for p in pages if p.get("native_text")}


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


class PageText:
    def __init__(self, text: str) -> None:
        self.spaced = SPACE_RE.sub(" ", text)
        self.original = SPACE_RE.sub("", text)
        self.folded = fold_chars(self.original)
        self.bigrams: dict[str, list[int]] = defaultdict(list)
        for i in range(len(self.folded) - 1):
            self.bigrams[self.folded[i:i + 2]].append(i)

    def has(self, text: str) -> bool:
        return fold_chars(SPACE_RE.sub("", text)) in self.folded

    def has_number(self, text: str) -> bool:
        token = SPACE_RE.sub("", number_text(text))
        return re.search(r"(?<![\d.,])" + re.escape(token) + r"(?![\d]|[.,]\d)", self.spaced) is not None

    def candidates(self, segment: str, limit: int = 3) -> list[tuple[float, str]]:
        """Distinct text-layer windows most similar to an OCR fragment, best first."""
        target = fold_chars(SPACE_RE.sub("", segment))
        votes: Counter[int] = Counter()
        for offset in range(len(target) - 1):
            for position in self.bigrams.get(target[offset:offset + 2], ()):
                votes[position - offset] += 1
        scored: dict[str, tuple[float, int]] = {}
        for anchor, _ in votes.most_common(12):
            for start in (anchor - 1, anchor, anchor + 1):
                for length in (len(target) - 1, len(target), len(target) + 1):
                    if start < 0 or length < 2 or start + length > len(self.folded):
                        continue
                    window = self.folded[start:start + length]
                    score = SequenceMatcher(None, target, window).ratio()
                    if score > scored.get(window, (0.0, 0))[0]:
                        scored[window] = (score, start)
        ranked = sorted(((score, start, window) for window, (score, start) in scored.items()), reverse=True)
        return [(score, self.original[start:start + len(window)]) for score, start, window in ranked[:limit]]


def plausible_misread(segment: str, window: str) -> bool:
    """Swapped CJK characters; a bracketed qualifier may also gain or lose one."""
    a, b = fold_chars(SPACE_RE.sub("", segment)), fold_chars(window)
    qualifier = a[:1] in "（(" and a[-1:] in "）)" and b[:1] in "（(" and b[-1:] in "）)"
    if len(a) != len(b) and not (qualifier and abs(len(a) - len(b)) == 1):
        return False
    distance = edit_distance(a, b)
    if distance == 0 or distance > max(1, len(a) // 4) + (1 if qualifier else 0):
        return False
    changed = {ch for ch in a if ch not in b} | {ch for ch in b if ch not in a}
    return all(CJK_RE.match(ch) for ch in changed)


QUALIFIER_RE = re.compile(r"[（(][^（）()]{2,16}[)）]")


def _fragments(label: str):
    """(part index, start, end, text) of bracketed qualifiers ("（基于市场）", "（綠電抵扣）").

    Only short qualifiers are corrected: running label text is often split by
    the text layer's column interleaving, so a long match may splice two cells.
    """
    for index, part in enumerate(label.split(" › ")):
        for match in QUALIFIER_RE.finditer(part):
            compact = SPACE_RE.sub("", match.group(0))
            if not MARKUP_RE.search(compact) and not re.search(r"\d", compact):
                yield index, match.start(), match.end(), match.group(0)


def reconcile_view(view: TableView, page: PageText) -> None:
    """Record text-layer agreement, unconfirmed figures and corrected labels on the view."""
    cells = [cell for line in view.lines for cell in line.values.values() if is_number_text(cell.text)]
    if len(cells) < MIN_NUMBERS:
        return
    confirmed = {cell.cell_id for cell in cells if page.has_number(cell.text)}
    corrected = {}
    for cell in cells:
        swapped = None if cell.cell_id in confirmed else _separator_swap(number_text(cell.text))
        if swapped and page.has_number(swapped):
            corrected[cell.cell_id] = swapped
    # A figure printed with the other decimal separator still shows the page matches.
    view.text_layer_agreement = round((len(confirmed) + len(corrected)) / len(cells), 3)
    if view.text_layer_agreement < MIN_AGREEMENT:
        return
    view.corrected_cells = corrected
    view.unconfirmed_cells = {cell.cell_id for cell in cells} - confirmed - set(corrected)
    for line in view.lines:
        edits = []
        for index, start, end, fragment in _fragments(line.label):
            if page.has(fragment):
                continue
            options = [(score, window) for score, window in page.candidates(fragment) if plausible_misread(fragment, window)]
            # Sibling qualifiers ("（綠電抵扣）" / "（綠電未抵扣）") are both printed: take
            # a reading only when it is clearly closer than every other one.
            if options and (len(options) == 1 or options[0][0] - options[1][0] >= 0.05):
                edits.append((index, start, end, options[0][1]))
        if edits:
            parts = line.label.split(" › ")
            for index, start, end, window in sorted(edits, key=lambda e: (e[0], -e[1])):
                parts[index] = parts[index][:start] + window + parts[index][end:]
            line.ocr_label, line.label = line.label, " › ".join(parts)


def _separator_swap(text: str) -> str | None:
    """"1,274" <-> "1.274" when the figure holds a single separator of one kind."""
    if text.count(",") == 1 and "." not in text:
        return text.replace(",", ".")
    if text.count(".") == 1 and "," not in text:
        return text.replace(".", ",")
    return None
