from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_esg.ai import CloudModelRouter, QiniuModelRegistry
from extract_esg.config import Settings
from extract_esg.persistence import SqliteStore
from extract_esg.web.app import serve
from extract_esg.workflows import ReportProcessingWorkflow


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env(getattr(args, "env_file", None))


def inspect_pdf(args: argparse.Namespace) -> int:
    state = ReportProcessingWorkflow().prepare_local_evidence(args.path, report_id=args.report_id)
    assert state.document_ir is not None
    payload = {
        "report_id": state.report_id,
        "sha256": state.document_ir.sha256,
        "pages": len(state.document_ir.pages),
        "packets": len(state.packets),
        "page_flags": [page.quality_flags for page in state.document_ir.pages],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def init_db(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db_path = Path(args.db or settings.sqlite_db_path)
    SqliteStore(db_path).init_schema()
    print(json.dumps({"db": str(db_path), "status": "initialized"}, ensure_ascii=False, indent=2))
    return 0


def run_local(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db_path = Path(args.db or settings.sqlite_db_path)
    store = SqliteStore(db_path)
    state = ReportProcessingWorkflow().run_local_pipeline(args.path, report_id=args.report_id, store=store)
    payload = {
        "db": str(db_path),
        "report_id": state.report_id,
        "status": state.status,
        "pages": len(state.document_ir.pages) if state.document_ir else 0,
        "packets": len(state.packets),
        "disclosures": len(state.disclosures),
        "note": "local baseline disclosures are candidates only; cloud extraction is not invoked by this command",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def list_reports(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db_path = Path(args.db or settings.sqlite_db_path)
    print(json.dumps(SqliteStore(db_path).list_reports(), ensure_ascii=False, indent=2))
    return 0


def list_cached_models(args: argparse.Namespace) -> int:
    registry = QiniuModelRegistry(cache_path=Path(args.cache) if args.cache else None)
    models = registry.load_cache()
    print(json.dumps([model.model_dump(mode="json") for model in models], ensure_ascii=False, indent=2))
    return 0


def refresh_models(args: argparse.Namespace) -> int:
    settings = _settings(args)
    registry = QiniuModelRegistry(cache_path=Path(args.cache) if args.cache else settings.model_cache)
    models = registry.refresh()
    payload = {"cache": str(registry.cache_path), "model_count": len(models)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def select_model(args: argparse.Namespace) -> int:
    settings = _settings(args)
    registry = QiniuModelRegistry(cache_path=Path(args.cache) if args.cache else settings.model_cache)
    models = registry.load_cache()
    if not models:
        raise SystemExit("No cached models. Run `extract-esg refresh-models` with QINIU_API_KEY first.")
    choice = CloudModelRouter(registry, settings).choose(args.task)
    print(json.dumps(choice.model_dump() if hasattr(choice, "model_dump") else {
        "task_kind": choice.task_kind,
        "model": choice.model.model_dump(mode="json") if choice.model else None,
        "explicit_model_id": choice.explicit_model_id,
        "reason": choice.reason,
    }, ensure_ascii=False, indent=2))
    return 0


def serve_web(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db_path = Path(args.db or settings.sqlite_db_path)
    serve(db_path=db_path, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract-esg")
    parser.add_argument("--env-file", default=None, help="Optional .env path. Defaults to ./.env.")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect-pdf")
    inspect.add_argument("path")
    inspect.add_argument("--report-id", default=None)

    db = sub.add_parser("init-db")
    db.add_argument("--db", default=None)

    local = sub.add_parser("run-local")
    local.add_argument("path")
    local.add_argument("--report-id", default=None)
    local.add_argument("--db", default=None)

    reports = sub.add_parser("list-reports")
    reports.add_argument("--db", default=None)

    cached = sub.add_parser("list-cached-models")
    cached.add_argument("--cache", default=None)

    refresh = sub.add_parser("refresh-models")
    refresh.add_argument("--cache", default=None)

    select = sub.add_parser("select-model")
    select.add_argument(
        "task",
        choices=[
            "page_classification",
            "vision_ocr",
            "structured_extract",
            "long_context_understanding",
            "mapping_review",
            "verification",
        ],
    )
    select.add_argument("--cache", default=None)

    web = sub.add_parser("serve")
    web.add_argument("--db", default=None)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    if args.command == "inspect-pdf":
        return inspect_pdf(args)
    if args.command == "init-db":
        return init_db(args)
    if args.command == "run-local":
        return run_local(args)
    if args.command == "list-reports":
        return list_reports(args)
    if args.command == "list-cached-models":
        return list_cached_models(args)
    if args.command == "refresh-models":
        return refresh_models(args)
    if args.command == "select-model":
        return select_model(args)
    if args.command == "serve":
        return serve_web(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
