from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber

from extract_esg.contracts.base import SourceTrace
from extract_esg.contracts.document import DocumentIR, LocalPdfPage, LocalTableCandidate, LocalTextBlock, PageRef


class LocalPdfProcessor:
    """Deterministic PDF evidence builder.

    This processor never calls OCR or LLM models. It extracts whatever the PDF
    natively exposes and prepares evidence anchors for cloud model tasks.
    """

    version = "local-pdf-0.1"

    def inspect(self, artifact_path: str | Path, *, report_id: str | None = None, run_id: str = "local") -> DocumentIR:
        path = Path(artifact_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        report = report_id or path.stem
        trace = SourceTrace(run_id=run_id, producer="LocalPdfProcessor", version=self.version)
        pages: list[LocalPdfPage] = []

        with pdfplumber.open(path) as pdf:
            for index, page in enumerate(pdf.pages):
                page_ref = PageRef(page_index=index)
                text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                blocks = []
                if text:
                    blocks.append(
                        LocalTextBlock(
                            page_ref=page_ref,
                            text=text,
                            role="unknown",
                            source=trace,
                            quality_flags=[],
                        )
                    )

                table_candidates = []
                for table in page.find_tables() or []:
                    row_count = len(table.rows or [])
                    column_count = None
                    if table.rows:
                        column_count = max((len(row.cells or []) for row in table.rows), default=0)
                    table_candidates.append(
                        LocalTableCandidate(
                            page_ref=page_ref,
                            row_count=row_count,
                            column_count=column_count,
                            source=trace,
                            quality_flags=[],
                        )
                    )

                flags = []
                if not text.strip():
                    flags.append("native_text_empty")
                if table_candidates:
                    flags.append("table_candidate_detected")

                pages.append(
                    LocalPdfPage(
                        page_ref=page_ref,
                        width=float(page.width),
                        height=float(page.height),
                        rotation=int(getattr(page, "rotation", 0) or 0),
                        native_text=text,
                        text_blocks=blocks,
                        table_candidates=table_candidates,
                        quality_flags=flags,
                    )
                )

        return DocumentIR(
            report_id=report,
            artifact_path=path,
            sha256=digest,
            pages=pages,
            source_traces=[trace],
        )

