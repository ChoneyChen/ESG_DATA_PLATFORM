"""Retire compiled runtime versions after successful publication, recoverably."""
from pathlib import Path
import re


def version_key(value):
    return tuple(map(int, value.split("."))) if re.fullmatch(r"\d+\.\d+\.\d+", value) else None


def retire_older_versions(output_path, compiled, validate):
    path = Path(output_path).resolve()
    version, package_id = compiled.manifest.package_version, compiled.manifest.package_id
    if path.name != "package.json" or path.parent.name != version or path.parent.parent.name != package_id:
        return []  # Arbitrary export paths are never a deletion target.
    current = version_key(version)
    if current is None:
        return []
    dist = path.parents[2]
    retired = []
    for folder in sorted(path.parent.parent.iterdir()):
        key = version_key(folder.name)
        if folder.is_symlink() or not folder.is_dir() or key is None or key >= current:
            continue
        source = folder / "package.json"
        if not source.is_file():
            continue
        previous = validate(source)
        if previous.manifest.package_id != package_id or previous.manifest.package_version != folder.name:
            continue
        archive = dist / ".retired" / package_id / folder.name
        if archive.exists():
            continue  # Never overwrite the only historical copy.
        archive.parent.mkdir(parents=True, exist_ok=True)
        folder.rename(archive)
        retired.append(str(archive))
    return retired
