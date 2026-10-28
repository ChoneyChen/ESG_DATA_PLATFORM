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


def test_extraction_runtime_contains_no_clause_program():
    """No clause list, no per-clause branch, no fixed page-keyword table."""
    import re
    catalog = REPO / "ExtractESG/extraction/src/esg_targeted/foundation/catalog.py"
    guards = REPO / "ExtractESG/extraction/src/esg_targeted/foundation/guards.py"
    page = REPO / "ExtractESG/document_processing/src/esg_v2/ocr/page_selection.py"
    assert not re.search(r'FoundationMetric\(\s*"', catalog.read_text(encoding="utf-8"))
    assert not re.search(r'(?:if|elif)\s+code\s*==\s*[\'"]', guards.read_text(encoding="utf-8"))
    assert "GROUPS: tuple" not in page.read_text(encoding="utf-8")
    assert not (REPO / "ExtractESG/extraction/src/esg_targeted/foundation/specs.py").exists()


def test_platform_major_clauses_declare_kind_reading_and_authoring():
    from esg_standard_packages.clause_package import ClausePackageCatalog
    from esg_standard_packages.scope import load_registry

    root = ROOT
    registry, _, _ = load_registry(root)
    major = set(json.loads((root / "profiles/esrs/2023-set1/platform-major.json").read_text(
        encoding="utf-8"))["source_datapoint_ids"])
    catalog = ClausePackageCatalog(root)
    seen = set()
    for module in registry["modules"]:
        if not module["executable"]:
            continue
        for code in module["source_datapoint_ids"]:
            if code not in major:
                continue
            seen.add(code)
            profile = catalog.profile(module["package_id"], module["package_version"], code)
            assert profile.clause_key == code
            assert profile.data_kind in {"quantitative", "qualitative"}
            assert profile.reading.operator in {"numeric_label", "ghg_scope", "pollutant", "water",
                                                "waste", "statement"}
            assert (root / "clause-authoring" / f"{code}.json").is_file()
            if profile.data_kind == "qualitative":
                assert profile.value_type == "statement" and profile.canonical_unit is None
            else:
                assert profile.value_type == "numeric" and profile.canonical_unit
    assert seen == major


def test_one_clause_edit_does_not_re_identify_its_package_siblings():
    from esg_standard_packages.clause_package import ClausePackageCatalog

    catalog = ClausePackageCatalog(ROOT)
    old_entry = catalog.package_entry("esrs.2023-set1.e1-5", "2.1.2")
    codes = [row["source_datapoint_id"] for row in old_entry["clauses"]]
    changed, unchanged = [], []
    for code in codes:
        old = catalog.profile("esrs.2023-set1.e1-5", "2.1.2", code)
        new = catalog.profile("esrs.2023-set1.e1-5", "2.1.3", code)
        (changed if old.clause_digest != new.clause_digest else unchanged).append(code)
    assert changed == ["E1-5_01"]
    assert len(unchanged) == len(codes) - 1
