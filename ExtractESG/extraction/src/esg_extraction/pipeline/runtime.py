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


def model_for(selection, store):
    if selection is not None and selection.provider != "local":
        from esg_contracts.model_providers import CLOUD_NAMES
        from .adapters.model_credentials import ModelCredentialStore

        key = ModelCredentialStore(store, selection.provider).read()
        if key is None:
            raise ValueError(f"{CLOUD_NAMES[selection.provider]} API key is not configured in the local workbench")
        if selection.provider == "deepseek":
            from .adapters.deepseek_model import DeepSeekModel

            return DeepSeekModel(selection.model, key)
        from .adapters.dashscope_model import DashScopeModel

        return DashScopeModel(selection.model, key)

    from .adapters.local_model import LocalQwenModel

    return LocalQwenModel(Path(os.getenv(
        "QWEN_VLM_MODEL_PATH", str(Path.home() / "Desktop/model/Qwen3.5-9B-4bit"),
    )))


def input_document(documents, request):
    return documents.pdf(request.asset_id) if request.asset_id else documents.document(request.ir_run_id)


def saved_reading_progress(checkpoint, previous, summary, stage):
    previous = previous or {}
    jobs = checkpoint.get("jobs")
    return {
        "stage": stage,
        "object_id": previous.get("object_id"),
        "current": checkpoint.get("position", previous.get("current", 0)),
        "total": len(jobs) if jobs is not None else previous.get("total", 0),
        "model_calls": checkpoint.get("calls", previous.get("model_calls", 0)),
        "fact_count": summary.get("source_facts", previous.get("fact_count", 0)),
    }


def execute(run_id, *, model=None, waiting_tasks=0):
    from esg_contracts.facts import RunRequest
    from esg_contracts.evidence import DocumentReference
    from .engine import ExtractionBackbone, PipelineCancelled, ExecutionYield, ENGINE_VERSION
    from .result_store import ResultStore
    from .ports import DocumentUnavailable, ProviderInterruption

    repo, store, artifacts, documents = resources()
    state = store.claim(run_id)
    results = ResultStore(run_id, artifacts)
    document_model = None
    try:
        fingerprint = engine_digest()
        if state.get("engine_fingerprint") != fingerprint:
            raise ValueError(
                "Extraction code changed after run creation; submit a new run"
            )
        request = RunRequest.model_validate(state["request"])
        actual = input_document(documents, request)
        frozen_document = DocumentReference.model_validate(state["document"])
        if actual.evidence_identity() != frozen_document.evidence_identity():
            raise ValueError(
                "Document evidence changed after run creation; submit a new run"
            )
        if model is None:
            model = model_for(request.model_selection, store)
        model.progress_callback = lambda event: store.progress(run_id, current_call=event)
        identity = {"version": ENGINE_VERSION, "fingerprint": fingerprint}
        if request.model_selection is not None:
            identity["model_selection"] = request.model_selection.model_dump(mode="json")
        if request.document_model_selection is not None:
            from esg_contracts.model_providers import ModelSelection

            selection = request.document_model_selection
            document_model = model if selection == (request.model_selection or ModelSelection()) else model_for(selection, store)
            document_model.progress_callback = model.progress_callback
            identity["document_model_selection"] = selection.model_dump(mode="json")
        body = ExtractionBackbone(
            documents,
            model,
            artifacts,
            results,
            engine_identity=identity,
            document_model=document_model,
        ).run(
            run_id,
            request,
            repo.thaw(state["targets"]),
            document=frozen_document,
            cancel_check=lambda: store.is_cancelled(run_id),
            on_progress=lambda event: store.progress(run_id, progress=event),
            waiting_tasks=waiting_tasks,
        )
        status = (
            "completed"
            if body["summary"]["assessment"]["execution_state"] == "completed"
            else "partial"
        )
        store.progress(run_id, current_call=None, progress=saved_reading_progress(
            results.checkpoint(), store.get(run_id).get("progress"), body["summary"], "finished",
        ))
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
    except ExecutionYield as exc:
        summary = (results.status() or {}).get("summary") or {}
        store.progress(run_id, current_call=None, progress=saved_reading_progress(
            results.checkpoint(), store.get(run_id).get("progress"), summary, "window_yielded",
        ))
        return {**store.update(run_id, status="queued", worker_pid=None, worker_started=None,
                              execution_continues=True, summary=summary), "window": exc.window}
    except DocumentUnavailable as exc:
        return store.update(run_id, status="interrupted", worker_pid=None, worker_started=None,
            error=str(exc), summary=(ResultStore(run_id, artifacts).status() or {}).get("summary"))
    except ProviderInterruption as exc:
        checkpoint = results.checkpoint()
        summary = (results.status() or {}).get("summary") or {}
        store.progress(run_id, current_call=None, progress=saved_reading_progress(
            checkpoint, store.get(run_id).get("progress"), summary, exc.phase,
        ))
        return store.update(run_id, status="interrupted", worker_pid=None, worker_started=None,
            error=str(exc), summary=summary or None, provider_interruption={
                "provider": exc.provider, "category": exc.category, "phase": exc.phase,
            })
    except PipelineCancelled:
        return store.update(run_id, status="cancelled")
    except Exception as exc:
        store.update(
            run_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:1000]}"
        )
        raise
    finally:
        if document_model is not None and document_model is not model:
            document_model.close()
        if model is not None:
            model.close()
