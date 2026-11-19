"""Read original PDF pages and optional IR metadata over the document API."""

import requests
from urllib.parse import quote
from ..ports import DocumentUnavailable
from esg_contracts.evidence import DocumentReference, PDFPageText


class HttpDocumentPort:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False

    def _request(self, method, path, body=None, *, binary=False):
        try:
            response = self.session.request(
                method, self.base_url + path, json=body, timeout=120
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise DocumentUnavailable("Document evidence service unavailable; saved progress retained") from exc
        if response.status_code >= 500:
            raise DocumentUnavailable(f"Document evidence service returned HTTP {response.status_code}; saved progress retained")
        response.raise_for_status()
        return response.content if binary else response.json()

    def pdf(self, asset_id):
        return DocumentReference.model_validate(self._request(
            "GET", f"/api/report-assets/{quote(asset_id, safe='')}/reading-source"))

    def pages(self, asset_id):
        return [PDFPageText.model_validate(item) for item in self._request(
            "GET", f"/api/report-assets/{quote(asset_id, safe='')}/reading-pages")]

    def image(self, asset_id, page_index, *, thumbnail=False):
        return self._request("GET", f"/api/report-assets/{quote(asset_id, safe='')}/"
            f"reading-pages/{page_index}/image?thumbnail={str(thumbnail).lower()}", binary=True)

    def document(self, ir_run_id):
        return DocumentReference.model_validate(
            self._request("GET", f"/api/evidence/{ir_run_id}")
        )
