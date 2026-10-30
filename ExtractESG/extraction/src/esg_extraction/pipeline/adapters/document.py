"""HTTP document adapter: only IR IDs and stable evidence object references cross services."""

import requests
from esg_contracts.evidence import DocumentReference, ObjectReference, EvidenceFrame


class HttpDocumentPort:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False

    def _request(self, method, path, body=None):
        response = self.session.request(
            method, self.base_url + path, json=body, timeout=120
        )
        response.raise_for_status()
        return response.json()

    def document(self, ir_run_id):
        return DocumentReference.model_validate(
            self._request("GET", f"/api/evidence/{ir_run_id}")
        )

    def search(self, ir_run_id, intent):
        payload = self._request(
            "POST", f"/api/evidence/{ir_run_id}/search", intent.model_dump(mode="json")
        )
        return [ObjectReference.model_validate(row) for row in payload["objects"]]

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
