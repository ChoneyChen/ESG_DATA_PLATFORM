import copy
from pathlib import Path

import pytest
from esg_targeted.contracts import ExtractionSelectionSnapshot
from esg_targeted.standards.catalog import StandardPackageCatalog
from esg_targeted.standards.profiles import platform_major_profile, validate_selection_snapshot

STANDARD_ROOT=Path(__file__).resolve().parents[3]/"standard_packages"


def test_major_profile_pins_dictionary_and_selected_outputs_independently_of_core():
    scope=platform_major_profile(STANDARD_ROOT,topics=["E1","E2","E3"])
    assert len(scope["overall_source_datapoint_ids"])==55
    assert len(scope["source_datapoint_ids"])==19
    assert len(scope["pending_source_datapoint_ids"])==0
    catalog=StandardPackageCatalog(STANDARD_ROOT/"dist")
    selections=[(catalog.load(s["package_id"],s["package_version"]),s["metric_ids"]) for s in scope["session_standards"]]
    snapshot=ExtractionSelectionSnapshot.model_validate(scope["selection_snapshot"])
    validate_selection_snapshot(STANDARD_ROOT,snapshot,selections)
    changed=copy.deepcopy(snapshot)
    changed.dictionary_digest="0"*64
    with pytest.raises(ValueError,match="dictionary content changed"):
        validate_selection_snapshot(STANDARD_ROOT,changed,selections)
    changed=copy.deepcopy(snapshot)
    changed.source_datapoint_ids.append("E1-5_02")
    with pytest.raises(ValueError,match="output clauses differ"):
        validate_selection_snapshot(STANDARD_ROOT,changed,selections)


def test_global_major_profile_selects_every_ready_clause():
    scope=platform_major_profile(STANDARD_ROOT,topics=None)
    assert len(scope["source_datapoint_ids"])==55
    assert scope["pending_source_datapoint_ids"]==[]
    assert set(scope["source_datapoint_ids"])==set(scope["ready_source_datapoint_ids"])
