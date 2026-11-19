"""Direct access to the original PDF's page text and pixels."""

from io import BytesIO
from threading import Lock

import pypdfium2 as pdfium
from fastapi import APIRouter, HTTPException, Query, Response

from esg_contracts.evidence import DocumentReference, PDFPageText
from esg_document.ocr.page_selection import ClausePageSelector


def reading_pages_router(assets, reports):
    router = APIRouter(prefix="/api/report-assets/{asset_id}")
    pdf_lock = Lock()

    def original(asset_id):
        try:
            asset = assets.get(asset_id)
            return asset["sha256"], assets.resolve(asset_id)
        except (KeyError, ValueError, FileNotFoundError):
            raise HTTPException(404, "Original PDF not found") from None

    @router.get("/reading-source", response_model=DocumentReference)
    def reading_source(asset_id: str):
        sha, _ = original(asset_id)
        report = reports.by_sha(sha)
        return DocumentReference(document_id=report.pdf_id if report else asset_id,
            ir_run_id="pdf-" + sha, ir_revision=1, content_digest=sha, source_sha256=sha,
            report_resource=report, label=report.original_filename if report else None,
            source_kind="pdf")

    @router.get("/reading-pages", response_model=list[PDFPageText])
    def reading_pages(asset_id: str):
        _, path = original(asset_id)
        with pdf_lock:
            return [PDFPageText(page_index=i, text=text) for i, text
                    in enumerate(ClausePageSelector._page_texts(path))]

    @router.get("/reading-pages/{page_index}/image")
    def page_image(asset_id: str, page_index: int, thumbnail: bool = Query(False)):
        _, path = original(asset_id)
        with pdf_lock, pdfium.PdfDocument(str(path)) as pdf:
            if not 0 <= page_index < len(pdf):
                raise HTTPException(404, "PDF page not found")
            page = pdf[page_index]
            try:
                longest = max(page.get_size())
                scale = min((960 if thumbnail else 2400) / longest, 3.0)
                bitmap = page.render(scale=scale)
                try:
                    with bitmap.to_pil().convert("RGB") as image, BytesIO() as stream:
                        image.save(stream, "JPEG", quality=90)
                        return Response(stream.getvalue(), media_type="image/jpeg")
                finally:
                    bitmap.close()
            finally:
                page.close()

    return router
