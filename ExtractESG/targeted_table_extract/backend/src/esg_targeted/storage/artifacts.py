from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any

from esg_targeted.io import read_json, write_json, write_jsonl


class ArtifactStore:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()

    def job_dir(self, job_id: str) -> Path:
        path = self._job_path(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def existing_job_ids(self) -> list[str]:
        """Return result-bundle directory names without creating any directories."""

        if not self.output_root.is_dir():
            return []
        return sorted(
            path.name
            for path in self.output_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    def existing_job_dir(self, job_id: str) -> Path:
        path = self._job_path(job_id)
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    def delete_job(self, job_id: str) -> dict[str, int | bool]:
        path = self._job_path(job_id)
        if not path.exists():
            return {"directory_existed": False, "file_count": 0, "size_bytes": 0}
        file_count = 0
        size_bytes = 0
        for artifact in path.rglob("*"):
            if artifact.is_file():
                file_count += 1
                size_bytes += artifact.stat().st_size
        shutil.rmtree(path)
        return {
            "directory_existed": True,
            "file_count": file_count,
            "size_bytes": size_bytes,
        }

    def _job_path(self, job_id: str) -> Path:
        path = (self.output_root / job_id).resolve()
        if path == self.output_root or self.output_root not in path.parents:
            raise ValueError("job path escapes output root")
        return path

    def write_json(self, job_id: str, relative: str, payload: Any) -> Path:
        path = self.resolve(job_id, relative, must_exist=False)
        write_json(path, payload)
        return path

    def write_jsonl(self, job_id: str, relative: str, rows) -> Path:
        path = self.resolve(job_id, relative, must_exist=False)
        write_jsonl(path, rows)
        return path

    def resolve(self, job_id: str, relative: str, *, must_exist: bool = True) -> Path:
        root = self.job_dir(job_id)
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ValueError("artifact path escapes job directory")
        if must_exist and not path.is_file():
            raise FileNotFoundError(path)
        return path

    def list(self, job_id: str) -> list[dict[str, Any]]:
        root = self.job_dir(job_id)
        records = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            mime, _ = mimetypes.guess_type(path.name)
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "media_type": mime or "application/octet-stream",
                    "modified_at": path.stat().st_mtime,
                }
            )
        return records

    def read_json(self, job_id: str, relative: str) -> Any:
        return read_json(self.resolve(job_id, relative))

    def exists(self, job_id: str, relative: str) -> bool:
        try:
            self.resolve(job_id, relative)
            return True
        except FileNotFoundError:
            return False
