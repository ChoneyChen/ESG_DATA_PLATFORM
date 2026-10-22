"""Page results for digitally produced PDF pages, read from their text layer.

PaddleOCR-VL reads a page in two steps: a layout model finds the regions and
their reading order, then a vision-language model transcribes each region.
For a page whose embedded text is clean the second step is unnecessary:
words, positions and font sizes are exact. Here the regions found by the
same layout model are filled from the PDF's own words, text regions with
their lines and table regions with a grid rebuilt from word geometry and
ruling lines. The result has the shape of a PaddleOCR-VL page result, so the
Document IR builder and every later stage consume either source unchanged.

Geometry is in the pixel space of the page image the layout model saw. Lines
are grouped by glyph baseline, not by font box: fonts in one table row can
report boxes several points apart while sharing the baseline.
"""
from __future__ import annotations

import html
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

MIN_PAGE_CHARS = 40
IMAGE_LABELS = {"image", "header_image", "footer_image", "seal", "chart"}
SKIP_ORDER_LABELS = {"figure_title", "vision_footnote", "image", "chart", "table", "header", "header_image",
                     "footer", "footer_image", "footnote", "aside_text"}
MARKDOWN_IGNORE_LABELS = ["number", "footnote", "header", "header_image", "footer", "footer_image", "aside_text"]
VALUE_RE = re.compile(
    r"^[<>≤≥~约約]?\(?[-−–+]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:[.．]\d+)?\)?[%％‰]?\*?$"
    r"|^(?:/|-|—|–|－|N/?A|n/?a|NA|不适用|不適用|无|無)$")
YEAR_RE = re.compile(r"^(?:FY\s?)?(?:19|20)\d{2}(?:\s*[/／-]\s*\d{2,4})?(?:年|年度|財年|财年)?$", re.I)
CID_RE = re.compile(r"\(cid:\d+\)")
MARK_RE = re.compile(r"[\d,，、\s*]+|[*#†‡§]+|[a-z]")
# Words a small glyph completes rather than annotates: chemical formulas
# (SO2, NH3-N, N2O, tCO2e) and unit exponents (m3, km2).
FORMULA_END_RE = re.compile(
    r"(?:(?<![A-Za-z])|(?<=[tgkM]))(?:CO|SO|NO|NH|CH|N|H|O|SF|NF|Cl|HF)\d*$|(?<![A-Za-z])(?:[kcdN]?m|ft)$")
OPEN_RE = re.compile(r"[（(\[【「《][^）)\]】」》]*$|(?:及|和|与|與|、|/|-|of|and|or|the|for|from|in)$", re.I)


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    base: float  # baseline, measured from the top of the page
    size: float
    mark: bool = False  # a footnote mark

    @property
    def top(self) -> float:
        return self.base - 0.8 * self.size

    @property
    def bottom(self) -> float:
        return self.base + 0.2 * self.size

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def base(self) -> float:
        return statistics.median(w.base for w in self.words)

    @property
    def size(self) -> float:
        return statistics.median(w.size for w in self.words)

    @property
    def mid(self) -> float:
        return self.base - 0.3 * self.size

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def text(self) -> str:
        return join_words(self.words)


# ---------------------------------------------------------------- reading


def page_words(page: Any, scale: float) -> tuple[list[Word], int]:
    """Words of a pdfplumber page in pixel space, and the count of characters
    set at an angle (left out). Only glyphs inside the visible page count: a
    page cropped out of a two-page spread still carries the other half's text."""
    from pdfplumber.utils.text import WordExtractor

    x0_page, top_page, x1_page, bottom_page = (float(v) for v in page.bbox)
    x_off, y_off = x0_page, top_page
    words: list[Word] = []
    angled = 0
    # Upright means not rotated: slanted (synthetic italic) glyphs shear their
    # matrix but stay on the line, and pdfplumber would read them as vertical.
    chars = [{**char, "upright": _upright(char)} for char in page.dedupe_chars().chars
             if x0_page <= (float(char["x0"]) + float(char["x1"])) / 2 <= x1_page
             and top_page <= (float(char["top"]) + float(char["bottom"])) / 2 <= bottom_page]
    extractor = WordExtractor(x_tolerance=1.5, y_tolerance=2.5, keep_blank_chars=False, use_text_flow=False,
                              extra_attrs=["size"])
    for raw in extractor.extract_words(chars, return_chars=True):
        text = str(raw["text"])
        if not text.strip():
            continue
        glyphs = raw.get("chars") or []
        if not glyphs or not all(glyph.get("upright", True) for glyph in glyphs):
            angled += len(text)
            continue
        first = glyphs[0]
        base = float(first["top"]) + float(first["y1"]) - float(first["matrix"][5])
        words.append(Word(text, (float(raw["x0"]) - x_off) * scale, (float(raw["x1"]) - x_off) * scale,
                          (base - y_off) * scale, float(raw["size"]) * scale))
    return words, angled


def _upright(char: dict[str, Any]) -> bool:
    a, b = (float(v) for v in (char.get("matrix") or (1, 0))[:2])
    return a > 0 and abs(b) <= 0.05 * abs(a)


def page_rules(page: Any, scale: float) -> list[tuple[float, float, float]]:
    """Horizontal rules (y, x0, x1) in pixel space; collinear pieces joined.
    Lines, rectangle edges and row shading all count."""
    x_off, y_off = float(page.bbox[0]), float(page.bbox[1])
    pieces = sorted(((float(e["top"]) - y_off) * scale, (float(e["x0"]) - x_off) * scale,
                     (float(e["x1"]) - x_off) * scale) for e in page.horizontal_edges)
    rules: list[list[float]] = []
    for y, x0, x1 in pieces:
        for rule in reversed(rules[-12:]):
            if abs(rule[0] - y) <= 1.5 and x0 <= rule[2] + 4 and x1 >= rule[1] - 4:
                rule[1], rule[2] = min(rule[1], x0), max(rule[2], x1)
                break
        else:
            rules.append([y, x0, x1])
    return [(y, x0, x1) for y, x0, x1 in rules]


