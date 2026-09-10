from __future__ import annotations

import json
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from esg_v2.utils.hashing import sha256_file


RUN_ID_PATTERN = re.compile(r"^(ocr|ir)-\d{8}T\d{6}Z-[0-9a-f]{12}$")
PACKAGE_DIR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def new_run_id(kind: str) -> str:
    if kind not in {"ocr", "ir"}:
        raise ValueError(f"Unsupported run kind: {kind}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}-{stamp}-{uuid.uuid4().hex[:12]}"


def require_run_id(value: str, kind: str) -> str:
    match = RUN_ID_PATTERN.fullmatch(value)
    if not match or match.group(1) != kind:
        raise ValueError(
            f"New {kind} run_id must match {kind}-YYYYMMDDTHHMMSSZ-<12 lowercase hex characters>"
        )
    return value


def require_package_dir_name(value: str) -> str:
    if not PACKAGE_DIR_NAME_PATTERN.fullmatch(value):
        raise ValueError("Package directory name contains unsafe or unsupported characters")
    return value


def package_dir(root: Path, name: str) -> Path:
    return root / require_package_dir_name(name)


def create_package_root(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    return path


def page_stem(page_index: int) -> str:
    if page_index < 0:
        raise ValueError("page_index must be non-negative")
    return f"page-{page_index + 1:04d}"


def page_object_id(kind: str, page_index: int, sequence: int) -> str:
    if kind not in {"block", "layout", "table", "figure"}:
        raise ValueError(f"Unsupported page object kind: {kind}")
    if page_index < 0 or sequence < 1:
        raise ValueError("page_index must be non-negative and sequence must be positive")
    return f"{kind}-p{page_index + 1:04d}-{sequence:04d}"


def cell_id(page_index: int, table_sequence: int, row_index: int, column_index: int) -> str:
    if min(page_index, row_index, column_index) < 0 or table_sequence < 1:
        raise ValueError("Cell coordinates must be non-negative and table_sequence must be positive")
    return (
        f"cell-p{page_index + 1:04d}-t{table_sequence:04d}"
        f"-r{row_index + 1:04d}-c{column_index + 1:04d}"
    )


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: Iterable[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def read_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def resolve_package_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Package path escapes root: {relative}") from exc
    return candidate


def write_file_index(root: Path, path: Path) -> Path:
    rows = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or item == path or item.name == ".DS_Store":
            continue
        media_type = mimetypes.guess_type(item.name)[0] or "application/octet-stream"
        rows.append(
            {
                "path": relative_path(root, item),
                "size_bytes": item.stat().st_size,
                "media_type": media_type,
                "sha256": sha256_file(item),
            }
        )
    return write_json(path, {"algorithm": "sha256", "file_count": len(rows), "files": rows})


@dataclass(frozen=True)
class OcrPackageLayout:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def source_request(self) -> Path:
        return self.root / "source" / "request.json"

    @property
    def source_preflight(self) -> Path:
        return self.root / "source" / "preflight.json"

    @property
    def provider_input(self) -> Path:
        return self.root / "source" / "provider-input.pdf"

    @property
    def submit_response(self) -> Path:
        return self.root / "provider" / "submit-response.json"

    @property
    def poll_events(self) -> Path:
        return self.root / "provider" / "poll-events.jsonl"

    @property
    def provider_result(self) -> Path:
        return self.root / "provider" / "result.jsonl"

    @property
    def artifact_index(self) -> Path:
        return self.root / "artifacts" / "index.json"

    @property
    def file_index(self) -> Path:
        return self.root / "integrity" / "files.json"

    def page_observation(self, page_index: int) -> Path:
        return self.root / "observations" / "pages" / f"{page_stem(page_index)}.json"

    def page_markdown(self, page_index: int) -> Path:
        return self.root / "content" / "pages" / f"{page_stem(page_index)}.md"

    def page_image_dir(self, page_index: int) -> Path:
        return self.root / "artifacts" / "images" / page_stem(page_index)

    def page_layout_image(self, page_index: int, suffix: str = ".jpg") -> Path:
        return self.root / "artifacts" / "layouts" / f"{page_stem(page_index)}{suffix}"


@dataclass(frozen=True)
class DocumentIrPackageLayout:
    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def canonical_document(self) -> Path:
        return self.root / "canonical" / "document.json"

    @property
    def page_index(self) -> Path:
        return self.root / "canonical" / "pages" / "index.json"

    @property
    def table_index(self) -> Path:
        return self.root / "canonical" / "tables" / "index.json"

    @property
    def logical_table_index(self) -> Path:
        return self.root / "canonical" / "logical-tables" / "index.json"

    @property
    def figure_index(self) -> Path:
        return self.root / "canonical" / "figures" / "index.json"

    @property
    def spread_index(self) -> Path:
        return self.root / "canonical" / "spreads" / "index.json"

    @property
    def structure_edges(self) -> Path:
        return self.root / "canonical" / "relations" / "structure-edges.jsonl"

    @property
    def coordinate_systems(self) -> Path:
        return self.root / "canonical" / "coordinates" / "systems.json"

    @property
    def local_forensics(self) -> Path:
        return self.root / "observations" / "local-pdf" / "forensics.json"

    @property
    def retired_entities(self) -> Path:
        return self.root / "observations" / "retired-entities.jsonl"

    @property
    def artifact_index(self) -> Path:
        return self.root / "artifacts" / "index.json"

    @property
    def quality_report(self) -> Path:
        return self.root / "quality" / "quality-report.json"

    @property
    def validation_report(self) -> Path:
        return self.root / "quality" / "validation-report.json"

    @property
    def review_index(self) -> Path:
        return self.root / "review" / "index.json"

    @property
    def file_index(self) -> Path:
        return self.root / "integrity" / "files.json"

    @property
    def snapshot(self) -> Path:
        return self.root / "exports" / "document-ir.snapshot.json"

    def page(self, page_index: int) -> Path:
        return self.root / "canonical" / "pages" / f"{page_stem(page_index)}.json"

    def paddle_layout(self, page_index: int) -> Path:
        return self.root / "observations" / "paddle-layout" / f"{page_stem(page_index)}.json"

    def table(self, table_id: str) -> Path:
        return self.root / "canonical" / "tables" / f"{table_id}.json"

    def logical_table(self, logical_table_id: str) -> Path:
        return self.root / "canonical" / "logical-tables" / f"{logical_table_id}.json"

    def figure(self, figure_id: str) -> Path:
        return self.root / "canonical" / "figures" / f"{figure_id}.json"

    def spread(self, spread_id: str) -> Path:
        return self.root / "canonical" / "spreads" / f"{spread_id}.json"

    def page_image(self, page_index: int) -> Path:
        return self.root / "artifacts" / "page-images" / f"{page_stem(page_index)}.png"

    def crop(self, kind: str, entity_id: str) -> Path:
        if kind not in {"tables", "figures"}:
            raise ValueError(f"Unsupported crop kind: {kind}")
        return self.root / "artifacts" / "crops" / kind / f"{entity_id}.png"

    def spread_image(self, spread_id: str) -> Path:
        return self.root / "artifacts" / "spreads" / f"{spread_id}.png"

    def review_task(self, task_id: str) -> Path:
        return self.root / "review" / "tasks" / f"{task_id}.json"

    def candidate(self, candidate_id: str) -> Path:
        return self.root / "review" / "candidates" / f"{candidate_id}.json"

    def review_collection(self, relative: str) -> Path:
        return self.root / "review" / relative
