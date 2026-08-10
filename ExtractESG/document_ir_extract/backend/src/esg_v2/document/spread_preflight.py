from __future__ import annotations

from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, SpreadIR
from esg_v2.document.text_normalization import visible_text


@dataclass(frozen=True)
class SpreadPreflightDecision:
    classification: str
    confidence: float
    content_dependency: bool
    requires_detailed_review: bool
    signals: tuple[str, ...]


class SpreadPreflightClassifier:
    """Separates information-bearing seams from visual-only continuity locally."""

    NON_CONTENT_BLOCK_TYPES = {
        "header",
        "footer",
        "page_number",
        "number",
        "header_image",
        "footer_image",
    }
    DATA_FIGURE_TYPES = {"chart", "table", "infographic"}

    def classify(self, document: DocumentIR) -> DocumentIR:
        blocks = {item.block_id: item for item in document.blocks}
        tables = {item.table_id: item for item in document.tables}
        figures = {item.figure_id: item for item in document.figures}
        pages = {item.page_id: item for item in document.pages}

        for spread in document.spreads:
            if spread.status in {"confirmed", "rejected"}:
                spread.resolution_source = "inherited"
                spread.requires_detailed_review = False
                if spread.status == "rejected":
                    spread.classification = "standalone_pages"
                    spread.content_dependency = False
                elif spread.linked_entities:
                    spread.classification = "content_crossing"
                    spread.content_dependency = True
                continue

            decision = self._decision(spread, blocks, tables, figures)
            spread.classification = decision.classification
            spread.preflight_confidence = decision.confidence
            spread.content_dependency = decision.content_dependency
            spread.requires_detailed_review = decision.requires_detailed_review
            spread.preflight_signals = list(decision.signals)
            spread.resolution_source = "local_preflight"
            for signal in decision.signals:
                if signal not in spread.reason_codes:
                    spread.reason_codes.append(signal)

            if decision.classification == "visual_continuity":
                spread.status = "visual_continuity"
                self._replace_flag(
                    spread.quality_flags,
                    "horizontal_spread_candidate",
                    "horizontal_spread_visual_continuity",
                )
                for page_id in spread.page_ids:
                    page = pages.get(page_id)
                    if page:
                        self._replace_flag(
                            page.quality_flags,
                            "horizontal_spread_candidate",
                            "horizontal_spread_visual_continuity",
                        )
            else:
                spread.status = "candidate"
                if "horizontal_spread_candidate" not in spread.quality_flags:
                    spread.quality_flags.append("horizontal_spread_candidate")
        return document

    def _decision(self, spread: SpreadIR, blocks, tables, figures) -> SpreadPreflightDecision:
        page_indices = set(spread.page_indices)
        informative_by_page = {page_index: [] for page_index in page_indices}
        visual_by_page = {page_index: [] for page_index in page_indices}
        table_by_page = {page_index: [] for page_index in page_indices}

        for entity_id in spread.member_entity_ids:
            block = blocks.get(entity_id)
            if block is not None and block.page_index in page_indices:
                if self._informative_block(block):
                    informative_by_page[block.page_index].append(block.block_id)
                continue
            table = tables.get(entity_id)
            if table is not None and table.page_index in page_indices:
                table_by_page[table.page_index].append(table.table_id)
                informative_by_page[table.page_index].append(table.table_id)
                continue
            figure = figures.get(entity_id)
            if figure is not None and figure.page_index in page_indices:
                visual_by_page[figure.page_index].append(figure.figure_id)
                if self._data_figure(figure):
                    informative_by_page[figure.page_index].append(figure.figure_id)

        informative_pages = [page for page, ids in informative_by_page.items() if ids]
        visual_count = sum(len(ids) for ids in visual_by_page.values())
        table_pages = [page for page, ids in table_by_page.items() if ids]
        signals: list[str] = []

        if len(table_pages) == 2:
            signals.extend(("preflight_tables_touch_both_seam_sides", "preflight_information_crossing"))
            return SpreadPreflightDecision("content_crossing", 0.99, True, True, tuple(signals))
        if len(informative_pages) == 2:
            signals.extend(("preflight_information_objects_on_both_sides", "preflight_information_crossing"))
            return SpreadPreflightDecision("content_crossing", 0.97, True, True, tuple(signals))
        if not informative_pages and visual_count:
            signals.extend(("preflight_visual_objects_only", "preflight_no_cross_seam_text_or_table"))
            return SpreadPreflightDecision("visual_continuity", 0.96, False, False, tuple(signals))
        if len(informative_pages) == 1 and visual_count:
            signals.extend(("preflight_mixed_text_and_visual_seam", "preflight_detailed_review_required"))
            return SpreadPreflightDecision("uncertain", 0.70, True, True, tuple(signals))

        signals.append("preflight_insufficient_local_seam_semantics")
        return SpreadPreflightDecision("uncertain", 0.55, True, True, tuple(signals))

    def _informative_block(self, block) -> bool:
        block_type = str(block.block_type or "").casefold()
        if block_type in self.NON_CONTENT_BLOCK_TYPES:
            return False
        text = visible_text(block.text or block.markdown or "")
        if "table" in block_type or "<table" in (block.markdown or "").casefold():
            return bool(text)
        return len(text) >= 4

    def _data_figure(self, figure) -> bool:
        visual_type = str(figure.visual_type or "").casefold()
        if visual_type in self.DATA_FIGURE_TYPES:
            return True
        if figure.chart_spec:
            return True
        legend = " ".join(figure.legend_text)
        return bool(legend.strip()) and any(character.isdigit() for character in legend)

    @staticmethod
    def _replace_flag(flags: list[str], old: str, new: str) -> None:
        flags[:] = [flag for flag in flags if flag != old]
        if new not in flags:
            flags.append(new)
