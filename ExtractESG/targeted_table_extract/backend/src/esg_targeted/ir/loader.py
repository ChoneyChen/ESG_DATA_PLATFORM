from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from esg_targeted.io import read_json


@dataclass(frozen=True)
class LoadedDocumentIr:
    root: Path
    manifest: dict[str, Any]
    document: dict[str, Any]
    sections_by_id: dict[str, dict[str, Any]]
    pages: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    figures: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]

    @property
    def run_id(self) -> str:
        return self.manifest["run_id"]

    @property
    def document_id(self) -> str:
        return self.manifest.get("document_id") or self.document["metadata"]["document_id"]

    @property
    def ir_revision(self) -> int:
        return int(self.manifest.get("ir_revision", 1))


class DocumentIrLoader:
    supported_schema_prefix = "document-ir-v0."

    def load(self, root: Path, *, require_ready: bool = True) -> LoadedDocumentIr:
        root = root.resolve()
        manifest = read_json(root / "manifest.json")
        schema_version = str(manifest.get("schema_version", ""))
        if not schema_version.startswith(self.supported_schema_prefix):
            raise ValueError(f"unsupported Document IR schema: {schema_version}")
        policy = manifest.get("evidence_policy", {})
        limited = manifest.get("can_build_limited_evidence") and policy.get("mode") == "limited"
        if require_ready and not (manifest.get("can_build_evidence", False) or limited):
            raise ValueError(
                f"Document IR {manifest.get('run_id')} is not evidence-ready: "
                f"{manifest.get('readiness', 'unknown')}"
            )

        entrypoints = manifest.get("entrypoints", {})
        document = read_json(self._resolve(root, entrypoints["canonical_document"]))
        page_index = read_json(self._resolve(root, entrypoints["pages"]))
        table_index = read_json(self._resolve(root, entrypoints["tables"]))

        pages = [read_json(self._resolve(root, item["path"])) for item in page_index.get("pages", [])]
        tables = [read_json(self._resolve(root, item["path"])) for item in table_index.get("tables", [])]
        figures = []
        figure_entrypoint = entrypoints.get("figures")
        if figure_entrypoint:
            figure_index = read_json(self._resolve(root, figure_entrypoint))
            figures = [
                read_json(self._resolve(root, item["path"]))
                for item in figure_index.get("figures", [])
                if item.get("path")
            ]
        artifact_index_path = root / "artifacts" / "index.json"
        artifacts = (
            read_json(artifact_index_path).get("artifacts", [])
            if artifact_index_path.is_file()
            else []
        )
        sections = document.get("sections", [])
        if limited:
            excluded = set(policy.get("excluded_page_indices", []))
            pages = [p for p in pages if p["page"]["page_index"] not in excluded]
            tables = [t for t in tables if t.get("table", t).get("page_index") not in excluded]
            figures = [f for f in figures if f.get("figure", f).get("page_index") not in excluded]
            artifacts = [a for a in artifacts if a.get("page_index") not in excluded]
        return LoadedDocumentIr(
            root=root,
            manifest=manifest,
            document=document,
            sections_by_id={item["section_id"]: item for item in sections},
            pages=pages,
            tables=tables,
            figures=figures,
            artifacts=artifacts,
        )

    @staticmethod
    def _resolve(root: Path, relative: str) -> Path:
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError(f"Document IR entrypoint escapes package root: {relative}")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path


def iter_page_records(ir: LoadedDocumentIr) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for payload in ir.pages:
        yield payload["page"], payload.get("blocks", [])
