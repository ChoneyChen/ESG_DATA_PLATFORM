from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import json


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    logical_tables: list[dict[str, Any]] = field(default_factory=list)
    spreads: list[dict[str, Any]] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.manifest["run_id"]

    @property
    def document_id(self) -> str:
        return (
            self.manifest.get("document_id") or self.document["metadata"]["document_id"]
        )

    @property
    def ir_revision(self) -> int:
        return int(self.manifest.get("ir_revision", 1))


from .contracts import DocumentIR


class DocumentIrLoader:
    supported_schema_version = DocumentIR.model_fields["schema_version"].default

    def load(self, root: Path, *, require_ready: bool = True) -> LoadedDocumentIr:
        root = root.resolve()
        manifest = read_json(root / "manifest.json")
        schema_version = str(manifest.get("schema_version", ""))
        if schema_version != self.supported_schema_version:
            raise ValueError(f"unsupported Document IR schema: {schema_version}")
        policy = manifest.get("evidence_policy", {})
        limited = (
            manifest.get("can_build_limited_evidence")
            and policy.get("mode") == "limited"
        )
        if require_ready and not (manifest.get("can_build_evidence", False) or limited):
            raise ValueError(
                f"Document IR {manifest.get('run_id')} is not evidence-ready: "
                f"{manifest.get('readiness', 'unknown')}"
            )

        entrypoints = manifest.get("entrypoints", {})
        document = read_json(self._resolve(root, entrypoints["canonical_document"]))
        page_index = read_json(self._resolve(root, entrypoints["pages"]))
        table_index = read_json(self._resolve(root, entrypoints["tables"]))

        pages = [
            read_json(self._resolve(root, item["path"]))
            for item in page_index.get("pages", [])
        ]
        tables = [
            read_json(self._resolve(root, item["path"]))
            for item in table_index.get("tables", [])
        ]
        figures = []
        figure_entrypoint = entrypoints.get("figures")
        if figure_entrypoint:
            figure_index = read_json(self._resolve(root, figure_entrypoint))
            figures = [
                read_json(self._resolve(root, item["path"]))
                for item in figure_index.get("figures", [])
                if item.get("path")
            ]

        def optional_records(key):
            entry = entrypoints.get(key)
            if not entry:
                return list(document.get(key) or [])
            index = read_json(self._resolve(root, entry))
            return [
                read_json(self._resolve(root, item["path"]))
                for item in index.get(key, [])
                if item.get("path")
            ]

        logical_tables = optional_records("logical_tables")
        spreads = optional_records("spreads")
        artifact_index_path = root / "artifacts" / "index.json"
        artifacts = (
            read_json(artifact_index_path).get("artifacts", [])
            if artifact_index_path.is_file()
            else []
        )
        sections = document.get("sections", [])
        availability = {
            a["object_id"]: a
            for a in policy.get("object_availability", [])
            if a.get("object_id")
        }
        if limited:
            excluded_pages = set(policy.get("excluded_page_indices", []))
            excluded_targets = set(policy.get("excluded_target_ids", []))

            def source_allowed(object_id):
                capabilities = availability.get(object_id, {}).get("capabilities", {})
                reference = capabilities.get("source_reference", True)
                return object_id not in excluded_targets and reference

            filtered_pages = []
            for payload in pages:
                page = payload["page"]
                if page["page_index"] in excluded_pages or not source_allowed(
                    page["page_id"]
                ):
                    continue
                payload = {
                    **payload,
                    "page": dict(page),
                    "blocks": [
                        b
                        for b in payload.get("blocks", [])
                        if source_allowed(b["block_id"])
                        and availability.get(b["block_id"], {})
                        .get("capabilities", {})
                        .get("text", True)
                    ],
                }
                page_capabilities = availability.get(page["page_id"], {}).get(
                    "capabilities", {}
                )
                if not page_capabilities.get("text", True):
                    payload["page"]["text"] = ""
                if not page_capabilities.get("page_image", True):
                    payload["page"]["page_image_path"] = None
                filtered_pages.append(payload)
            pages = filtered_pages
            tables = [
                {
                    **t,
                    "cells": [
                        c for c in t.get("cells", []) if source_allowed(c["cell_id"])
                    ],
                }
                for t in tables
                if t.get("page_index") not in excluded_pages
                and source_allowed(t["table_id"])
            ]
            figures = [
                f
                for f in figures
                if f.get("page_index") not in excluded_pages
                and source_allowed(f["figure_id"])
            ]
            artifacts = [
                a for a in artifacts if a.get("page_index") not in excluded_pages
            ]
            kept_tables = {t["table_id"] for t in tables}
            logical_tables = [
                t
                for t in logical_tables
                if source_allowed(t["logical_table_id"])
                and set(t.get("source_table_ids", [])) <= kept_tables
                and not excluded_pages.intersection(t.get("page_indices", []))
            ]
            spreads = [
                s
                for s in spreads
                if source_allowed(s["spread_id"])
                and not excluded_pages.intersection(s.get("page_indices", []))
                and not excluded_targets.intersection(s.get("member_entity_ids", []))
            ]
        tables = [
            {
                **t,
                "evidence_capabilities": availability.get(t["table_id"], {}).get(
                    "capabilities", {}
                ),
            }
            for t in tables
        ]
        figures = [
            {
                **f,
                "evidence_capabilities": availability.get(f["figure_id"], {}).get(
                    "capabilities", {}
                ),
            }
            for f in figures
        ]
        return LoadedDocumentIr(
            root=root,
            manifest=manifest,
            document=document,
            sections_by_id={item["section_id"]: item for item in sections},
            pages=pages,
            tables=tables,
            figures=figures,
            artifacts=artifacts,
            logical_tables=logical_tables,
            spreads=spreads,
        )

    @staticmethod
    def _resolve(root: Path, relative: str) -> Path:
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError(f"Document IR entrypoint escapes package root: {relative}")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path


def iter_page_records(
    ir: LoadedDocumentIr,
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for payload in ir.pages:
        yield payload["page"], payload.get("blocks", [])
