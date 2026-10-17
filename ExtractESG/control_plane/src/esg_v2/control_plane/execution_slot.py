"""The pipeline's inherited flock, shared with standalone targeted workers."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class ExecutionSlotBusy(RuntimeError):
    pass


class ExecutionSlot:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.fd: int | None = None

    def acquire(self) -> "ExecutionSlot":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.close()
            raise ExecutionSlotBusy("Shared model execution slot is occupied") from exc
        return self

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)  # Closing the parent's copy does not unlock the child's copy.
            self.fd = None


def inherited_slot_is_valid() -> bool:
    """Check descriptor identity; an environment flag alone is not ownership."""
    try:
        fd = int(os.environ["ESG_EXECUTION_SLOT_FD"])
        expected = Path(os.environ["ESG_EXECUTION_SLOT_PATH"]).resolve()
        held, actual = os.fstat(fd), expected.stat()
        return held.st_dev == actual.st_dev and held.st_ino == actual.st_ino
    except (KeyError, ValueError, OSError):
        return False
