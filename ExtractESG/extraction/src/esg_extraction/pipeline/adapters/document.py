"""HTTP document adapter: only IR IDs and stable evidence object references cross services."""

import requests
from ..ports import DocumentUnavailable
from esg_contracts.evidence import DocumentReference, ObjectReference, EvidenceFrame, ObjectSearchPage


class HttpDocumentPort:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False

    def _request(self, method, path, body=None):
        try:
            response = self.session.request(
                method, self.base_url + path, json=body, timeout=120
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise DocumentUnavailable("Document evidence service unavailable; saved progress retained") from exc
        if response.status_code >= 500:
            raise DocumentUnavailable(f"Document evidence service returned HTTP {response.status_code}; saved progress retained")
        response.raise_for_status()
        return response.json()

    def document(self, ir_run_id):
        return DocumentReference.model_validate(
            self._request("GET", f"/api/evidence/{ir_run_id}")
        )

    def search(self, ir_run_id, intent):
        return self.search_page(ir_run_id, intent).objects

    def search_page(self, ir_run_id, intent):
        payload = self._request(
            "POST", f"/api/evidence/{ir_run_id}/search", intent.model_dump(mode="json")
        )
        return ObjectSearchPage.model_validate(payload)

    def read(self, ir_run_id, reference, chunk_index=0):
        return EvidenceFrame.model_validate(
            self._request(
                "POST",
                f"/api/evidence/{ir_run_id}/read",
                {
                    "reference": reference.model_dump(mode="json"),
                    "chunk_index": chunk_index,
                },
            )
        )
