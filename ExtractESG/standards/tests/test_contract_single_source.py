"""The clause contract is authored once, in the standard tree, and nowhere else."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
POLICY = ROOT / "contracts/platform-policy.esrs-2023-set1.json"
BUILDER = REPO / "PlatESG/scripts/build-dictionary.py"


def test_dictionary_builder_authors_no_clause():
    text = BUILDER.read_text(encoding="utf-8")
    # A source datapoint id in the builder would be a second authored contract.
    assert not re.search(r"['\"][A-Z]{1,3}\d?[-.]\d+_\d+['\"]", text)
    assert "fixed_dimensions=" not in text


def test_platform_policy_covers_the_selected_clauses():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    selected = json.loads((ROOT / "profiles/esrs/2023-set1/platform-major.json").read_text(encoding="utf-8"))
    assert set(policy["metrics"]) == set(selected["source_datapoint_ids"])
    for source_id, entry in policy["metrics"].items():
        assert entry["required_dimensions"] is not None, source_id
        assert "comparison_dimensions" in entry, source_id
        assert entry["value_type"] in {"numeric", "statement"}, source_id


def test_fixed_axes_are_declared_next_to_the_standard():
    axes = json.loads((ROOT / "profiles/esrs/2023-set1/clause-fixed-axes.json").read_text(encoding="utf-8"))
    assert axes["axes"], "fixed clause values must be declared in the standard tree"
    assert all(isinstance(value, dict) and value for value in axes["axes"].values())


def test_no_source_file_reads_the_external_standard_folder():
    """The authoring folder outside the repository is an obsolete snapshot."""
    forbidden = ("标准要求包/", "/标准要求包", "契约参考/", "示例_ESRS")
    suffixes = {".py", ".mjs", ".ts", ".tsx", ".json", ".sh"}
    skip = {"node_modules", ".venv", "__pycache__", ".git", ".local", "history", "migrations"}
    offenders = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if skip & set(path.parts) or path == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []
