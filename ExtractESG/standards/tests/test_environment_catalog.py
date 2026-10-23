from copy import deepcopy
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.contracts import ModuleSource
from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.io import canonical_json_bytes, load_json, write_json
from esg_standard_packages.scope import ExtractionProfile, compile_profile, load_registry, resolve_context_requirements, resolve_shared_requirements, validate_registry

ROOT=Path(__file__).resolve().parents[1]
CATALOG=ROOT/'catalogs/esrs/2023-set1/ig3-original.json'
PROFILE=ROOT/'profiles/esrs/2023-set1/platform-major.json'


@pytest.fixture(scope='module')
def built():return load_registry(ROOT)


def test_every_original_number_has_exactly_one_module_and_no_mdr_fake_metrics(built):
    registry,catalog,packages=built
    assert len(catalog['records'])==1184
    # Every original datapoint is compiled; a new planned module must be a
    # deliberate regression, not an unnoticed coverage gap.
    assert len(packages)==99
    assert sum(len(p.metrics) for p in packages.values())==1126
    assert sum(not r['executable'] for r in registry['modules'])==0
    expected={r['id'] for r in catalog['records'] if r['record_kind']=='独立编号条目'}
    assert len(expected)==1126
    assert {i for r in registry['modules'] for i in r['source_datapoint_ids']}==expected
    assert len(registry['shared_requirement_references'])==58
    ids={m.source_datapoint_id for p in packages.values() for m in p.metrics}
    assert not any('.MDR-' in i for i in ids)
    assert {r['source_datapoint_id'] for r in registry['shared_requirement_references']}.isdisjoint(ids)
    environment={m.source_datapoint_id for dr,p in packages.items() if dr.startswith(('E1','E2','E3')) for m in p.metrics}
    assert len(environment)==322
    assert 'E1-5_19' in environment # Do not silently apply later errata to this user catalog.
    assert 'S3-3_11' in expected and 'S3-3_01' not in expected


@pytest.mark.parametrize('topic,count',[('E1',211),('E2',66),('E3',45)])
def test_topical_coverage_against_original_catalog(built,topic,count):
    _,catalog,packages=built
    raw={r['id']:r for r in catalog['records'] if r['source_sheet']=='ESRS '+topic and r['record_kind']=='独立编号条目'}
    actual={m.source_datapoint_id:m for dr,p in packages.items() if dr.startswith(topic) for m in p.metrics}
    assert len(actual)==count and actual.keys()==raw.keys()
    for original,metric in actual.items():
        assert metric.provenance.raw_values==raw[original]['raw_values']
        assert metric.provenance.source_sheet==raw[original]['source_sheet']
        assert metric.provenance.original_data_type==raw[original]['data_type']


def test_platform_major_exact_user_list_and_package_core_is_broader(built):
    user_list='E1-5_01 E1-5_05 E1-5_09 E1-6_07 E1-6_09 E1-6_10 E1-6_11 E2-4_02 E2-4_03 E2-4_09 E2-5_02 E3-4_01 E3-4_02 E3-4_03 E3-4_11 E3-4_12 E4.IRO-1_14 E4.SBM-3_04 E4-5_01 E4-5_02 E5-4_02 E5-4_05 E5-5_07 E5-5_11 E5-5_15 S1-6_02 S1-6_12 S1-9_02 S1-13_03 S1-14_02 S1-14_05 S1-16_01 S2-1_05 S2-3_02 S2-3_06 S2-4_11 S3-1_01 S3-3_11 S3-4_02 S3-4_11 S4-1_01 S4-3_02 S4-3_13 S4-4_11 G1-1_03 G1-4_01 G1-4_02 G1-4_04 G1-6_01'.split()
    # The user's 49 stay in order; the six clauses of the priority-32 set that
    # were outside that list join them, so the platform covers every metric the
    # workbench can extract.
    added=['E1-5_02','E1-6_12','E1-6_30','S1-13_04','S1-17_02','GOV-1_06']
    profile=ExtractionProfile.model_validate(load_json(PROFILE))
    kept=[i for i in profile.source_datapoint_ids if i in set(user_list)]
    assert kept==user_list and len(user_list)==49
    assert set(profile.source_datapoint_ids)-set(user_list)==set(added)
    assert len(profile.source_datapoint_ids)==55
    registry,_,packages=built
    for r in registry['modules']:
        assert set(r['major_source_datapoint_ids'])<=set(r['core_source_datapoint_ids'])
    assert 'E1-5_02' in packages['E1-5'].manifest.core_source_datapoint_ids
    assert 'E1-5_02' in profile.source_datapoint_ids
    assert all(p.manifest.core_source_datapoint_ids for p in packages.values())


def test_full_compilation_leaves_no_pending_scope():
    # With every module compiled, selecting the platform-major profile no
    # longer meets a planned clause. The topic filter still narrows the
    # selection explicitly and never drops the rest silently.
    result=compile_profile(ROOT,PROFILE,topics=['E1','E2','E3'])
    total=len(load_json(PROFILE)['source_datapoint_ids'])
    # E1–E3 now hold 19 platform-major clauses: the original 16 plus fossil
    # energy, total emissions and emission intensity.
    assert result['output_count']==19
    assert len(result['excluded_by_explicit_filter'])==total-19
    assert len(result['session_standards'])==5
    assert sum(len(s['metric_ids']) for s in result['session_standards'])==19
    assert all('MDR-' not in i for i in result['source_datapoint_ids'])


def test_local_core_and_full_are_explicit_distinct_profiles():
    core=compile_profile(ROOT,ROOT/'profiles/esrs/2023-set1/package-core/e1-1.json')
    assert core['source_datapoint_ids']==['E1-1_01','E1-1_02','E1-1_03','E1-1_06','E1-1_15']
    all_=compile_profile(ROOT,ROOT/'profiles/esrs/2023-set1/environment-all.json')
    assert all_['output_count']==322 and len(all_['routes'])==25


def test_operands_and_context_do_not_expand_output(tmp_path):
    profile=load_json(ROOT/'profiles/esrs/2023-set1/environment-all.json')
    profile['source_datapoint_ids']=['E1-5_09']
    path=tmp_path/'ratio.json';write_json(path,profile)
    result=compile_profile(ROOT,path)
    route=result['routes'][0]
    assert result['output_count']==1 and route['source_datapoint_ids']==['E1-5_09']
    assert route['output_metric_ids']==['esrs.2023-set1.e1-5.dp09']
    assert {'esrs.2023-set1.e1-5.dp01','esrs.2023-set1.e1-5.dp05'}<=set(route['internal_dependency_metric_ids'])
    # Nested sums needed to reconstruct those operands are allowed; unrelated
    # presentation, production, intensities and revenue must not be activated.
    assert not any(i.endswith(('dp04','dp15','dp16','dp17','dp18','dp19','dp20','dp21','dp22','dp23')) for i in route['execution_metric_ids'])
    assert all(r['output_policy']=='context_only' for r in route['context_requirement_references'])


def test_pinned_esrs2_and_mdr_context_interface_is_selective(built):
    _,_,packages=built
    refs=resolve_context_requirements(packages['E3-4'],['E3-4_01'],packages)
    assert {r['purpose'] for r in refs}=={'report_identity','measurement_method','business_context'}
    assert all(r['source_digest'] and r['target_package_version']=='2.1.1' for r in refs)
    assert any(r['target_source_datapoint_ids']==['MDR-M_01','MDR-M_02'] for r in refs)
    nonnumeric=resolve_context_requirements(packages['E3-4'],['E3-4_06'],packages)
    assert not any(r['purpose']=='measurement_method' for r in nonnumeric)


def test_mdr_adoption_branch_does_not_invent_dr_or_activate_unknown(built):
    registry,_,packages=built
    refs=resolve_shared_requirements(packages['E1-2'],registry,packages)
    assert {r['purpose'] for r in refs}=={'adopted_policy','absent_policy'}
    assert all(r['activation_state']=='awaiting_report_context' for r in refs)
    absent=resolve_shared_requirements(packages['E1-2'],registry,packages,adopted=False)
    assert len(absent)==1
    assert absent[0]['source_datapoint_id']=='E1.MDR-P_07-08'
    assert absent[0]['original_disclosure_requirement'] is None
    assert absent[0]['target_source_datapoint_ids']==['MDR-P_07','MDR-P_08']
    assert absent[0]['output_policy']=='context_only'


def test_semantic_type_overrides_preserve_original_metadata(built):
    _,_,packages=built
    by_original={m.source_datapoint_id:m for p in packages.values() for m in p.metrics}
    assert by_original['MDR-T_05'].provenance.original_data_type=='Integer'
    assert by_original['MDR-T_05'].measurement_contract.semantic_type_note
    assert by_original['E3-3_05'].provenance.original_data_type=='narrative/monetary'
    assert by_original['E3-3_05'].data_class.value=='qualitative'
    assert by_original['E1-8_04'].measurement_contract.unit_dimension.endswith('carbon-price')
    assert by_original['E3-4_08'].measurement_contract.canonical_unit_id.endswith('cubic-metre-per-million-eur')
    assert by_original['E3-4_10'].measurement_contract.normalization!='percent_to_ratio'
    p=packages['E3-4'];v=next(e for e in p.elements if e.metric_id.endswith('dp05') and e.element_code=='value')
    assert v.value_contract.minimum is None
    assert by_original['E3-4_11'].obligation_class.value=='voluntary_if_material'
    assert by_original['BP-1_01'].obligation_class.value=='mandatory'
    assert by_original['E1-7_11'].measurement_contract.value_role=='forecast'
    assert by_original['MDR-A_11'].measurement_contract.value_role=='forecast'
    assert by_original['MDR-T_13'].measurement_contract.value_role=='actual'
    assert by_original['SBM-3_09'].measurement_contract.value_role=='forecast'
    assert by_original['E1-9_01'].measurement_contract.value_role=='actual'


