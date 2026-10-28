"""Page keywords come from the selected clauses' own retrieval contracts."""
import sys
from pathlib import Path

EXTRACT = Path(__file__).resolve().parents[3]
for directory in (EXTRACT / "extraction/src", EXTRACT / "document_processing/src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from esg_targeted.foundation.catalog import BY_CODE, PROFILES  # noqa: E402
from esg_v2.ocr.page_selection import Foundation16PageSelector, _page_terms, clause_groups  # noqa: E402


def test_every_profiled_clause_has_page_terms():
    for profile, codes in PROFILES.items():
        groups = {group.key: group for group in clause_groups(profile)}
        for code in codes:
            assert code in groups, f"{profile}:{code} has no page group"
            expected = set(_page_terms(BY_CODE[code].retrieval_terms))
            assert set(groups[code].strong) == expected


def test_the_selector_defaults_to_the_clause_contract():
    groups = Foundation16PageSelector(profile="base16").keyword_groups
    assert {group.key for group in groups} == set(PROFILES["base16"])


def test_no_fixed_family_keyword_table_remains():
    import esg_v2.ocr.page_selection as page

    assert not hasattr(page, "GROUPS")
    assert not hasattr(page, "_GROUP_MATCHERS")
