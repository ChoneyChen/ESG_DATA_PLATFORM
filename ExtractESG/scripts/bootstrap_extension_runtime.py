"""Create an empty first-run extension projection without hiding corruption.

Compiled definitions and approval records are projected by PlatESG after their
authoritative database transaction. This initializer never activates a draft.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def initialize(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.projection.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'registry.json'
        if path.exists():
            value = json.loads(path.read_text())
            if value.get('schema_version') != 'project-extension-registry-1.0' or not isinstance(value.get('registrations'), list):
                raise ValueError('Extension runtime registry is invalid; recover its authoritative projection')
            return {'created': False, 'registration_count': len(value['registrations'])}
        if any((root / 'releases').rglob('release.json')):
            raise ValueError('Extension releases exist without a registry; recover the projection rather than resetting it')
        with NamedTemporaryFile(dir=root, prefix='.registry-', suffix='.json', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(b'{"schema_version":"project-extension-registry-1.0","registrations":[]}\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return {'created': True, 'registration_count': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(initialize(args.root), ensure_ascii=False))
