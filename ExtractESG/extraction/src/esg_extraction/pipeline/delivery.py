"""Automatic, idempotent raw-data outbox. Database acceptance is not publishing."""
import os
from urllib.parse import urlsplit
import requests
from esg_contracts.exports import ExportManifest, IngestionReceipt
from .exporting import export_archive, json_bytes
from hashlib import sha256


def deliver(run_id, store, artifacts, base_url):
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Configure a credential-free database service URL")
    state = store.get(run_id)
    export_id = state["delivery"]["export_id"]
    manifest = ExportManifest.model_validate(artifacts.read(run_id, f"exports/{export_id}/manifest.json"))
    attempted = False
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.get(base_url.rstrip("/") + "/v1/ingestion/receipts/" + export_id, timeout=10)
            if response.status_code == 404:
                attempted = True
                response = client.post(base_url.rstrip("/") + "/v1/ingestion/exports", data=export_archive(run_id, manifest, artifacts),
                    headers={"Content-Type": "application/zip", "Idempotency-Key": export_id}, timeout=60)
            response.raise_for_status()
            receipt = IngestionReceipt.model_validate(response.json())
            if receipt.export_id != export_id or receipt.input_digest != sha256(json_bytes(manifest.model_dump(mode="json"))).hexdigest():
                raise ValueError("Receiver receipt differs from this sealed export")
            expected = next(member.records for member in manifest.files if member.role == "facts")
            if receipt.accepted_facts + receipt.rejected_facts != expected:
                raise ValueError("Receipt did not account for every exported source fact")
            artifacts.write(run_id, f"exports/{export_id}/receipt.json", receipt.model_dump(mode="json"))
            return store.update(run_id, delivery={"export_id": export_id,
                "state": {"accepted":"delivered", "partial":"partial", "rejected":"failed"}[receipt.status],
                "accepted_facts": receipt.accepted_facts, "rejected_facts": receipt.rejected_facts, "retryable": False})
    except requests.HTTPError as exc:
        permanent = exc.response is not None and 400 <= exc.response.status_code < 500
        return store.update(run_id, delivery={"export_id": export_id,
            "state": "failed" if permanent or not attempted else "unknown", "retryable": not permanent,
            "message": "数据库拒绝此封存包，请查看接收诊断。" if permanent else "等待数据库连接恢复后自动核对回执。"})
    except requests.RequestException:
        return store.update(run_id, delivery={"export_id": export_id, "state":"unknown" if attempted else "failed",
            "message":"等待数据库连接恢复后自动核对回执。"})
    except (ValueError, OSError):
        return store.update(run_id, delivery={"export_id": export_id, "state":"failed", "retryable":False, "message":"封存文件或接收回执不一致。"})


class DeliveryOutbox:
    def __init__(self, resource_factory):
        import threading
        self.resource_factory = resource_factory
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        import threading
        if not os.getenv("ESG_DATABASE_INGEST_URL"):
            return
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def work(self):
        while not self.stop_event.is_set():
            _, store, artifacts, _ = self.resource_factory()
            for state in store.list():
                delivery = state.get("delivery") or {}
                if delivery.get("export_id") and delivery.get("retryable", True) and delivery.get("state") in {"prepared", "failed", "unknown"}:
                    try:
                        deliver(state["run_id"], store, artifacts, os.environ["ESG_DATABASE_INGEST_URL"])
                    except (ValueError, KeyError, OSError):
                        pass
            self.stop_event.wait(15)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