def text_layer_quality(words: list[Word], angled: int = 0) -> tuple[bool, str]:
    chars = sum(len(w.text) for w in words)
    if chars < MIN_PAGE_CHARS:
        return False, "too_little_text"
    broken = sum(len(CID_RE.findall(w.text)) for w in words) + sum(
        1 for w in words for ch in w.text if ch == "\ufffd" or "\ue000" <= ch <= "\uf8ff")
    if broken > 0.02 * chars:
        return False, "unmapped_glyphs"
    if angled > 0.2 * (chars + angled):
        return False, "angled_text"
    return True, "ok"


def join_words(words: Iterable[Word]) -> str:
    out = ""
    previous: Word | None = None
    for word in sorted(words, key=lambda w: w.x0):
        if previous is not None:
            gap = word.x0 - previous.x1
            cjk = _is_cjk(previous.text[-1:]) or _is_cjk(word.text[:1])
            # The extractor split Latin words at spaces and gaps (slanted
            # neighbours may even overlap); only a size change splits a word.
            same_size = abs(previous.size - word.size) <= 0.05 * max(previous.size, word.size)
            if word.mark:
                pass
            elif cjk:
                out += " " if gap > 0.6 * min(previous.size, word.size) else ""
            elif same_size or gap > 0.08 * min(previous.size, word.size):
                out += " "
        out += word.text
        previous = word
    return out


def join_lines(texts: Iterable[str]) -> str:
    out = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if not out:
            out = text
        elif out.endswith("-") and text[:1].islower():
            out = out[:-1] + text
        elif _is_cjk(out[-1]) or _is_cjk(text[0]):
            out += text
        else:
            out += " " + text
    return out


def _is_cjk(char: str) -> bool:
    return bool(char) and ("㐀" <= char <= "鿿" or "＀" <= char <= "￯" or "　" <= char <= "〿")


def is_value(text: str) -> bool:
    return bool(VALUE_RE.match(text.replace(" ", "")))


def is_number(text: str) -> bool:
    return is_value(text) and any(ch.isdigit() for ch in text) and not YEAR_RE.match(text)


def build_lines(words: list[Word]) -> list[Line]:
    """Rows of words sharing a baseline. Small glyphs: a chemical subscript or
    unit exponent joins the word it follows (SO + 2, tCO + 2 + e, m + 3); other
    small figures are footnote marks, kept as superscripts ("中水使用量³⁸")."""
    if not words:
        return []
    words = [Word(w.text, w.x0, w.x1, w.base, w.size, w.mark) for w in words]
    body = statistics.median(w.size for w in words)
    normal = sorted((w for w in words if w.size >= 0.72 * body), key=lambda w: (w.base, w.x0))
    small = sorted((w for w in words if w.size < 0.72 * body), key=lambda w: (w.base, w.x0))
    lines: list[Line] = []
    for word in normal:
        for line in reversed(lines[-8:]):
            if abs(line.words[0].base - word.base) <= 0.25 * max(word.size, line.words[0].size) and not any(
                    min(w.x1, word.x1) - max(w.x0, word.x0) > 0.3 * min(w.x1 - w.x0, word.x1 - word.x0)
                    for w in line.words):  # slanted glyphs of neighbouring words overlap a little
                line.words.append(word)
                break
        else:
            lines.append(Line([word]))
    for word in small:
        near = [l for l in lines if -1.0 * l.size <= word.base - l.base <= 0.6 * l.size]
        host = None
        if near:
            host = min(near, key=lambda l: min((abs(word.x0 - w.x1) for w in l.words), default=math.inf))
            if min(abs(word.x0 - w.x1) for w in host.words) > 1.5 * host.size:
                host = min(near, key=lambda l: abs(l.base - word.base))
        if host is None:
            lines.append(Line([word]))
            continue
        raised = word.base < host.base - 0.15 * host.size
        left = [w for w in host.words if w.x1 <= word.x0 + 1.5 and word.x0 - w.x1 < 0.6 * host.size]
        anchor = max(left, key=lambda w: w.x1) if left else None
        attached = anchor is not None and word.x0 - anchor.x1 < 0.3 * host.size
        mark = MARK_RE.fullmatch(word.text.strip())
        if attached and (FORMULA_END_RE.search(anchor.text) or not mark and not raised):
            anchor.text += word.text
            anchor.x1 = max(anchor.x1, word.x1)
        elif mark:
            host.words.append(Word(footnote_mark(word.text), word.x0, word.x1, host.base, host.size, True))
        else:
            host.words.append(Word(word.text, word.x0, word.x1, host.base, host.size))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
        merged: list[Word] = []
        for word in line.words:
            if merged and is_mark(word) and is_mark(merged[-1]) and word.x0 - merged[-1].x1 < 0.5 * line.size:
                # Two marks set apart ("4" "10") stay two numbers: ⁴,¹⁰ not ⁴¹⁰.
                joiner = "" if merged[-1].text.endswith((",", "，", "、")) or word.text.startswith((",", "，", "、")) else ","
                merged[-1].text += joiner + word.text
                merged[-1].x1 = word.x1
            else:
                merged.append(word)
        line.words = merged
    return sorted(lines, key=lambda l: (l.base, l.x0))


SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def footnote_mark(body: str) -> str:
    """A footnote mark as printed: superscript digits ("中水使用量³⁸") stay
    distinguishable from figures after text normalisation; other marks (*, a)
    keep their characters."""
    body = re.sub(r"\s+", "", body)
    return body.translate(SUPERSCRIPT) if re.fullmatch(r"[\d,，、]+", body) else body


def is_mark(word: Word) -> bool:
    return word.mark


# ---------------------------------------------------------------- tables


@dataclass
class Column:
    x0: float
    x1: float
    values: bool = False

    def overlap(self, word: Word) -> float:
        return min(self.x1, word.x1) - max(self.x0, word.x0)

    def gap(self, word: Word) -> float:
        return max(self.x0 - word.x1, word.x0 - self.x1, 0.0)


def _columns(lines: list[Line], size: float, min_gap: float) -> list[Column]:
    """Columns from an x projection of the data lines. A stretch covered by at
    most one line (an overlong label) still separates two well covered
    columns."""
    spans = [(w.x0, w.x1) for line in lines for w in line.words if not is_mark(w)]
    if not spans:
        return []
    lo = math.floor(min(a for a, _ in spans))
    hi = math.ceil(max(b for _, b in spans))
    diff = [0] * (hi - lo + 2)
    for a, b in spans:
        diff[int(a) - lo] += 1
        diff[int(math.ceil(b)) - lo] -= 1
    cover, running = [], 0
    for step in diff[:-1]:
        running += step
        cover.append(running)
    weak = 1 if len(lines) >= 5 else 0
    runs: list[list[int]] = []
    start = None
    for x, value in enumerate(cover + [0]):
        if value > weak and start is None:
            start = x
        elif value <= weak and start is not None:
            runs.append([start, x])
            start = None
    # Weak stretches narrower than a column gap do not separate; a stretch no
    # line crosses at all does, however narrow (tightly set figure columns).
    merged: list[list[int]] = []
    for run in runs:
        if merged:
            gap = cover[merged[-1][1]:run[0]]
            limit = 0.25 * size if not any(gap) else min_gap
            if run[0] - merged[-1][1] < limit:
                merged[-1][1] = run[1]
                continue
        merged.append(run)
    columns = [Column(lo + a, lo + b) for a, b in merged]
    # Words only in weak stretches widen their nearest column.
    for a, b in sorted(spans):
        word = Word("", a, b, 0.0, size)
        if columns and not any(c.overlap(word) > 0 for c in columns):
            nearest = min(columns, key=lambda c: c.gap(word))
            if nearest.gap(word) < min_gap:
                nearest.x0, nearest.x1 = min(nearest.x0, a), max(nearest.x1, b)
            else:
                columns.append(Column(a, b))
                columns.sort(key=lambda c: c.x0)
    return columns


def _split_repeated_periods(columns: list[Column], lines: list[Line]) -> list[Column]:
    """Split a column that holds several period columns of one header.

    A table whose header repeats a period per entity ("2023 2024 2025" over
    Ports, then over Retail, ...) has those repeats as its only column
    boundaries when rows share one baseline; an x projection then merges the
    entity groups into a few wide columns and several figures land in one cell.
    Each repeated period word marks a boundary, so a merged column splits at the
    midpoints between its period anchors. A single period per year keeps the
    projection's own columns.
    """
    positions: dict[str, list[float]] = {}
    for line in lines:
        years = [w for w in line.words if YEAR_RE.match(re.sub(r"\s+", "", w.text))]
        figures = [w for w in line.words if is_value(w.text) and not YEAR_RE.match(re.sub(r"\s+", "", w.text))]
        if len(years) < 2 or figures:
            continue  # a period header repeats years; a data row does not
        for word in years:
            text = re.sub(r"\s+", "", word.text)
            positions.setdefault(text.casefold(), []).append((word.x0 + word.x1) / 2)
    anchors: list[float] = []
    for midpoints in positions.values():
        kept: list[float] = []
        for x in sorted(midpoints):
            if not kept or x - kept[-1] > 1.0:
                kept.append(x)
        if len(kept) >= 2:
            anchors.extend(kept)
    anchors.sort()
    if len(anchors) < 2:
        return columns
    split: list[Column] = []
    for column in columns:
        inside = [x for x in anchors if column.x0 - 1.0 <= x <= column.x1 + 1.0]
        if len(inside) < 2:
            split.append(column)
            continue
        edges = [column.x0, *[(a + b) / 2 for a, b in zip(inside, inside[1:])], column.x1]
        width = (column.x1 - column.x0) / len(edges[:-1])
        for a, b in zip(edges, edges[1:]):
            if b - a > 0.25 * width or len(edges) == 2:
                split.append(Column(a, b, column.values))
    return sorted(split, key=lambda c: c.x0)


def _column_of(columns: list[Column], word: Word) -> int:
    best = max(range(len(columns)), key=lambda i: columns[i].overlap(word))
    if columns[best].overlap(word) > 0:
        return best
    return min(range(len(columns)), key=lambda i: columns[i].gap(word))


def _bands(rules: list[tuple[float, float, float]], box: list[float]) -> list[tuple[float, float]]:
    """Row bands between rules that run across most of the table."""
    x0, y0, x1, y1 = box
    width = x1 - x0
    ys = sorted({round(y, 1) for y, a, b in rules
                 if y0 - 4 <= y <= y1 + 4 and min(b, x1) - max(a, x0) >= 0.5 * width})
    cuts: list[float] = []
    for y in ys:
        if not cuts or y - cuts[-1] > 3:
            cuts.append(y)
    edges = [-math.inf, *cuts, math.inf]
    return [(a, b) for a, b in zip(edges, edges[1:])]


