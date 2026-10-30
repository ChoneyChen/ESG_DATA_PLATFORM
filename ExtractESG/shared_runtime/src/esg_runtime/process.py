"""Process identity checks never treat an observation failure as termination."""

import os, subprocess


def identity(pid):
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=1,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def observed_alive(pid, started):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    current = identity(pid)
    if started and current:
        return current == started
    return None
