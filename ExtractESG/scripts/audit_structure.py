#!/usr/bin/env python3
"""Inventory first-party files and enforce module boundaries without reading private data."""

import argparse, ast, hashlib, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/verification/project-structure.json"
EXCLUDED = {
    "CrawlESG",
    ".claude",
    ".local",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
}


def paths():
    result = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    for name in sorted(set(result.stdout.decode().split("\0"))):
        path = ROOT / name
        if (
            not name
            or any(part in EXCLUDED for part in Path(name).parts)
            or path == OUTPUT
            or path.name.startswith(".env")
            or not path.exists()
        ):
            continue
        yield path


def owner(path):
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if parts[0] == "ExtractESG":
        return "/".join(parts[:2]) if len(parts) > 2 else "ExtractESG"
    if parts[0] == "PlatESG":
        return "/".join(parts[:2]) if len(parts) > 2 else "PlatESG"
    return parts[0] if len(parts) > 1 else "root"


def role(path):
    parts = path.relative_to(ROOT).parts
    if "migrations" in parts:
        return "immutable_schema_history"
    if "catalog" in parts and parts[0] == "database":
        return "frozen_product_contract"
    if parts[0] == "ClauseESG" and "clauses" in parts:
        return (
            "clause_source"
            if path.name == "clause.json"
            else "clause_index_or_selection"
        )
    if "fixtures" in parts or "tests" in parts or ".test." in path.name:
        return "isolated_verification"
    if path.suffix == ".md":
        return "documentation"
    if path.suffix in {".py", ".mjs", ".ts", ".tsx", ".sh", ".css", ".html", ".swift"}:
        return "implementation"
    if path.suffix == ".json" and "schemas" in parts:
        return "generated_schema"
    return "configuration_or_asset"


def inspect():
    files = list(paths())
    modules = set()
    roots = set()
    errors = []
    records = []
    for path in files:
        if path.is_symlink():
            errors.append(
                {"path": str(path.relative_to(ROOT)), "issue": "source alias remains"}
            )
            continue
        if path.suffix == ".py" and "src" in path.parts:
            bits = list(path.parts[path.parts.index("src") + 1 :])
            bits[-1] = path.stem
            if bits[-1] == "__init__":
                bits.pop()
            key = ".".join(bits)
            modules.add(key)
            roots.update(".".join(bits[:i]) for i in range(1, len(bits) + 1))
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        body = path.read_bytes()
        imports = []
        if path.suffix == ".py":
            try:
                tree = ast.parse(body, filename=rel)
            except SyntaxError:
                errors.append({"path": rel, "issue": "invalid Python syntax"})
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif (
                    isinstance(node, ast.ImportFrom) and node.module and not node.level
                ):
                    imports.append(node.module)
            for module in imports:
                if module.startswith("esg_") and module not in roots:
                    errors.append(
                        {
                            "path": rel,
                            "issue": "unresolved local import",
                            "module": module,
                        }
                    )
                if "/contracts/src/" in rel and module.startswith(
                    ("esg_document", "esg_extraction", "esg_pipeline")
                ):
                    errors.append(
                        {
                            "path": rel,
                            "issue": "contract imports executor",
                            "module": module,
                        }
                    )
                if "/extraction/src/" in rel and module.startswith(
                    ("esg_document", "esg_pipeline")
                ):
                    errors.append(
                        {"path": rel, "issue": "reader bypasses port", "module": module}
                    )
                if "/document/src/" in rel and module.startswith(
                    ("esg_clauses", "esg_extraction")
                ):
                    errors.append(
                        {
                            "path": rel,
                            "issue": "document imports clause or reader implementation",
                            "module": module,
                        }
                    )
        if path.suffix == ".json":
            try:
                json.loads(body)
            except (ValueError, UnicodeError):
                errors.append({"path": rel, "issue": "invalid JSON"})
        records.append(
            {
                "path": rel,
                "owner": owner(path),
                "role": role(path),
                "bytes": len(body),
                "lines": body.count(b"\n"),
                "sha256": hashlib.sha256(body).hexdigest(),
                **({"imports": sorted(set(imports))} if imports else {}),
            }
        )
    counts = {}
    for record in records:
        counts[record["owner"]] = counts.get(record["owner"], 0) + 1
    return {
        "schema_version": "project-structure-audit-1.0",
        "scope": "first_party_except_crawl_no_private_data",
        "files": records,
        "counts": counts,
        "errors": errors,
        "note": "Structural validation and dependency audit; this manifest does not claim manual line-by-line semantic review.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = inspect()
    if args.write:
        OUTPUT.parent.mkdir(exist_ok=True)
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "files": len(result["files"]),
                "modules": result["counts"],
                "errors": result["errors"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(bool(result["errors"]))


if __name__ == "__main__":
    main()