def test_mixed_narrative_numbers_are_numeric_and_signed_differences_survive(built):
    _,_,packages=built
    for original in ['BP-2_07','BP-2_14','IRO-1_14','MDR-T_18']:
        package=next(p for p in packages.values() if any(m.source_datapoint_id==original for m in p.metrics))
        metric=next(m for m in package.metrics if m.source_datapoint_id==original)
        value=next(e for e in package.elements if e.metric_id==metric.metric_id and e.element_code=='value')
        assert value.binding.target=='value_numeric'
        assert value.value_contract.primary_type.value=='decimal'
        assert value.value_contract.minimum is None
        assert value.null_allowed # Mixed clauses can carry a statement alone.


@pytest.mark.parametrize('corrupt',['duplicate','core','provenance','unit','context','binding_type'])
def test_corrupt_contracts_are_rejected(built,corrupt):
    _,catalog,packages=built;p=packages['E3-4']
    payload={k:deepcopy(p.model_dump(mode='json')[k]) for k in ['manifest','metrics','elements','concepts','relations','dimensions','code_sets','derivations','sources','validation_rules']}
    if corrupt=='duplicate':payload['metrics'][1]['source_datapoint_id']=payload['metrics'][0]['source_datapoint_id']
    if corrupt=='core':payload['manifest']['core_source_datapoint_ids'].append('E3-4_99')
    if corrupt=='provenance':payload['metrics'][0]['provenance']['raw_values'][0]='E3-4_99'
    if corrupt=='unit':payload['metrics'][0]['measurement_contract']['canonical_unit_id']='extractesg.core.unit.unknown'
    if corrupt=='context':payload['manifest']['context_requirements'][0]['applies_to_source_datapoint_ids']=['E3-4_99']
    if corrupt=='binding_type':
        next(e for e in payload['elements'] if e['element_code']=='value')['value_contract']['primary_type']='text'
    with pytest.raises(PackageIntegrityError):StandardPackageCompiler()._validate_integrity(p.core,ModuleSource.model_validate(payload))


def test_registry_rejects_wrong_version_and_missing_source_coverage(built):
    registry,catalog,packages=built
    for corruption in ['version','coverage','shared']:
        broken=deepcopy(registry)
        if corruption=='version':broken['modules'][0]['package_version']='99.0.0'
        if corruption=='coverage':broken['modules']=[r for r in broken['modules'] if r['disclosure_requirement']!='E3-4']
        if corruption=='shared':broken['shared_requirement_references'].pop()
        with pytest.raises(PackageIntegrityError):validate_registry(broken,catalog,packages)


def test_a_compiled_module_cannot_lose_its_identity(built):
    registry,_,_=built
    r=deepcopy(registry['modules'][0]);r['package_version']=None
    from esg_standard_packages.scope import ModuleRegistration
    with pytest.raises(ValidationError):ModuleRegistration.model_validate(r)


def test_deterministic_rebuild_preserves_published_sources(tmp_path,built):
    sys.path.insert(0,str(ROOT/'scripts'))
    from build_esrs_environment import generate
    _,_,packages=built
    regenerated,registry=generate(tmp_path)
    assert {dr:p.compilation.source_digest for dr,p in regenerated.items()}=={dr:p.compilation.source_digest for dr,p in packages.items()}
    for dr,p in regenerated.items():
        registration=next(r for r in registry['modules'] if r['disclosure_requirement']==dr)
        source=ROOT/registration['source_directory']
        module_dir=tmp_path/'packages'/source.relative_to(ROOT/'authoring')
        for path in module_dir.glob('*.json'):
            assert path.read_bytes()==(source/path.name).read_bytes()


def test_historical_expert_contracts_are_preserved_except_new_metadata(built):
    _,_,packages=built
    for dr in ['E1-5','E1-6','E2-4']:
        old=StandardPackageCompiler().validate_compiled(ROOT/f'dist/.retired/esrs.2023-set1.{dr.lower()}/2.0.0/package.json')
        new=packages[dr]
        assert canonical_json_bytes([e.model_dump(mode='json') for e in old.elements])==canonical_json_bytes([e.model_dump(mode='json') for e in new.elements])
        assert old.derivations==new.derivations and old.relations==new.relations
