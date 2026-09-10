from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from esg_v2.utils.hashing import sha256_file


CATALOG_SCHEMA = "esg-report-asset-catalog-v1"
CATALOG_FILENAME = "report-catalog.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportAssetCatalog:
    """Scans one dedicated PDF directory and derives OCR completion from manifests."""

    def __init__(self, root: Path, ocr_output_root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.ocr_output_root = ocr_output_root.expanduser().resolve()
        self.catalog_path = self.root / CATALOG_FILENAME
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            previous = self._load_raw()
            previous_by_name = {
                str(item.get("file_name")): item
                for item in previous.get("assets", [])
                if isinstance(item, dict)
            }
            ocr_by_sha = self._ocr_runs_by_sha()
            assets: list[dict[str, Any]] = []
            for path in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
                if not path.is_file() or path.suffix.casefold() != ".pdf":
                    continue
                stat = path.stat()
                cached = previous_by_name.get(path.name) or {}
                if (
                    cached.get("size_bytes") == stat.st_size
                    and cached.get("modified_ns") == stat.st_mtime_ns
                    and cached.get("sha256")
                ):
                    digest = str(cached["sha256"])
                else:
                    digest = sha256_file(path)
                runs = ocr_by_sha.get(digest, [])
                assets.append(
                    {
                        "asset_id": f"pdf-{digest[:24]}",
                        "file_name": path.name,
                        "relative_path": path.name,
                        "size_bytes": stat.st_size,
                        "modified_ns": stat.st_mtime_ns,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                        "sha256": digest,
                        "ocr_status": "processed" if runs else "unprocessed",
                        "ocr_run_count": len(runs),
                        "ocr_runs": runs,
                    }
                )
            payload = {
                "schema_version": CATALOG_SCHEMA,
                "root": str(self.root),
                "updated_at": _utc_now(),
                "asset_count": len(assets),
                "processed_count": sum(item["ocr_status"] == "processed" for item in assets),
                "unprocessed_count": sum(item["ocr_status"] == "unprocessed" for item in assets),
                "assets": assets,
            }
            self._write_atomic(payload)
            return payload

    def get(self, asset_id: str) -> dict[str, Any]:
        payload = self.refresh()
        asset = next(
            (item for item in payload["assets"] if item["asset_id"] == asset_id),
            None,
        )
        if asset is None:
            raise KeyError(asset_id)
        return asset

    def resolve(self, asset_id: str) -> Path:
        asset = self.get(asset_id)
        path = (self.root / str(asset["relative_path"])).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Report asset escapes the configured directory") from exc
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def add(self, file_name: str, source: BinaryIO) -> dict[str, Any]:
        safe_name = Path(file_name).name
        if not safe_name.casefold().endswith(".pdf"):
            raise ValueError("Only PDF report assets are accepted")
        destination = (self.root / safe_name).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Invalid report asset name") from exc
        if destination.exists():
            raise FileExistsError(safe_name)
        with self._lock:
            fd, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=".pdf", dir=self.root)
            try:
                with os.fdopen(fd, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(temp_name).replace(destination)
            except Exception:
                Path(temp_name).unlink(missing_ok=True)
                raise
        return self.get(f"pdf-{sha256_file(destination)[:24]}")

    def delete(self, asset_id: str) -> dict[str, Any]:
        asset = self.get(asset_id)
        path = self.resolve(asset_id)
        path.unlink()
        payload = self.refresh()
        return {
            "asset_id": asset_id,
            "file_name": asset["file_name"],
            "deleted": True,
            "remaining_asset_count": payload["asset_count"],
        }

    def _ocr_runs_by_sha(self) -> dict[str, list[dict[str, Any]]]:
        output: dict[str, list[dict[str, Any]]] = {}
        if not self.ocr_output_root.is_dir():
            return output
        for path in self.ocr_output_root.glob("*/manifest.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            source = manifest.get("source")
            source = source if isinstance(source, dict) else {}
            digest = str(source.get("sha256") or "")
            if len(digest) != 64:
                continue
            output.setdefault(digest, []).append(
                {
                    "run_id": str(manifest.get("run_id") or path.parent.name),
                    "provider": manifest.get("ocr_provider"),
                    "page_count": int(manifest.get("page_count") or 0),
                    "completed": True,
                }
            )
        for runs in output.values():
            runs.sort(key=lambda item: str(item["run_id"]), reverse=True)
        return output

    def _load_raw(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return {"assets": []}
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"assets": []}
        return payload if isinstance(payload, dict) else {"assets": []}

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        temporary = self.catalog_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.catalog_path)
