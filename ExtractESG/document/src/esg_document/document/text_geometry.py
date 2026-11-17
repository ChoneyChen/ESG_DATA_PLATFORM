"""Exact text slices and conservative relationships from native PDF geometry."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import statistics
import math

from esg_contracts.evidence import EvidenceContextLink, TextEvidence
from esg_document.ocr.text_layer import Word, build_lines


def _union(boxes):
    return {axis: operation(box[axis] for box in boxes)
        for axis, operation in [('x0', min), ('y0', min), ('x1', max), ('y1', max)]}


def _contains(outer, inner, tolerance=0):
    return (outer['x0'] - tolerance <= inner['x0'] and inner['x1'] <= outer['x1'] + tolerance
        and outer['y0'] - tolerance <= inner['y0'] and inner['y1'] <= outer['y1'] + tolerance)


def _convex_polygon(commands):
    vertices = []
    for command in commands:
        operation, *points = command
        if operation == 'm':
            if vertices:
                return None
            vertices.append(tuple(points[0]))
        elif operation == 'l' and vertices:
            vertices.append(tuple(points[0]))
        elif operation == 'c' and vertices:
            start, first, second, end = vertices[-1], *points
            for step in range(1, 33):
                t = step / 32
                vertices.append(tuple((1-t)**3 * start[k] + 3*(1-t)**2*t * first[k]
                    + 3*(1-t)*t*t * second[k] + t**3 * end[k] for k in (0, 1)))
        elif operation != 'h':
            return None
    if len(vertices) < 3:
        return None
    if math.dist(vertices[0], vertices[-1]) < .1:
        vertices.pop()
    turns = [(b[0]-a[0])*(c[1]-b[1]) - (b[1]-a[1])*(c[0]-b[0])
        for a, b, c in zip(vertices, vertices[1:] + vertices[:1], vertices[2:] + vertices[:2])]
    signs = {turn > 0 for turn in turns if abs(turn) > 1e-6}
    return vertices if len(signs) == 1 else None


def _polygon_contains(polygon, box):
    corners = [(box[x], box[y]) for x in ('x0', 'x1') for y in ('y0', 'y1')]
    for point in corners:
        distances = [((b[0]-a[0])*(point[1]-a[1]) - (b[1]-a[1])*(point[0]-a[0]))
            / max(math.dist(a, b), 1e-12)
            for a, b in zip(polygon, polygon[1:] + polygon[:1])]
        if not (all(distance > .05 for distance in distances)
            or all(distance < -.05 for distance in distances)):
            return False
    return True


def _slice(source, text, bbox, *, word=False):
    positions = [index for index, char in enumerate(source.text) if not char.isspace()]
    compact = ''.join(source.text[index] for index in positions)
    needle = ''.join(text.split())
    start = compact.find(needle)
    if not needle or start < 0 or compact.find(needle, start + 1) >= 0:
        return None
    first, end = positions[start], positions[start + len(needle) - 1] + 1
    if word and re.fullmatch(r'[-+−\d.,，%％‰]+', needle) and (
        first > 0 and (source.text[first - 1].isdigit() or source.text[first - 1] in '.,，')
        or end < len(source.text) and (
            source.text[end].isdigit() or source.text[end] in '.,，')):
        return None
    return source.model_copy(update={
        'span_id': f'{source.object_id}@{source.char_start + first}:{source.char_start + end}',
        'char_start': source.char_start + first, 'text': source.text[first:end], 'bbox': bbox})


@dataclass
class NativeTextGeometry:
    is_native_figure: bool = False
    fragments: list[TextEvidence] = field(default_factory=list)
    words: list[TextEvidence] = field(default_factory=list)
    rectangles: list[dict] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)

    def literal_bbox(self, cell):
        if cell.char_start is None or cell.char_end is None:
            return None
        words = [word for word in self.words if cell.char_start <= word.char_start
            and word.char_start + len(word.text) <= cell.char_end]
        exact = [word for word in words if word.char_start == cell.char_start
            and word.char_start + len(word.text) == cell.char_end]
        if len(exact) == 1:
            return exact[0].bbox
        if ''.join(word.text for word in sorted(words, key=lambda word: word.char_start)).replace(' ', '') != ''.join(cell.raw_text.split()):
            return None
        return _union([word.bbox for word in words]) if words else None

    def bar_links(self, cells):
        """A printed bar contains its value and has a descriptor below its base.

        A shared baseline and repeated bar widths establish distinct columns.
        Arbitrary nearby text and a figure-wide bounding box establish no link.
        """
        if not self.is_native_figure:
            return []
        bars = []
        for box in self.rectangles:
            width, height = box['x1'] - box['x0'], box['y1'] - box['y0']
            inside = [cell for cell in cells if cell.bbox and _contains(box, cell.bbox, .5)]
            if width > 0 and height > .5 * width and len(inside) == 1:
                bars.append((box, inside[0]))
        links = []
        for box, cell in bars:
            width = box['x1'] - box['x0']
            peers = [(other, value) for other, value in bars
                if abs(other['y1'] - box['y1']) <= 1
                and abs((other['x1'] - other['x0']) - width) <= .1 * width]
            peers.sort(key=lambda item: item[0]['x0'])
            if len(peers) < 2 or any(left['x1'] > right['x0']
                for (left, _), (right, _) in zip(peers, peers[1:])):
                continue
            following = min((other['y0'] for other in self.rectangles
                if other['y0'] > box['y1'] and other['x0'] < box['x1']
                and other['x1'] > box['x0']), default=float('inf'))
            descriptors = [fragment for fragment in self.fragments if fragment.bbox
                and box['y1'] <= fragment.bbox['y0'] < min(following, box['y1'] + 24)
                and box['x0'] - 1 <= fragment.bbox['x0']
                and fragment.bbox['x1'] <= box['x1'] + 1]
            for group in _wrapped_groups(descriptors):
                links.append(EvidenceContextLink(cell_id=cell.cell_id,
                    source_ids=[source.span_id for source in group], relation='column'))
            left, right = peers[0][0]['x0'], peers[-1][0]['x1']
            regional = []
            for fragment in self.fragments:
                b = fragment.bbox
                if (not b or b['y1'] >= box['y1'] or b['x1'] <= left or b['x0'] >= right
                    or any(fragment.char_start == value.char_start
                        and fragment.char_start + len(fragment.text) == value.char_end for value in cells)):
                    continue
                regional.append(fragment)
            links.extend(EvidenceContextLink(cell_id=cell.cell_id,
                source_ids=[fragment.span_id for fragment in group], relation='region')
                for group in _wrapped_groups(regional))
        return links

    def enclosure_links(self, cells):
        """A convex closed source path can enclose one value and its descriptor."""
        if not self.is_native_figure:
            return []
        shapes = [(path['bbox'], polygon) for path in self.paths
            if (polygon := _convex_polygon(path['commands'])) is not None]
        shapes.sort(key=lambda item: (item[0]['x1']-item[0]['x0']) * (item[0]['y1']-item[0]['y0']))
        links = []
        for cell in cells:
            if not cell.bbox:
                continue
            shape = next(((box, polygon) for box, polygon in shapes
                if _polygon_contains(polygon, cell.bbox)), None)
            if shape is None:
                continue
            box, polygon = shape
            inside = [other for other in cells if other.bbox and _polygon_contains(polygon, other.bbox)]
            if len(inside) != 1:
                continue
            descriptors = [fragment for fragment in self.fragments if fragment.bbox
                and _polygon_contains(polygon, fragment.bbox)
                and fragment.bbox['y1'] < cell.bbox['y0']
                and min(fragment.bbox['x1'], cell.bbox['x1'])
                    > max(fragment.bbox['x0'], cell.bbox['x0'])]
            for group in _wrapped_groups(descriptors):
                links.append(EvidenceContextLink(cell_id=cell.cell_id,
                    source_ids=[source.span_id for source in group], relation='column'))
            parent = next(((outer, outline) for outer, outline in shapes
                if outer is not box and _polygon_contains(outline, box)), None)
            if parent is None:
                continue
            outer, outline = parent
            regional = []
            for fragment in self.fragments:
                b = fragment.bbox
                if not b or any(fragment.char_start == other.char_start
                    and fragment.char_start + len(fragment.text) == other.char_end for other in cells):
                    continue
                caption = (outer['y0'] - 24 <= b['y0'] and b['y1'] < outer['y0']
                    and outer['x0'] - 1 <= b['x0'] and b['x1'] <= outer['x1'] + 1)
                if _polygon_contains(outline, b) or caption:
                    regional.append(fragment)
            links.extend(EvidenceContextLink(cell_id=cell.cell_id,
                source_ids=[fragment.span_id for fragment in group], relation='region')
                for group in _wrapped_groups(regional))
        return links


def _wrapped_groups(fragments):
    groups = []
    for fragment in sorted(fragments, key=lambda item: (item.bbox['y0'], item.bbox['x0'])):
        previous = groups[-1][-1] if groups else None
        adjacent = previous is not None and previous.object_id == fragment.object_id and (
            0 <= fragment.char_start - previous.char_start - len(previous.text) <= 2)
        continuation = previous is not None and fragment.text[:1].islower() and (
            previous.text.rstrip().endswith('-') or adjacent and previous.text[-1:].islower())
        if (previous and continuation
            and abs(previous.bbox['x0'] - fragment.bbox['x0']) <= 2
            and 0 <= fragment.bbox['y0'] - previous.bbox['y1'] <= 1.5 * (
                previous.bbox['y1'] - previous.bbox['y0'])):
            groups[-1].append(fragment)
        else:
            groups.append([fragment])
    return groups


def native_text_geometry(source, block, local_page):
    trace = block.get('source_trace') or {}
    native_figure = trace.get('parser') == 'pdf-native-figure-text'
    note = next((note for note in trace.get('notes', [])
        if note.startswith('native_word_indices:')), None)
    raw = local_page.get('native_text_blocks', [])
    if note is not None:
        indices = [int(index) for index in note.split(':', 1)[1].split(',') if index.isdigit()]
        native = [raw[index] for index in indices if index < len(raw)]
    else:
        native = [item for item in raw if source.bbox and _contains(source.bbox, item['bbox'], 1)]
    words = [Word(item['text'], item['bbox']['x0'], item['bbox']['x1'],
        item.get('baseline_points') if item.get('baseline_points') is not None else
            item['bbox']['y1'] - .2 * (item['bbox']['y1'] - item['bbox']['y0']),
        item.get('font_size_points') or item['bbox']['y1'] - item['bbox']['y0'],
        source_indices=(index,)) for index, item in enumerate(native)]
    result = NativeTextGeometry(is_native_figure=native_figure,
        paths=local_page.get('native_filled_paths', []), rectangles=[
        {axis: box[axis] for axis in ('x0', 'y0', 'x1', 'y1')}
        for box in local_page.get('native_filled_rectangles', [])])
    body_size = statistics.median(word.size for word in words) if words else 0
    for item, word in zip(native, words):
        if word.size < .72 * body_size:
            continue
        span = _slice(source, item['text'], {axis: item['bbox'][axis]
            for axis in ('x0', 'y0', 'x1', 'y1')}, word=True)
        if span is not None:
            result.words.append(span)
    if not native_figure:
        return result
    for line in build_lines(words):
        groups = []
        for word in line.words:
            if not groups or word.x0 - groups[-1][-1].x1 > 1.5 * line.size:
                groups.append([word])
            else:
                groups[-1].append(word)
        for group in groups:
            originals = [native[index]['bbox'] for word in group for index in word.source_indices]
            if not originals:
                continue
            span = _slice(source, ' '.join(word.text for word in group), _union(originals))
            if span is not None:
                result.fragments.append(span)
    return result


def context_links(rows, clauses, geometry):
    # The provider uses the complete literal inventory before extraction applies
    # clause guards, input budgets, retries, or value filtering.
    cells = [cell for row in rows for cell in row.cells
        if cell.candidate_kind in {'measurement', 'unresolved'}]
    links = []
    for clause in ([] if geometry.is_native_figure else clauses):
        inside = [cell for cell in cells if cell.char_start is not None and cell.char_end is not None
            and clause.char_start <= cell.char_start
            and cell.char_end <= clause.char_start + len(clause.text)]
        if len(inside) == 1:
            links.append(EvidenceContextLink(cell_id=inside[0].cell_id,
                source_ids=[clause.span_id], relation='same_clause'))
    links.extend(geometry.bar_links(cells))
    links.extend(geometry.enclosure_links(cells))
    return links
