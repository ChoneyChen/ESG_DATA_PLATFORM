"""The reader's page keywords must cover every family the extractor can ask for."""
import sys
from pathlib import Path

EXTRACT = Path(__file__).resolve().parents[3]
for directory in (EXTRACT / "extraction/src", EXTRACT / "document_processing/src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from esg_targeted.foundation.catalog import BY_CODE, GROUPS as EXTRACT_GROUPS, PROFILES  # noqa: E402
from esg_v2.ocr.page_selection import GROUPS as READER_GROUPS  # noqa: E402

# Measurement-method statements are selected beside pollutant pages by wording,
# not by a page family of their own.
METHOD_GROUPS = {"pollution_method"}


def test_reader_covers_every_family_the_extractor_may_ask_for():
    reader = {group.key: group for group in READER_GROUPS}
    for group in EXTRACT_GROUPS:
        assert group.key in reader, f"page selector misses family {group.key}"
        missing = [term for term in group.strong if term not in reader[group.key].strong]
        assert not missing, f"page selector misses {group.key} terms: {missing}"


def test_every_profiled_metric_has_a_page_family():
    families = {group.key for group in READER_GROUPS} | METHOD_GROUPS
    for profile, codes in PROFILES.items():
        for code in codes:
            group = BY_CODE[code].group
            assert group in families, f"{profile}:{code} group {group} has no page family"
