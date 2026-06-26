from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_esg.ai import QiniuModelRegistry
from extract_esg.workflows import ReportProcessingWorkflow


def inspect_pdf(path: str) -> int:
    state = ReportProcessingWorkflow().prepare_local_evidence(path)
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


def list_cached_models(cache: str | None) -> int:
    registry = QiniuModelRegistry(cache_path=Path(cache) if cache else None)
    models = registry.load_cache()
    print(json.dumps([model.model_dump(mode="json") for model in models], ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract-esg")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect-pdf")
    inspect.add_argument("path")

    cached = sub.add_parser("list-cached-models")
    cached.add_argument("--cache", default=None)

    args = parser.parse_args(argv)
    if args.command == "inspect-pdf":
        return inspect_pdf(args.path)
    if args.command == "list-cached-models":
        return list_cached_models(args.cache)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

