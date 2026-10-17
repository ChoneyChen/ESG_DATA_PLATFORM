from __future__ import annotations

from dataclasses import dataclass

from esg_v2.document.contracts import DocumentIR, SpreadIR
from esg_v2.document.spread_builder import HorizontalSpreadBuilder
from esg_v2.document.spread_pair_analysis import SpreadPairAnalyzer


@dataclass(frozen=True)
class SpreadPreflightDecision:
    classification: str
    confidence: float
    content_dependency: bool
    requires_detailed_review: bool
    signals: tuple[str, ...]


class SpreadPreflightClassifier:
    """Separates information-bearing seams from visual-only continuity locally."""

    def classify(self, document: DocumentIR) -> DocumentIR:
        pages = {item.page_id: item for item in document.pages}
        pages_by_index = {item.page_index: item for item in document.pages}
        analyzer = SpreadPairAnalyzer()

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

            decision = self._decision(document, spread, analyzer, pages_by_index)
            spread.classification = decision.classification
            spread.preflight_confidence = decision.confidence
            spread.content_dependency = decision.content_dependency
            spread.requires_detailed_review = decision.requires_detailed_review
            spread.preflight_signals = list(decision.signals)
            spread.resolution_source = "local_preflight"
            for signal in decision.signals:
                if signal not in spread.reason_codes:
                    spread.reason_codes.append(signal)

            if decision.classification == "standalone_pages":
                spread.status = "rejected"
                self._replace_flag(
                    spread.quality_flags,
                    "horizontal_spread_candidate",
                    "horizontal_spread_rejected_local_preflight",
                )
                for page_id in spread.page_ids:
                    page = pages.get(page_id)
                    if page:
                        self._replace_flag(
                            page.quality_flags,
                            "horizontal_spread_candidate",
                            "horizontal_spread_rejected_local_preflight",
                        )
            elif decision.classification == "visual_continuity":
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

    def _decision(
        self,
        document: DocumentIR,
        spread: SpreadIR,
        analyzer: SpreadPairAnalyzer,
        pages_by_index,
    ) -> SpreadPreflightDecision:
        analysis = analyzer.analyze(document, spread)
        signals: list[str] = []
        if len(spread.page_indices) == 2:
            left = pages_by_index.get(spread.page_indices[0])
            right = pages_by_index.get(spread.page_indices[1])
            metrics = HorizontalSpreadBuilder.visual_seam_metrics(
                left.page_image_path if left else None,
                right.page_image_path if right else None,
            )
            if metrics:
                spread.seam_metrics.update(metrics)
                if (
                    metrics["structured_row_ratio"] < HorizontalSpreadBuilder.MIN_STRUCTURED_SEAM_ROWS
                    or metrics["structured_row_overlap"] < HorizontalSpreadBuilder.MIN_STRUCTURED_SEAM_OVERLAP
                ):
                    signals.extend(("preflight_flat_or_background_only_seam", "preflight_standalone_pages"))
                    return SpreadPreflightDecision("standalone_pages", 0.99, False, False, tuple(signals))

        if analysis.vertical_table_continuation_pairs and not analysis.content_pairs:
            signals.extend(("preflight_repeated_table_headers", "preflight_likely_vertical_continuation"))
            return SpreadPreflightDecision("standalone_pages", 0.96, False, False, tuple(signals))
        if analysis.content_pairs:
            signals.extend(("preflight_geometrically_matched_content_pair", "preflight_information_crossing"))
            return SpreadPreflightDecision("content_crossing", 0.97, True, True, tuple(signals))
        if analysis.visual_pairs:
            signals.extend(("preflight_matched_visual_objects_only", "preflight_no_cross_seam_text_or_table"))
            return SpreadPreflightDecision("visual_continuity", 0.96, False, False, tuple(signals))
        signals.extend(("preflight_no_geometrically_matched_entities", "preflight_standalone_pages"))
        return SpreadPreflightDecision("standalone_pages", 0.95, False, False, tuple(signals))

    @staticmethod
    def _replace_flag(flags: list[str], old: str, new: str) -> None:
        flags[:] = [flag for flag in flags if flag != old]
        if new not in flags:
            flags.append(new)