def _label_text(line: Line, columns: list[Column]) -> list[Word]:
    first_values = next((c.x0 for c in columns if c.values), math.inf)
    return [w for w in line.words if w.x1 <= first_values]


def _continues(line: Line, anchor: Line, columns: list[Column], label_right: float) -> bool:
    """The anchor's label runs on into this line (wrapped cell)."""
    label = _label_text(anchor, columns)
    if not label:
        return False
    text = join_words(label)
    return (max(w.x1 for w in label) >= label_right - 1.6 * anchor.size or bool(OPEN_RE.search(text))
            or bool(re.match(r"^[）)\]】」》a-z]", line.text)))


def _split(members: list[Line], anchors: list[Line], columns: list[Column], label_right: float) -> list[list[Line]]:
    """Rows inside one band: every figure line starts a row; label lines join
    the row they visibly belong to, or stand alone (section headings)."""
    order = sorted(anchors, key=lambda l: l.mid)
    rows: dict[int, list[Line]] = {id(a): [a] for a in order}
    alone: list[list[Line]] = []
    for line in sorted(members, key=lambda l: l.mid):
        if id(line) in rows:
            continue
        above = max((a for a in order if a.mid <= line.mid), key=lambda a: a.mid, default=None)
        below = min((a for a in order if a.mid > line.mid), key=lambda a: a.mid, default=None)
        da = line.mid - above.mid if above else math.inf
        db = below.mid - line.mid if below else math.inf
        reach = 1.75 * line.size
        target = None
        if min(da, db) <= reach:
            if abs(da - db) > 0.35 * line.size:
                target = above if da < db else below
            elif above is not None and _continues(line, above, columns, label_right):
                target = above
            elif below is not None and OPEN_RE.search(line.text):
                target = below
        if target is not None:
            rows[id(target)].append(line)
        elif alone and line.mid - alone[-1][-1].mid <= 1.5 * line.size and not any(
                a for a in order if alone[-1][-1].mid < a.mid < line.mid):
            alone[-1].append(line)  # a wrapped section heading
        else:
            alone.append([line])
    result = [rows[id(a)] for a in order] + alone
    return sorted((sorted(r, key=lambda l: l.mid) for r in result), key=lambda r: r[0].mid)


def _line_columns(line: Line, columns: list[Column]) -> list[int]:
    """Column of every word of a line; a footnote mark keeps the column of
    the word it follows."""
    out: list[int] = []
    for word in line.words:
        out.append(out[-1] if out and is_mark(word) else _column_of(columns, word))
    return out


def _cells(row: list[Line], columns: list[Column]) -> list[str]:
    parts: list[list[list[Word]]] = [[] for _ in columns]
    for line in row:
        per_column: dict[int, list[Word]] = {}
        for word, index in zip(line.words, _line_columns(line, columns)):
            per_column.setdefault(index, []).append(word)
        for index, words in per_column.items():
            parts[index].append(words)
    return [join_lines(join_words(words) for words in lines) for lines in parts]


def _header_spans(line_group: list[Line], columns: list[Column], size: float) -> list[list[Any]]:
    """[first column, last column, text, centre] per heading of one header row;
    a heading set over several columns spans them."""
    segments: list[list[Word]] = []
    words = sorted((w for line in line_group for w in line.words), key=lambda w: w.x0)
    for word in words:
        if segments and (is_mark(word) or word.x0 - max(w.x1 for w in segments[-1]) < 0.8 * size and not (
                is_value(word.text) and is_value(segments[-1][-1].text))):
            segments[-1].append(word)
        else:
            segments.append([word])
    spans: list[list[Any]] = []
    for segment in segments:
        x0, x1 = min(w.x0 for w in segment), max(w.x1 for w in segment)
        probe = Word("", x0, x1, 0.0, size)
        covered = [i for i, c in enumerate(columns) if c.overlap(probe) > 0.35 * min(c.x1 - c.x0, x1 - x0)]
        if not covered:
            covered = [_column_of(columns, probe)]
        text = join_lines(join_words([w for w in segment if abs(w.base - line.base) < 1e-6]) for line in line_group)
        if spans and covered[0] <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], covered[-1])
            spans[-1][2] = join_lines([spans[-1][2], text]) if spans[-1][2] != text else text
            spans[-1][3] = (spans[-1][3] + (x0 + x1) / 2) / 2
        else:
            spans.append([covered[0], covered[-1], text, (x0 + x1) / 2])
    return spans


def _widen_group_headings(rows: list[list[list[Any]]], columns: list[Column]) -> None:
    """A heading centred over sub-columns that have no heading of their own in
    its row ("2025" over two entity columns | 合计) spans those sub-columns."""
    for index, spans in enumerate(rows[:-1]):
        below = {c for lower in rows[index + 1:] for first, last, text, _ in lower if text for c in range(first, last + 1)}
        taken = {c for first, last, _, _ in spans for c in range(first, last + 1)}
        for span in spans:
            first, last, text, centre = span
            if not text:
                continue
            lo = first
            while lo - 1 >= 0 and lo - 1 not in taken and columns[lo - 1].values and lo - 1 in below:
                lo -= 1
            hi = last
            while hi + 1 < len(columns) and hi + 1 not in taken and columns[hi + 1].values and hi + 1 in below:
                hi += 1
            best = None
            for a in range(lo, first + 1):
                for b in range(last, hi + 1):
                    if (a, b) == (first, last):
                        continue
                    width = columns[b].x1 - columns[a].x0
                    if abs((columns[a].x0 + columns[b].x1) / 2 - centre) <= 0.15 * width and (
                            best is None or b - a > best[1] - best[0]):
                        best = (a, b)
            if best:
                span[0], span[1] = best
                taken.update(range(best[0], best[1] + 1))


