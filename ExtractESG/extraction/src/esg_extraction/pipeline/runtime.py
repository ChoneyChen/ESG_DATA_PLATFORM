"""Composition root: filesystem, HTTP and model paths stop here."""

import os
from pathlib import Path
from esg_clauses.repository import ClauseRepository
from .state import RunStore
from .adapters.artifacts import FileArtifactPort
from .adapters.document import HttpDocumentPort


def resources():
    data = os.getenv("ESG_DATA_ROOT")
    clauses = os.getenv("ESG_CLAUSE_ROOT")
    if not data or not clauses:
        from esg_runtime.layout import DATA_ROOT, CLAUSES_ROOT

        data = data or str(DATA_ROOT)
        clauses = clauses or str(CLAUSES_ROOT)
    data_root = Path(data)
    return (
        ClauseRepository(clauses),
        RunStore(
            Path(
                os.getenv(
                    "ESG_EXTRACTION_RUNS_DB",
                    str(data_root / "control/extractions.sqlite3"),
                )
            )
        ),
        FileArtifactPort(
            Path(os.getenv("ESG_EXTRACTION_DATA_ROOT", str(data_root / "extractions")))
        ),
        HttpDocumentPort(
            os.getenv("ESG_DOCUMENT_BACKEND_URL", "http://127.0.0.1:18080")
        ),
    )


def engine_digest():
    from esg_runtime.release import package_digest

    return package_digest(
        (
            "esg_extraction",
            "esg_contracts",
            "esg_data",
            "esg_reports",
            "esg_runtime",
            "esg_clauses",
        )
    )


def execute(run_id, *, model=None):
    from esg_contracts.facts import RunRequest
    from esg_contracts.evidence import DocumentReference
    from .adapters.local_model import LocalQwenModel
    from .engine import ExtractionBackbone, PipelineCancelled, ENGINE_VERSION

    repo, store, artifacts, documents = resources()
    state = store.claim(run_id)
    model = model or LocalQwenModel(
        Path(
            os.getenv(
                "QWEN_VLM_MODEL_PATH",
                str(Path.home() / "Desktop/model/Qwen3.5-9B-4bit"),
            )
        )
    )
    model.progress_callback = lambda event: store.update(run_id, current_call=event)
    try:
        fingerprint = engine_digest()
        if state.get("engine_fingerprint") != fingerprint:
            raise ValueError(
                "Extraction code changed after run creation; submit a new run"
            )
        actual = documents.document(state["request"]["ir_run_id"])
        frozen_document = DocumentReference.model_validate(state["document"])
        if actual.evidence_identity() != frozen_document.evidence_identity():
            raise ValueError(
                "Document evidence changed after run creation; submit a new run"
            )
        body = ExtractionBackbone(
            documents,
            model,
            artifacts,
            engine_identity={"version": ENGINE_VERSION, "fingerprint": fingerprint},
        ).run(
            run_id,
            RunRequest.model_validate(state["request"]),
            repo.thaw(state["targets"]),
            document=frozen_document,
            cancel_check=lambda: store.get(run_id)["cancel_requested"],
            on_progress=lambda event: store.update(run_id, progress=event),
        )
        status = (
            "completed"
            if body["summary"]["assessment"]["execution_state"] == "completed"
            else "partial"
        )
        result = store.update(
            run_id,
            status=status,
            summary=body["summary"],
            finished_at=__import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        )
        from .exporting import seal_export

        try:
            manifest = seal_export(run_id, store, artifacts)
            result = store.update(
                run_id,
                delivery={"export_id": manifest.export_id, "state": "prepared"},
                export_download_url=f"/api/runs/{run_id}/exports/{manifest.export_id}",
            )
        except (ValueError, OSError):
            result = store.update(
                run_id,
                delivery={"state": "failed", "message": "原值已保存，导出准备失败。"},
            )
        return result
    except PipelineCancelled:
        return store.update(run_id, status="cancelled")
    except Exception as exc:
        store.update(
            run_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:1000]}"
        )
        raise
    finally:
        model.close()
