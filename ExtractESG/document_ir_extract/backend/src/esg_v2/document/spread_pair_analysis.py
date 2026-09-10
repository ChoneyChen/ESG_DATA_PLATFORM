from __future__ import annotations

import re
from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, SpreadIR, TableIR
from esg_v2.document.text_normalization import visible_text


@dataclass(frozen=True)
class SpreadEntityPair:
    left_id: str
    right_id: str
    entity_type: str
    vertical_overlap: float
    alignment_score: float
    information_bearing: bool


@dataclass(frozen=True)
class SpreadPairAnalysis:
    content_pairs: tuple[SpreadEntityPair, ...] = ()
    visual_pairs: tuple[SpreadEntityPair, ...] = ()
    vertical_table_continuation_pairs: tuple[SpreadEntityPair, ...] = ()

    @property
    def matched_pairs(self) -> tuple[SpreadEntityPair, ...]:
        return (*self.content_pairs, *self.visual_pairs)

    @property
    def unique_link_pair(self) -> SpreadEntityPair | None:
        return self.content_pairs[0] if len(self.content_pairs) == 1 else None


class SpreadPairAnalyzer:
    """Matches only geometrically compatible entities on opposite seam sides."""

    MIN_VERTICAL_OVERLAP = 0.48
    MAX_CENTER_DELTA = 0.18
    NON_CONTENT_BLOCK_TYPES = {
        "header",
        "footer",
        "page_number",
        "number",
        "header_image",
        "footer_image",
    }
    DATA_FIGURE_TYPES = {"chart", "table", "infographic"}

    def analyze(self, document: DocumentIR, spread: SpreadIR) -> SpreadPairAnalysis:
        return self.analyze_members(document, spread.page_indices, spread.member_entity_ids)

    def analyze_members(
        self,
        document: DocumentIR,
        page_indices: list[int],
        member_entity_ids: list[str],
    ) -> SpreadPairAnalysis:
        if len(page_indices) != 2:
            return SpreadPairAnalysis()
        left_page, right_page = page_indices
        pages = {page.page_index: page for page in document.pages}
        if left_page not in pages or right_page not in pages:
            return SpreadPairAnalysis()

        member_ids = set(member_entity_ids)
        entities = []
        for entity_type, values in (
            ("table", document.tables),
            ("figure", document.figures),
            ("block", document.blocks),
        ):
            for entity in values:
                entity_id = self._entity_id(entity, entity_type)
                if entity_id not in member_ids or entity.page_index not in {left_page, right_page}:
                    continue
                if entity.bbox is None or not self._eligible(entity, entity_type):
                    continue
                page = pages[entity.page_index]
                entities.append(
                    (
                        entity_id,
                        entity_type,
                        entity,
                        entity.page_index,
                        entity.bbox.y0 / max(1.0, page.height),
                        entity.bbox.y1 / max(1.0, page.height),
                    )
                )

        candidates: list[tuple[float, SpreadEntityPair, object, object]] = []
        for left in (item for item in entities if item[3] == left_page):
            for right in (item for item in entities if item[3] == right_page):
                if left[1] != right[1] or not self._compatible(left[2], right[2], left[1]):
                    continue
                overlap = max(0.0, min(left[5], right[5]) - max(left[4], right[4]))
                minimum_height = max(1e-6, min(left[5] - left[4], right[5] - right[4]))
                overlap_ratio = overlap / minimum_height
                center_delta = abs((left[4] + left[5]) / 2 - (right[4] + right[5]) / 2)
                if overlap_ratio < self.MIN_VERTICAL_OVERLAP or center_delta > self.MAX_CENTER_DELTA:
                    continue
                score = 0.72 * min(1.0, overlap_ratio) + 0.28 * max(
                    0.0,
                    1.0 - center_delta / self.MAX_CENTER_DELTA,
                )
                information_bearing = self._information_bearing(left[2], right[2], left[1])
                candidates.append(
                    (
                        score,
                        SpreadEntityPair(
                            left_id=left[0],
                            right_id=right[0],
                            entity_type=left[1],
                            vertical_overlap=round(overlap_ratio, 4),
                            alignment_score=round(score, 4),
                            information_bearing=information_bearing,
                        ),
                        left[2],
                        right[2],
                    )
                )

        content: list[SpreadEntityPair] = []
        visual: list[SpreadEntityPair] = []
        vertical_tables: list[SpreadEntityPair] = []
        used_left: set[str] = set()
        used_right: set[str] = set()
        for _, pair, left_entity, right_entity in sorted(candidates, key=lambda item: item[0], reverse=True):
            if pair.left_id in used_left or pair.right_id in used_right:
                continue
            used_left.add(pair.left_id)
            used_right.add(pair.right_id)
            if pair.entity_type == "table" and self._likely_vertical_table_continuation(
                left_entity,
                right_entity,
            ):
                vertical_tables.append(pair)
                continue
            if pair.information_bearing:
                content.append(pair)
            else:
                visual.append(pair)
        return SpreadPairAnalysis(tuple(content), tuple(visual), tuple(vertical_tables))

    def _eligible(self, entity, entity_type: str) -> bool:
        if entity_type != "block":
            return True
        if entity.table_id or entity.figure_id:
            return False
        if str(entity.block_type or "").casefold() in self.NON_CONTENT_BLOCK_TYPES:
            return False
        return len(visible_text(entity.text or entity.markdown or "")) >= 4

    @staticmethod
    def _compatible(left, right, entity_type: str) -> bool:
        if entity_type == "table":
            if left.row_count > 1 and right.row_count > 1:
                return min(left.row_count, right.row_count) / max(left.row_count, right.row_count) >= 0.72
            return True
        if entity_type == "block":
            return str(left.block_type or "") == str(right.block_type or "")
        return True

    def _information_bearing(self, left, right, entity_type: str) -> bool:
        if entity_type in {"table", "block"}:
            return True
        return self._data_figure(left) or self._data_figure(right)

    def _data_figure(self, figure) -> bool:
        if str(figure.visual_type or "").casefold() in self.DATA_FIGURE_TYPES:
            return True
        if figure.chart_spec:
            return True
        legend = " ".join(figure.legend_text)
        return bool(legend.strip()) and any(character.isdigit() for character in legend)

    @classmethod
    def _likely_vertical_table_continuation(cls, left: TableIR, right: TableIR) -> bool:
        if not left.column_count or left.column_count != right.column_count:
            return False
        left_headers = cls._table_header_tokens(left)
        right_headers = cls._table_header_tokens(right)
        if not left_headers or not right_headers:
            return False
        similarity = len(left_headers & right_headers) / max(1, len(left_headers | right_headers))
        return similarity >= 0.60

    @staticmethod
    def _table_header_tokens(table: TableIR) -> set[str]:
        header_rows = set(table.header_row_indices)
        if not header_rows and table.cells:
            header_rows = {min(cell.row_index for cell in table.cells)}
        text = " ".join(cell.text for cell in table.cells if cell.row_index in header_rows)
        return {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text)
            if len(token) >= 2
        }

    @staticmethod
    def _entity_id(entity, entity_type: str) -> str:
        return str(getattr(entity, f"{entity_type}_id"))
