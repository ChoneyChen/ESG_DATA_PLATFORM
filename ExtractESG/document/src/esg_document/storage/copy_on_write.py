"""Copy independent revision assets, using filesystem cloning when available.

Clones have separate file identities and share unchanged storage extents. Never
use hard links: a later visual rebuild must not mutate its parent revision.
"""

from __future__ import annotations

import ctypes
from functools import lru_cache
import os
from pathlib import Path
import shutil
import sys


@lru_cache(maxsize=1)
def _darwin_clonefile():
    try:
        function = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return None
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
    function.restype = ctypes.c_int
    return function


def _clone_file(source, destination):
    if sys.platform == "darwin":
        function = _darwin_clonefile()
        if function is None:
            raise OSError("clonefile is unavailable")
        if function(os.fsencode(source), os.fsencode(destination), 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), os.fspath(destination))
    elif sys.platform.startswith("linux"):
        import fcntl

        # Linux's FICLONE ioctl creates independent copy-on-write extents.
        with open(source, "rb") as original, open(destination, "xb") as copied:
            fcntl.ioctl(copied.fileno(), 0x40049409, original.fileno())
    else:
        raise OSError("Filesystem cloning is unavailable on this platform")


def copy_on_write(source, destination, *, follow_symlinks=True):
    """A copy2-compatible copy function with a transparent cloning fallback."""
    source_path = Path(source)
    destination_path = Path(destination)
    if destination_path.is_dir():
        destination_path /= source_path.name
    if source_path.is_file() and (follow_symlinks or not source_path.is_symlink()):
        try:
            _clone_file(source_path, destination_path)
        except OSError:
            # An unsupported filesystem/ioctl may leave an empty destination;
            # copy2 replaces its contents while preserving normal copy errors.
            pass
        else:
            shutil.copystat(
                source_path, destination_path, follow_symlinks=follow_symlinks
            )
            return os.fspath(destination_path)
    return shutil.copy2(source_path, destination_path, follow_symlinks=follow_symlinks)
