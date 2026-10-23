from pathlib import Path

from esg_standard_packages.scope import compile_profile, load_registry
from esg_targeted.contracts import ReportExtractionRequest
from esg_targeted.models.report_semantics import semantic_card
from esg_targeted.standards.catalog import StandardPackageCatalog
from tests.helpers import standard_dist_root


ROOT=Path(__file__).resolve().parents[3]/'standard_packages'


def test_all_new_packages_load_in_existing_targeted_catalog():
    packages,issues=StandardPackageCatalog(standard_dist_root()).scan()
    assert not issues
    assert len(packages)==99
    # Versions come from the registry so a published package never needs a
    # test edit; the scan above already proved every dist package loads.
    import json
    registry=json.loads((ROOT/'registry/esrs/2023-set1/registry.json').read_text())
    versions={m['disclosure_requirement']:m['package_version'] for m in registry['modules'] if m['executable']}
    assert all(p.package_version==versions[p.disclosure_requirement] and p.status=='draft' for p in packages)
    assert {p.disclosure_requirement for p in packages}>={'E1-5','E1-6','E2-4','E3-4','MDR-M','BP-1'}


def test_major_scope_is_a_valid_existing_report_session_selection():
    scope=compile_profile(ROOT,ROOT/'profiles/esrs/2023-set1/platform-major.json',topics=['E1','E2','E3'])
    request=ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'ir-scope-fixture'},standards=scope['session_standards'])
    assert len(request.standards)==5
    assert sum(len(s.metric_ids) for s in request.standards)==19
    assert not any(s.package_id.endswith(('mdr-m','bp-1','sbm-1')) for s in request.standards)


def test_full_environment_scope_fits_one_report_session():
    scope=compile_profile(ROOT,ROOT/'profiles/esrs/2023-set1/environment-all.json')
    request=ReportExtractionRequest(input={'kind':'document_ir','ir_run_id':'ir-scope-fixture'},standards=scope['session_standards'])
    assert len(request.standards)==25
    assert sum(len(s.metric_ids) for s in request.standards)==322


def test_new_measurement_contract_reaches_semantic_card_with_original_id():
    _,_,packages=load_registry(ROOT)
    package=packages['E3-4'];metric=next(m for m in package.metrics if m.source_datapoint_id=='E3-4_11')
    card=semantic_card(metric,[e for e in package.elements if e.metric_id==metric.metric_id])
    assert card['source_datapoint_id']=='E3-4_11'
    assert card['measurement_contract']['canonical_unit_id']=='extractesg.core.unit.cubic-metre'
    assert card['measurement_contract']['normalization']=='compatible_unit_only'
    assert 'water_measure' in card['identity_axes']
    assert 'provenance' not in card # Full raw catalog rows don't inflate model input.


def test_semantic_cards_keep_new_roles_separate_and_read_legacy_contracts():
    catalog=StandardPackageCatalog(standard_dist_root())
    new=catalog.load('esrs.2023-set1.e1-5','2.1.2')
    metric=next(m for m in new.metrics if m.source_datapoint_id=='E1-5_01')
    card=semantic_card(metric,[e for e in new.elements if e.metric_id==metric.metric_id])
    assert card['positive_definition']==metric.measurement_contract.positive_definition
    assert card['interpretation_guidance']==metric.measurement_contract.interpretation_guidance
    assert card['semantic_distinctions']==metric.measurement_contract.distinguish_from
    assert card['positive_definition'] not in card['exclusions']
    assert not set(card['interpretation_guidance']) & set(card['exclusions'])
    old=catalog.load('esrs.2023-set1.e1-5','2.1.1')
    old_metric=next(m for m in old.metrics if m.source_datapoint_id=='E1-5_01')
    old_card=semantic_card(old_metric,[e for e in old.elements if e.metric_id==old_metric.metric_id])
    assert old_card['positive_definition']==old_metric.description
    assert old_card['exclusions']==old_metric.measurement_contract.exclusions
    assert old_card['interpretation_guidance']==[]