def _span_cells(spans: list[list[Any]], width: int) -> list[tuple[str, int]]:
    cells: list[tuple[str, int]] = []
    cursor = 0
    for first, last, text, _ in spans:
        first = max(first, cursor)
        last = max(last, first)
        cells.extend(("", 1) for _ in range(first - cursor))
        cells.append((text, last - first + 1))
        cursor = last + 1
    cells.extend(("", 1) for _ in range(width - cursor))
    return cells


def table_grid(words: list[Word], rules: list[tuple[float, float, float]], box: list[float]) -> list[list[tuple[str, int]]]:
    """Rows of (text, colspan) cells for the words of one table region."""
    lines = build_lines(words)
    if not lines:
        return []
    size = statistics.median(l.size for l in lines)
    width = max(1.0, box[2] - box[0])

    def prose(line: Line) -> bool:
        return any(len(w.text) > 12 and w.x1 - w.x0 > 0.45 * width for w in line.words)

    data = [l for l in lines if any(is_number(w.text) for w in l.words) and not prose(l)]
    basis = data or [l for l in lines if not prose(l)] or lines
    # A category printed once beside its rows ("水资源", left of the row
    # labels) stands in a column of its own.
    if data:
        left_edge = min(w.x0 for l in data for w in l.words)
        basis = basis + [l for l in lines if l not in data and not prose(l) and l.x1 <= left_edge - 0.6 * size]
    columns = _split_repeated_periods(_columns(basis, size, 0.6 * size), basis)
    if not columns:
        return [[(line.text, 1)] for line in lines]
    for index, column in enumerate(columns):
        inside = [w for l in basis for w in l.words if _column_of(columns, w) == index]
        column.values = bool(inside) and sum(is_value(w.text) for w in inside) >= 0.5 * len(inside)
    if all(c.values for c in columns) and len(columns) > 1:
        columns[0].values = False  # the label column holds e.g. "范围1"
    label_right = next((c.x0 for c in columns if c.values), columns[-1].x1)
    label_right = max((c.x1 for c in columns if c.x1 <= label_right), default=label_right)

    def is_anchor(line: Line) -> bool:
        figures = [w for w in line.words if is_value(w.text) and columns[_column_of(columns, w)].values]
        return bool(figures) and not all(YEAR_RE.match(w.text) for w in figures)

    anchors = [l for l in lines if is_anchor(l)]
    if not anchors:
        anchors = [l for l in lines if len({_column_of(columns, w) for w in l.words}) >= 2] or lines
    anchor_ids = {id(a) for a in anchors}
    first_mid = min(a.mid for a in anchors)
    rows: list[list[Line]] = []
    for low, high in _bands(rules, box):
        members = [l for l in lines if low <= l.mid < high]
        if not members:
            continue
        band_anchors = [l for l in members if id(l) in anchor_ids]
        if len(band_anchors) == 1 or (not band_anchors and min(l.mid for l in members) > first_mid):
            rows.append(sorted(members, key=lambda l: l.mid))
        elif band_anchors:
            rows.extend(_split(members, band_anchors, columns, label_right))
        else:
            rows.extend([l] for l in sorted(members, key=lambda l: l.mid))
    rows.sort(key=lambda r: r[0].mid)
    # Header: rows above the first figure row, down to the last one with words
    # over figure columns (years, units); label-only rows after it are section
    # rows of the body. Lines that only continue cells of the line above (a
    # wrapped heading) are one row.
    first_body = next((i for i, r in enumerate(rows) if any(id(l) in anchor_ids for l in r)), len(rows))
    heading_rows = [i for i in range(first_body)
                    if any(columns[c].values for l in rows[i] for c in _line_columns(l, columns))]
    first_body = heading_rows[-1] + 1 if heading_rows else first_body
    header: list[list[Line]] = []
    body: list[list[Line]] = []
    for index, row in enumerate(rows):
        if index >= first_body:
            body.append(row)
            continue
        if header and len(row) == 1 and row[0].mid - header[-1][-1].mid <= 1.5 * size:
            used = {_column_of(columns, w) for l in header[-1] for w in l.words}
            if {_column_of(columns, w) for w in row[0].words} <= used:
                header[-1].append(row[0])
                continue
        header.append(list(row))
    header_spans = [_header_spans(group, columns, size) for group in header]
    _widen_group_headings(header_spans, columns)
    grid: list[list[tuple]] = [_span_cells(spans, len(columns)) for spans in header_spans]
    cells = [_cells(row, columns) for row in body]
    figures = [any(text and columns[i].values and is_value(text.rstrip("⁰¹²³⁴⁵⁶⁷⁸⁹,，、*")) for i, text in enumerate(row))
               for row in cells]
    groups = _category_groups(body, cells, figures, columns, rules, size)
    covered = {(r, k) for k, spans in groups.items() for first, span, _ in spans for r in range(first + 1, first + span)}
    starts = {(first, k): (text, span) for k, spans in groups.items() for first, span, text in spans}
    for k in groups:
        for row in cells:
            row[k] = ""
    for r, row in enumerate(cells):
        if not figures[r] and not row[0] and any(row) and not any((r, k) in starts for k in groups):
            row[:] = [join_lines(row)] + [""] * (len(row) - 1)  # a section heading set over the columns
        out_row: list[tuple] = []
        for k, text in enumerate(row):
            if (r, k) in covered:
                continue
            if (r, k) in starts:
                label, span = starts[(r, k)]
                out_row.append((label, 1, span) if span > 1 else (label, 1))
            else:
                out_row.append((text, 1))
        grid.append(out_row)
    return grid


