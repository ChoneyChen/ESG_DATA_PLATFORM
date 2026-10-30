"""Artifacts are a producer-owned store addressed by run and artifact IDs."""

import json
from pathlib import Path


class FileArtifactPort:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, run_id, name):
        if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
            raise ValueError("Invalid run identity")
        path = (self.root / run_id / name).resolve()
        if not path.is_relative_to(self.root / run_id):
            raise ValueError("Artifact escapes its producer store")
        return path

    def write(self, run_id, name, value):
        path = self.path(run_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (
            json.dumps(
                value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n"
        )
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(data, encoding="utf-8")
        temporary.replace(path)

    def read(self, run_id, name):
        return json.loads(self.path(run_id, name).read_text(encoding="utf-8"))
