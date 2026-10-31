"""Local composition root. Document algorithms do not import the Console facade."""
import os
from esg_document.api import main as document
from esg_pipeline.extraction import ExtractionCoordinator
from esg_pipeline.contracts import PipelineTaskType
from .sessions import extraction_router
from .products import ProductStatus
from esg_database.http_client import ProductClient

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
document.library_deletion.configure(
    cancel_task=document.cancel_pipeline_task, processing=document.report_processing, extractions=extraction_plans,
)

product_status=ProductStatus(ProductClient(os.getenv("ESG_DATABASE_INGEST_URL","http://127.0.0.1:18280"),timeout=3))
app.include_router(extraction_router(extraction_plans, document._require_processing_ready,product_status))


@app.on_event("startup")
def start_plans():
    if not os.getenv("PYTEST_CURRENT_TEST"):
        extraction_plans.start()


@app.on_event("shutdown")
def stop_plans():
    extraction_plans.stop()