def _category_groups(body: list[list[Line]], cells: list[list[str]], figures: list[bool], columns: list[Column],
                     rules: list[tuple[float, float, float]], size: float) -> dict[int, list[tuple[int, int, str]]]:
    """Category cells printed once beside a group of rows ("水资源" beside its
    two rows, "废弃物" beside six): column -> [(first row, row count, text)].

    A group is bounded by the rules that cross the category column; without
    such rules a label shares the first row of its group (top aligned) or
    sits centred on its rows."""
    value_start = next((i for i, c in enumerate(columns) if c.values), len(columns))
    groups: dict[int, list[tuple[int, int, str]]] = {}
    n = len(cells)
    for k in range(max(0, value_start - 1)):
        filled = [r for r in range(n) if cells[r][k]]
        if not filled or len(filled) >= 0.6 * n or sum(1 for r in range(n) if cells[r][k + 1]) < 0.7 * n:
            continue
        if any(len(cells[r][k]) > 16 for r in filled) or sum(figures[r] for r in filled) < 0.5 * len(filled):
            continue  # section headings on rows of their own are not categories
        column = columns[k]

        def extent(r: int, own: bool) -> tuple[float, float]:
            lines = [l for l in body[r] if own == any(c == k for c in _line_columns(l, columns))] or body[r]
            return min(l.base - 0.8 * l.size for l in lines), max(l.base + 0.2 * l.size for l in lines)

        rows = [extent(r, False) for r in range(n)]
        mids = [(a + b) / 2 for a, b in rows]
        labels = [(r, sum(extent(r, True)) / 2) for r in filled]
        crossing = sorted(y for y, a, b in rules
                          if min(b, column.x1) - max(a, column.x0) >= 0.6 * (column.x1 - column.x0)
                          and rows[0][0] - size <= y <= rows[-1][1] + size)
        spans: list[tuple[int, int, str]] = []
        if len(crossing) >= 2:
            for r, y in labels:
                above = max((c for c in crossing if c <= y), default=None)
                below = min((c for c in crossing if c > y), default=None)
                if above is None or below is None:
                    continue
                members = [i for i in range(n) if above < mids[i] < below]
                if members:
                    spans.append((members[0], members[-1] - members[0] + 1, cells[r][k]))
        elif labels[0][0] == 0 and all(abs(y - mids[r]) < 0.5 * size for r, y in labels):
            bounds = [r for r, _ in labels] + [n]
            spans = [(r, bounds[i + 1] - r, cells[r][k]) for i, (r, _) in enumerate(labels)]
        else:
            spans = _centred_groups(labels, rows, [cells[r][k] for r, _ in labels])
        if spans:
            groups[k] = spans
    return groups


def _centred_groups(labels: list[tuple[int, float]], rows: list[tuple[float, float]], texts: list[str]) -> list[tuple[int, int, str]]:
    """Partition the rows into consecutive groups, one per label, so that each
    label sits as near as possible to the middle of its group."""
    n, m = len(rows), len(labels)
    if m > n:
        return []
    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for g in range(1, m + 1):
        y = labels[g - 1][1]
        for end in range(g, n + 1):
            for start in range(g - 1, end):
                if best[g - 1][start] == inf:
                    continue
                cost = best[g - 1][start] + abs((rows[start][0] + rows[end - 1][1]) / 2 - y)
                if cost < best[g][end]:
                    best[g][end], back[g][end] = cost, start
    spans, end = [], n
    for g in range(m, 0, -1):
        start = back[g][end]
        spans.append((start, end - start, texts[g - 1]))
        end = start
    return list(reversed(spans))


