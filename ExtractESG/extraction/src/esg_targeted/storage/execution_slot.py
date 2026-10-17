"""One inherited OS execution slot shared with the local pipeline queue."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class ExecutionSlotBusy(RuntimeError):
    pass


def execution_slot_path() -> Path:
    if os.getenv("ESG_EXECUTION_SLOT_PATH"):
        return Path(os.environ["ESG_EXECUTION_SLOT_PATH"]).expanduser().resolve()
    if os.getenv("ESG_PLATFORM_RUNTIME_ROOT"):
        return Path(os.environ["ESG_PLATFORM_RUNTIME_ROOT"]).expanduser().resolve() / "model-execution.lock"
    from esg_targeted.config import settings
    return settings.project_root.parents[2] / ".local/platform-runtime/model-execution.lock"


def inherited_slot_is_valid() -> bool:
    """Check that worker admission carries the real queue-owned descriptor."""
    inherited = os.getenv("ESG_EXECUTION_SLOT_FD")
    if not inherited:
        return False
    try:
        held, expected = os.fstat(int(inherited)), execution_slot_path().stat()
        return (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino)
    except (ValueError, OSError):
        return False


class ExecutionSlot:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.fd: int | None = None

    def acquire(self) -> "ExecutionSlot":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        inherited = os.getenv("ESG_EXECUTION_SLOT_FD")
        if inherited:
            try:
                fd = int(inherited)
                held, expected = os.fstat(fd), self.path.stat()
                if (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino):
                    self.fd = os.dup(fd)
                    return self
            except (ValueError, OSError):
                pass
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.close()
            raise ExecutionSlotBusy("Model execution slot is occupied by another OCR/IR/extraction worker") from exc
        return self

    def close(self) -> None:
        if self.fd is not None:
            # Never LOCK_UN an inherited open-file description: the worker
            # must retain ownership after its scheduling parent disappears.
            os.close(self.fd)
            self.fd = None
