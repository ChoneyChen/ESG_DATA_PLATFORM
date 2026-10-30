"""Local composition root. Document algorithms do not import the Console facade."""
import os
from esg_document.api import main as document
from esg_pipeline.extraction import ExtractionCoordinator
from esg_pipeline.contracts import PipelineTaskType
from .sessions import extraction_router

app = document.app


def queue_clause_run(run_id, name, timeout):
    return document._queue_task(
        task_type=PipelineTaskType.TARGETED_EXTRACTION, operation="clause_extraction",
        title=name, native_job_id=run_id,
        payload={"run_id": run_id, "_total_timeout_seconds": timeout}, deduplicate_native=True,
    )


extraction_plans = ExtractionCoordinator(
    document.pipeline_store, document.report_processing, queue_clause_run,
    document.cancel_pipeline_task, document.settings.extraction_backend_url,
)
app.include_router(extraction_router(extraction_plans, document._require_processing_ready))


@app.on_event("startup")
def start_plans():
    if not os.getenv("PYTEST_CURRENT_TEST"):
        extraction_plans.start()


@app.on_event("shutdown")
def stop_plans():
    extraction_plans.stop()