def grid_html(grid: list[list[tuple]]) -> str:
    out = ["<table>"]
    for row in grid:
        cells = []
        for cell in row:
            text, span = cell[0], cell[1]
            rowspan = cell[2] if len(cell) > 2 else 1
            attributes = (f' colspan="{span}"' if span > 1 else "") + (f' rowspan="{rowspan}"' if rowspan > 1 else "")
            cells.append(f"<td{attributes}>{html.escape(text, quote=False)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


# ---------------------------------------------------------------- pages


def _assign(words: list[Word], boxes: list[dict[str, Any]]) -> list[list[Word]]:
    """Each word goes to the region holding its centre; among overlapping
    regions the content region wins over a picture, then the smaller one."""
    members: list[list[Word]] = [[] for _ in boxes]
    geometry = [[float(v) for v in box["coordinate"]] for box in boxes]
    for word in words:
        cx, cy = word.center, word.base - 0.3 * word.size
        holders = [i for i, (x0, y0, x1, y1) in enumerate(geometry)
                   if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2]
        if not holders:
            continue
        best = min(holders, key=lambda i: (boxes[i]["label"] in IMAGE_LABELS,
                                           (geometry[i][2] - geometry[i][0]) * (geometry[i][3] - geometry[i][1])))
        members[best].append(word)
    return members


def _place_orphans(words: list[Word], boxes: list[dict[str, Any]], members: list[list[Word]]) -> None:
    """Words outside every region. A line touching a content region (a box
    drawn through a table's last row) joins it and widens it; the remaining
    lines become text regions of their own, placed after the region above
    them in reading order. Nothing printed on the page is dropped."""
    assigned = {id(w) for group in members for w in group}
    lines = build_lines([w for w in words if id(w) not in assigned])
    for _ in range(3):
        leftover = []
        for line in lines:
            top, bottom = line.base - 0.8 * line.size, line.base + 0.2 * line.size
            best, best_gap = None, math.inf
            for index, box in enumerate(boxes):
                if box["label"] in IMAGE_LABELS:
                    continue
                x0, y0, x1, y1 = box["coordinate"]
                if min(line.x1, x1) - max(line.x0, x0) < 0.5 * (line.x1 - line.x0):
                    continue
                gap = max(y0 - bottom, top - y1, 0.0)
                if gap <= 0.6 * line.size and gap < best_gap:
                    best, best_gap = index, gap
            if best is None:
                leftover.append(line)
                continue
            members[best].extend(line.words)
            x0, y0, x1, y1 = boxes[best]["coordinate"]
            boxes[best]["coordinate"] = [min(x0, line.x0), min(y0, top), max(x1, line.x1), max(y1, bottom)]
            boxes[best]["widened"] = True
        if len(leftover) == len(lines):
            break
        lines = leftover
    paragraphs: list[list[Line]] = []
    for line in sorted(lines, key=lambda l: (l.base, l.x0)):
        for paragraph in paragraphs:
            last = paragraph[-1]
            if 0 < line.base - last.base <= 1.9 * line.size and min(line.x1, last.x1) - max(line.x0, last.x0) > 0:
                paragraph.append(line)
                break
        else:
            paragraphs.append([line])
    for paragraph in paragraphs:
        x0, x1 = min(l.x0 for l in paragraph), max(l.x1 for l in paragraph)
        y0 = paragraph[0].base - 0.8 * paragraph[0].size
        y1 = paragraph[-1].base + 0.2 * paragraph[-1].size
        above = [i for i, b in enumerate(boxes) if b["coordinate"][3] <= y0 + 2
                 and min(x1, b["coordinate"][2]) - max(x0, b["coordinate"][0]) > 0]
        position = max(above, key=lambda i: boxes[i]["coordinate"][3]) + 1 if above else 0
        boxes.insert(position, {"label": "text", "score": None, "coordinate": [x0, y0, x1, y1], "added": "text_layer"})
        members.insert(position, [w for l in paragraph for w in l.words])


NOTE_START_RE = re.compile(r"^\s*(?:\d{1,3}\s*[.、．:：)）]|[（(]\d{1,3}[)）]|[①-⑳]|注\s*\d|\*{1,3}\S| \$ \^\{)")
NOTE_LABELS = {"footnote", "vision_footnote"}


def block_text(words: list[Word]) -> str:
    return join_lines(line.text for line in build_lines(words))


def note_parts(words: list[Word]) -> list[list[Word]]:
    """A footnote region holding several numbered notes splits into one part
    per note, so each note can be linked to the figure that cites it."""
    lines = build_lines(words)
    starts = [i for i, line in enumerate(lines) if NOTE_START_RE.match(line.text)]
    if len(starts) < 2:
        return [words]
    cuts = sorted({0, *starts})
    parts = [lines[a:b] for a, b in zip(cuts, [*cuts[1:], len(lines)])]
    return [[w for line in part for w in line.words] for part in parts if part]


TEXT_LABELS = {"text", "paragraph_title", "doc_title", "abstract", "content"}


def _ocr_text_in(box: dict[str, Any], ocr_lines: list[dict[str, Any]]) -> str:
    x0, y0, x1, y1 = box["coordinate"]
    inside = [line for line in ocr_lines
              if x0 - 2 <= (line["bbox"][0] + line["bbox"][2]) / 2 <= x1 + 2
              and y0 - 2 <= (line["bbox"][1] + line["bbox"][3]) / 2 <= y1 + 2]
    inside.sort(key=lambda line: (round(line["bbox"][1] / 12), line["bbox"][0]))
    return join_lines(str(line["text"]) for line in inside)


def page_result(page: Any, layout: dict[str, Any], page_count: int,
                ocr_lines: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, str]:
    """A PaddleOCR-VL shaped page result for one pdfplumber page and its
    layout regions, or (None, reason) when the page needs visual OCR.

    ``ocr_lines`` (on-device OCR of the page, same pixel space) fill text
    regions the text layer leaves empty: titles set as pictures or outlines."""
    width_px, height_px = int(layout["width"]), int(layout["height"])
    scale = width_px / float(page.width)
    if abs(height_px / float(page.height) - scale) > 0.02 * scale:
        return None, "page_geometry_mismatch"
    if int(page.rotation or 0) % 360:
        return None, "rotated_page"
    words, angled = page_words(page, scale)
    ok, reason = text_layer_quality(words, angled)
    if not ok:
        return None, reason
    rules = page_rules(page, scale)
    detected = list(layout.get("boxes") or [])
    boxes = [{**box, "coordinate": [float(v) for v in box["coordinate"]]} for box in detected]
    members = _assign(words, boxes)
    _place_orphans(words, boxes, members)
    blocks: list[dict[str, Any]] = []
    markdown: list[str] = []
    images = dict(layout.get("images") or {})
    split_boxes: list[dict[str, Any]] = []
    split_members: list[list[Word]] = []
    for box, inside in zip(boxes, members):
        parts = note_parts(inside) if box["label"] in NOTE_LABELS and inside else [inside]
        if len(parts) == 1:
            split_boxes.append(box)
            split_members.append(inside)
            continue
        for part in parts:
            x0, x1 = min(w.x0 for w in part), max(w.x1 for w in part)
            y0, y1 = min(w.top for w in part), max(w.bottom for w in part)
            split_boxes.append({**box, "coordinate": [x0, y0, x1, y1], "widened": True})
            split_members.append(part)
    boxes, members = split_boxes, split_members
    order = 0
    for index, (box, inside) in enumerate(zip(boxes, members)):
        label = str(box["label"])
        bbox = [int(round(v)) for v in box["coordinate"]]
        if label in IMAGE_LABELS:
            content = ""
        elif label == "table":
            if len(inside) < 4:
                return None, "table_region_without_text_layer"
            content = grid_html(table_grid(inside, rules, [float(v) for v in box["coordinate"]]))
        elif not inside and label in TEXT_LABELS:
            if ocr_lines is None:
                return None, "text_region_without_text_layer"
            content = _ocr_text_in(box, ocr_lines)
            if not content:
                # Nothing legible there either: an icon or ornament taken for a
                # title. It carries no text, so it is left out.
                continue
            box["text_source"] = "on_device_ocr"
        else:
            content = block_text(inside)
        block_order = None
        if label not in SKIP_ORDER_LABELS:
            order += 1
            block_order = order
        polygon = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]]]
        if box.get("polygon_points") and not box.get("widened") and not box.get("added"):
            polygon = box["polygon_points"]
        blocks.append({"block_label": label, "block_content": content, "block_bbox": bbox, "block_id": index,
                       "block_order": block_order, "group_id": index, "block_polygon_points": polygon,
                       **({"text_source": box["text_source"]} if box.get("text_source") else {})})
        if label in MARKDOWN_IGNORE_LABELS:
            continue
        if label in IMAGE_LABELS:
            x0, y0, x1, y1 = (int(v) for v in box["coordinate"])
            name = f"imgs/img_in_{label}_box_{x0}_{y0}_{x1}_{y1}.jpg"
            if name in images:
                width_pct = max(1, round(100 * (bbox[2] - bbox[0]) / max(1, width_px)))
                markdown.append(f'<div style="text-align: center;"><img src="{name}" alt="Image" width="{width_pct}%" /></div>')
        elif label in {"paragraph_title", "doc_title"} and content:
            markdown.append(("# " if label == "doc_title" else "## ") + content)
        elif content:
            markdown.append(content)
    return {
        "prunedResult": {
            "page_count": page_count,
            "width": width_px,
            "height": height_px,
            "model_settings": {
                "use_doc_preprocessor": False,
                "use_layout_detection": True,
                "use_chart_recognition": False,
                "use_seal_recognition": False,
                "use_ocr_for_image_block": False,
                "format_block_content": False,
                "merge_layout_blocks": False,
                "markdown_ignore_labels": MARKDOWN_IGNORE_LABELS,
                "return_layout_polygon_points": True,
                "text_source": "pdf_text_layer",
                "layout_model": layout.get("layout_model"),
            },
            "parsing_res_list": blocks,
            "layout_det_res": {"boxes": [
                {"label": b["label"], "score": b.get("score"), "coordinate": b["coordinate"],
                 **({"polygon_points": b["polygon_points"]} if b.get("polygon_points") else {})} for b in detected]},
        },
        "markdown": {"text": "\n\n".join(markdown), "images": images},
        "outputImages": {},
        "inputImage": None,
    }, "ok"


