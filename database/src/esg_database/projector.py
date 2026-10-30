"""Post-receipt projection can fail or retry independently of raw reception."""
import logging
import threading
from .client import DatabaseError
from .projection import project_export, TRANSFORM_VERSION

log = logging.getLogger(__name__)


class ProjectionWorker:
    def __init__(self, bridge):
        self.bridge = bridge
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def project(self, export_id):
        with self.lock:
            try:
                project_export(self.bridge, export_id)
            except Exception as exc:
                code = exc.code if isinstance(exc, DatabaseError) else "invalid_projection_source"
                retryable = code in {"adapter_unavailable", "adapter_timeout", "database_error"}
                try:
                    self.bridge.call("record_projection_failure", {"export_id": export_id,
                        "transform_version": TRANSFORM_VERSION, "error_code": code, "retryable": retryable})
                except DatabaseError:
                    pass
                log.warning("Capture projection did not complete: %s", code)

    def tick(self):
        if not self.bridge.call("ingestion_health", {}).get("products_ready"):
            return
        for row in self.bridge.call("pending_projections", {"transform_version": TRANSFORM_VERSION}):
            if self.stop_event.is_set():
                break
            self.project(row["export_id"])

    def start(self):
        def run():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except DatabaseError:
                    log.warning("Projection adapter unavailable; raw receipts remain independent")
                self.wake_event.wait(5)
                self.wake_event.clear()
        self.thread = threading.Thread(target=run, name="capture-projection", daemon=True)
        self.thread.start()

    def wake(self):
        self.wake_event.set()

    def close(self):
        self.stop_event.set()
        self.wake()
        if self.thread:
            self.thread.join(timeout=5)