# ---------------------------------------------------------------- documents


@dataclass
class TextLayerRead:
    """Pages of one document read from the text layer, and why the others
    still need visual OCR."""

    pages: dict[int, dict[str, Any]] = field(default_factory=dict)
    needs_ocr: dict[int, str] = field(default_factory=dict)
    layout_model: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    on_device_ocr_pages: list[int] = field(default_factory=list)  # text regions filled by on-device OCR

    def report(self, targets: list[int]) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for reason in self.needs_ocr.values():
            reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "route": "pdf_text_layer",
            "layout_model": self.layout_model,
            "page_count": len(targets),
            "text_layer_pages": sorted(self.pages),
            "visual_ocr_pages": [index for index in targets if index not in self.pages],
            "visual_ocr_reasons": {str(index): reason for index, reason in sorted(self.needs_ocr.items())},
            "visual_ocr_reason_counts": reasons,
            "on_device_ocr_pages": self.on_device_ocr_pages,
            "seconds": {key: round(value, 2) for key, value in self.timings.items()},
        }


def readable_pages(pdf_path: str, page_indices: list[int]) -> tuple[list[int], dict[int, str]]:
    """Pages whose text layer is worth a layout pass (a cheap check, no rendering)."""
    import pdfplumber

    readable: list[int] = []
    reasons: dict[int, str] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for index in page_indices:
            page = pdf.pages[index]
            if int(page.rotation or 0) % 360:
                reasons[index] = "rotated_page"
                continue
            words, angled = page_words(page, 1.0)
            ok, reason = text_layer_quality(words, angled)
            if ok:
                readable.append(index)
            else:
                reasons[index] = reason
            page.close()
    return readable, reasons


def fill_pages(pdf_path: str, layouts: list[dict[str, Any]], page_count: int,
               ocr: dict[int, list[dict[str, Any]]] | None = None) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """PaddleOCR-VL shaped results for the pages whose regions the text layer
    fills (with ``ocr`` lines for empty text regions, by page index)."""
    import pdfplumber

    pages: dict[int, dict[str, Any]] = {}
    reasons: dict[int, str] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for layout in layouts:
            index = int(layout["page_index"])
            page = pdf.pages[index]
            try:
                result, reason = page_result(page, layout, page_count, (ocr or {}).get(index))
            except Exception as exc:  # one odd page must not stop the others
                result, reason = None, f"text_layer_error:{type(exc).__name__}"
            if result is None:
                reasons[index] = reason
            else:
                pages[index] = result
            page.close()
    return pages, reasons
