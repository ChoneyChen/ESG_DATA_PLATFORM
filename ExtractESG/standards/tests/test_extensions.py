"""Extension contracts against real packages; all reviews/data are fixtures."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.extensions import (
    CompiledExtension, DerivedExtensionLineage, ExtensionApproval, ExtensionDefinition,
    ExtensionProposal, ExtensionRegistry, ExtensionSelection, ReviewAuthority,
    comparison_verdict, compile_extension, decide_proposal, definition_digest,
    derivation_rule_digest, load_registered_extension, register_extension,
    validate_lineage, validate_parent, validate_release, validate_selection,
    write_extension_release,
)
from esg_standard_packages.io import load_json, sha256_file
from esg_standard_packages.scope import load_registry
from esg_standard_packages.compiler import StandardPackageCompiler

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / 'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.0'


@pytest.fixture(scope='module')
def parent():
    # This suite exercises the immutable 1.0.0 extension, whose approval pins
    # parent 2.1.1. The active registry may legitimately advance independently.
    return StandardPackageCompiler().validate_compiled(
        ROOT / 'releases/esrs.2023-set1.e1-6/2.1.1/package.json')


@pytest.fixture
def proposal():
    return ExtensionProposal.model_validate(load_json(DRAFT / 'proposal.json'))


@pytest.fixture
def authority():
    return ReviewAuthority(actor_ref='synthetic_contract_reviewer', actor_kind='human',
        verified_by='server_authentication', capabilities=['approve_extension_definition'])


@pytest.fixture
def release(proposal, authority, parent):
    approval = decide_proposal(proposal, authority=authority, decision='approved',
                               reason='Independent contract review in an isolated test fixture')
    return compile_extension(proposal, parent=parent, approval=approval)


def test_original_catalog_and_major_ids_remain_unchanged(parent, proposal):
    _, _, packages = load_registry(ROOT)
    assert len(packages) == 99
    assert sum(len(p.metrics) for p in packages.values()) == 1126
    profile = load_json(ROOT / 'profiles/esrs/2023-set1/platform-major.json')
    assert len(profile['source_datapoint_ids']) == len(set(profile['source_datapoint_ids'])) == 55
    assert len(parent.metrics) == 35
    assert all(m.source_datapoint_id.startswith('E1-6_') for m in parent.metrics)
    assert proposal.definition.extension_id not in profile['source_datapoint_ids']
    assert not hasattr(proposal.definition, 'source_datapoint_id')
    assert ExtensionRegistry.model_validate(load_json(ROOT / 'extensions/registry.json')).registrations == []


def test_compile_pins_actual_parent_without_mutating_it(parent, proposal, authority):
    before = parent.model_dump(mode='json')
    approval = decide_proposal(proposal, authority=authority, decision='approved', reason='Synthetic review')
    first = compile_extension(proposal, parent=parent, approval=approval)
    second = compile_extension(proposal, parent=parent, approval=approval)
    assert first == second
    assert first.definition_digest == definition_digest(proposal.definition)
    assert validate_release(first.model_dump(mode='json'), parent=parent) == first
    assert parent.model_dump(mode='json') == before


def test_model_can_only_propose(proposal, parent, authority):
    raw = proposal.model_dump(mode='json')
    raw['proposer_kind'] = 'model'
    suggested = ExtensionProposal.model_validate(raw)
    raw['status'] = 'approved'
    with pytest.raises(ValidationError):
        ExtensionProposal.model_validate(raw)
    with pytest.raises(ValidationError):
        ReviewAuthority(actor_ref='model', actor_kind='model', verified_by='server_authentication',
                        capabilities=['approve_extension_definition'])
    rejected = decide_proposal(suggested, authority=authority, decision='rejected', reason='Synthetic rejection')
    with pytest.raises(PackageIntegrityError, match='approval'):
        compile_extension(suggested, parent=parent, approval=rejected)


def test_review_capability_and_substantive_reason_required(proposal, authority):
    no_role = authority.model_copy(update={'capabilities': []})
    with pytest.raises(PermissionError):
        decide_proposal(proposal, authority=no_role, decision='approved', reason='Synthetic review')
    with pytest.raises(ValueError):
        decide_proposal(proposal, authority=authority, decision='approved', reason='   ')


@pytest.mark.parametrize('field,value', [
    ('parent_package_id', 'esrs.2023-set1.e1-5'), ('parent_package_version', '2.1.0'),
    ('parent_source_digest', 'f' * 64),
])
def test_wrong_parent_snapshot_rejected(proposal, parent, field, value):
    raw = proposal.definition.model_dump(mode='json'); raw[field] = value
    with pytest.raises(PackageIntegrityError, match='parent package'):
        validate_parent(ExtensionDefinition.model_validate(raw), parent)


@pytest.mark.parametrize('mutation', ['unknown_original', 'wrong_metric', 'unit_dimension', 'unit_symbol'])
def test_original_links_and_operand_units_are_checked(proposal, parent, mutation):
    raw = proposal.definition.model_dump(mode='json')
    if mutation == 'unknown_original': raw['original_clause_links'][0]['source_datapoint_id'] = 'E1-6_99'
    elif mutation == 'wrong_metric': raw['derivation_rules'][0]['operands'][0]['source_metric_id'] = 'esrs.2023-set1.e1-6.dp09'
    elif mutation == 'unit_dimension': raw['unit_dimension'] = 'extractesg.core.unit-dimension.energy'
    else: raw['canonical_unit'] = 'MWh'
    with pytest.raises(PackageIntegrityError):
        validate_parent(ExtensionDefinition.model_validate(raw), parent)


def test_definition_change_needs_fresh_approval(proposal, parent, authority):
    approval = decide_proposal(proposal, authority=authority, decision='approved', reason='Synthetic review')
    raw = proposal.model_dump(mode='json'); raw['definition']['labels']['zh'] = 'Changed after review'
    with pytest.raises(PackageIntegrityError, match='exact proposal'):
        compile_extension(ExtensionProposal.model_validate(raw), parent=parent, approval=approval)


@pytest.mark.parametrize('field', ['definition', 'approval', 'release_digest'])
def test_tampered_release_rejected(release, parent, field):
    raw = release.model_dump(mode='json')
    if field == 'definition': raw['definition']['rationale'] = 'changed'
    elif field == 'approval': raw['approval']['actor_ref'] = 'changed'
    else: raw['release_digest'] = 'f' * 64
    with pytest.raises(PackageIntegrityError): validate_release(raw, parent=parent)


def test_timezone_required_for_review(release):
    raw = release.approval.model_dump(mode='json'); raw['decided_at'] = '2026-10-05T12:00:00'
    with pytest.raises(ValidationError): ExtensionApproval.model_validate(raw)


def test_immutable_registry_and_file_round_trip(release, parent, tmp_path):
    registry = ExtensionRegistry(schema_version='project-extension-registry-1.0')
    registered = register_extension(registry, release, path='scope1-scope2-total/1.0.0/release.json', parent=parent)
    assert registry.registrations == []
    assert register_extension(registered, release, path=registered.registrations[0].path, parent=parent) == registered
    path = write_extension_release(tmp_path, registered.registrations[0], release, parent=parent)
    assert load_registered_extension(tmp_path, registered.registrations[0], parent=parent) == release
    before = sha256_file(path)
    write_extension_release(tmp_path, registered.registrations[0], release, parent=parent)
    assert sha256_file(path) == before
    with pytest.raises(PackageIntegrityError, match='Immutable registered'):
        register_extension(registered, release, path='different/release.json', parent=parent)
    path.write_text('{}')
    with pytest.raises(PackageIntegrityError, match='overwrite'):
        write_extension_release(tmp_path, registered.registrations[0], release, parent=parent)


@pytest.mark.parametrize('path', ['../release.json', '/tmp/release.json', 'C:/release.json', 'x\\release.json', '.'])
def test_registration_paths_cannot_escape(release, parent, path):
    with pytest.raises(ValidationError):
        register_extension(ExtensionRegistry(schema_version='project-extension-registry-1.0'), release,
                           path=path, parent=parent)


def test_symlink_escape_checked_at_file_boundary(release, parent, tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'outside').symlink_to(outside, target_is_directory=True)
    registry = register_extension(ExtensionRegistry(schema_version='project-extension-registry-1.0'), release,
                                  path='outside/release.json', parent=parent)
    with pytest.raises(PackageIntegrityError, match='escapes'):
        write_extension_release(root, registry.registrations[0], release, parent=parent)


def selection(release, origins, rules=()):
    d = release.definition
    return ExtensionSelection(extension_id=d.extension_id, definition_version=d.definition_version,
        release_digest=release.release_digest, value_origins=origins, derivation_rule_ids=list(rules))


def test_reported_and_calculated_selection_are_explicit_and_separate(release, parent):
    reported = validate_selection(selection(release, ['reported']), release, parent=parent)
    assert reported['original_output_datapoint_ids'] == []
    assert reported['extension_output_count'] == 1
    assert reported['internal_operand_metric_ids'] == []
    location = release.definition.derivation_rules[0]
    calculated = validate_selection(selection(release, ['reported', 'derived'], [location.rule_id]), release, parent=parent)
    assert calculated['internal_operand_metric_ids'] == ['esrs.2023-set1.e1-6.dp07', 'esrs.2023-set1.e1-6.dp09']
    assert 'esrs.2023-set1.e1-6.dp10' not in calculated['internal_operand_metric_ids']
    with pytest.raises(PackageIntegrityError, match='explicitly choose'):
        validate_selection(selection(release, ['derived']), release, parent=parent)
    with pytest.raises(PackageIntegrityError, match='does not exist'):
        validate_selection(selection(release, ['derived'], ['missing.rule']), release, parent=parent)


def test_extension_core_does_not_imply_platform_selection(release, parent, proposal, authority):
    assert release.definition.package_core
    explicit = selection(release, ['reported']).model_copy(update={'selection_kind': 'platform_major_extension'})
    assert validate_selection(explicit, release, parent=parent)['selection_kind'] == 'platform_major_extension'
    raw = proposal.model_dump(mode='json'); raw['definition']['package_core'] = False
    detail = ExtensionProposal.model_validate(raw)
    approved = decide_proposal(detail, authority=authority, decision='approved', reason='Synthetic detail extension')
    detail_release = compile_extension(detail, parent=parent, approval=approved)
    explicit = selection(detail_release, ['reported']).model_copy(update={'selection_kind': 'platform_major_extension'})
    with pytest.raises(PackageIntegrityError, match='package-core'):
        validate_selection(explicit, detail_release, parent=parent)


def axes(method='location_based'):
    return {'entity_ref': 'synthetic-group', 'entity_kind': 'group', 'period_start': '2025-01-01',
        'period_end': '2025-12-31', 'boundary_code': 'group-consolidated', 'value_role': 'actual',
        'gwp_basis': 'ar6-100-year', 'consolidation_approach': 'operational_control',
        'accounting_standard': 'ghg-protocol-corporate-2004', 'ghg_scope': 'scope_1_2',
        'aggregation_role': 'total', 'amount_basis': 'gross', 'scope_2_method': method}


@pytest.mark.parametrize('method', ['unknown', '', None])
def test_unknown_reported_method_can_be_preserved_but_not_compared(proposal, method):
    context = axes(method)
    context['amount_basis'] = 'unknown'
    before = deepcopy(context)
    verdict = comparison_verdict(proposal.definition, context)
    assert verdict['comparison_eligibility'] == 'not_comparable'
    assert 'missing_comparison_axis:scope_2_method' in verdict['reason_codes']
    assert 'missing_comparison_axis:amount_basis' in verdict['reason_codes']
    assert context == before
    assert 'scope_2_method' not in proposal.definition.reported_required_axes
    assert 'amount_basis' not in proposal.definition.reported_required_axes


def test_net_amount_does_not_satisfy_gross_comparison(proposal):
    context = axes(); context['amount_basis'] = 'net'
    verdict = comparison_verdict(proposal.definition, context)
    assert 'comparison_axis_not_allowed:amount_basis' in verdict['reason_codes']


def lineage_payload(release, *, method='location_based', left='100.25', right='200.50', result='300.75'):
    index = 0 if method == 'location_based' else 1
    rule = release.definition.derivation_rules[index]
    operands = []
    for selector, value in zip(rule.operands, [left, right]):
        context = axes(method); context.pop('scope_2_method')
        context.update({k: v[0] for k, v in selector.required_axes.items()})
        if selector.operand_id == 'scope1': context['measurement_method'] = 'fuel-specific-factors'
        operands.append(dict(operand_id=selector.operand_id, observation_id=str(uuid4()),
            metric_id='synthetic.platform.' + selector.operand_id, standard_metric_id=selector.source_metric_id,
            metric_definition_version='1.0.0', source_datapoint_id=selector.source_datapoint_id, context_id=str(uuid4()),
            context_revision=1, fact_review_decision_id=str(uuid4()), mapping_review_decision_id=str(uuid4()),
            numeric_value=value, canonical_unit='tCO2e', normalization_rule_version='esg-units-1.1.0',
            normalization_rule_digest='a' * 64, reviewed_axes=context))
    return dict(schema_version='project-extension-lineage-1.0', extension_id=release.definition.extension_id,
        definition_version=release.definition.definition_version, release_digest=release.release_digest,
        rule_id=rule.rule_id, rule_version=rule.rule_version, rule_digest=derivation_rule_digest(rule),
        input_dataset_version=2, operands=operands, output_numeric_value=result, canonical_unit='tCO2e')


@pytest.mark.parametrize('method', ['location_based', 'market_based'])
def test_scope2_method_projects_from_correct_operand_not_scope1_method(release, parent, method):
    lineage = DerivedExtensionLineage.model_validate(lineage_payload(release, method=method))
    verdict = validate_lineage(lineage, release, parent=parent)
    assert verdict['reviewed_axes']['scope_2_method'] == method
    assert verdict['reviewed_axes']['ghg_scope'] == 'scope_1_2'
    assert verdict['review_status'] == 'pending'
    assert verdict['comparison_eligibility'] == 'comparable'


def test_large_decimal_lineage_remains_exact(release, parent):
    payload = lineage_payload(release, left='900719925474099312345.678901',
        right='0.000000000000000000001', result='900719925474099312345.678901000000000000001')
    lineage = DerivedExtensionLineage.model_validate(payload)
    assert validate_lineage(lineage, release, parent=parent)['value_origin'] == 'derived'
    payload['output_numeric_value'] = '900719925474099312345.678901'
    with pytest.raises(ValidationError, match='exact decimal'): DerivedExtensionLineage.model_validate(payload)


@pytest.mark.parametrize('field,value', [
    ('period_end', '2024-12-31'), ('boundary_code', 'subsidiary-only'), ('gwp_basis', 'unknown'),
    ('consolidation_approach', 'equity_share'), ('amount_basis', 'net'), ('measurement_method', 'market_based'),
])
def test_lineage_cannot_mix_identity_methods_or_net_inputs(release, parent, field, value):
    payload = lineage_payload(release); payload['operands'][1]['reviewed_axes'][field] = value
    with pytest.raises(PackageIntegrityError):
        validate_lineage(DerivedExtensionLineage.model_validate(payload), release, parent=parent)


def test_lineage_rule_digest_and_observation_uniqueness(release, parent):
    payload = lineage_payload(release); payload['rule_digest'] = 'f' * 64
    with pytest.raises(PackageIntegrityError, match='rule identity'):
        validate_lineage(DerivedExtensionLineage.model_validate(payload), release, parent=parent)
    payload = lineage_payload(release); payload['operands'][1]['observation_id'] = payload['operands'][0]['observation_id']
    with pytest.raises(ValidationError, match='Duplicate lineage observation'):
        DerivedExtensionLineage.model_validate(payload)


def test_negative_ghg_derived_result_cannot_publish(release, parent):
    payload = lineage_payload(release, left='-2', right='-1', result='-3')
    with pytest.raises(PackageIntegrityError, match='value constraints'):
        validate_lineage(DerivedExtensionLineage.model_validate(payload), release, parent=parent)


@pytest.mark.parametrize('start,end', [('2025', '2025'), ('2025-12-31', '2025-01-01'), ('20250101', '20251231')])
def test_matching_period_strings_still_require_canonical_iso_dates(release, parent, start, end):
    payload = lineage_payload(release)
    for operand in payload['operands']:
        operand['reviewed_axes'].update(period_start=start, period_end=end)
    with pytest.raises(PackageIntegrityError, match='canonical ISO dates'):
        validate_lineage(DerivedExtensionLineage.model_validate(payload), release, parent=parent)


@pytest.mark.parametrize('mutation', ['arbitrary_code', 'unknown_selector', 'missing_identity', 'wrong_projection', 'incomplete_target'])
def test_definition_cannot_enable_unbounded_or_incomplete_calculations(proposal, mutation):
    raw = proposal.definition.model_dump(mode='json'); rule = raw['derivation_rules'][0]
    if mutation == 'arbitrary_code': rule['operation'] = '__import__(os).system'
    elif mutation == 'unknown_selector': rule['operands'][1]['required_axes']['measurement_method'] = ['unknown']
    elif mutation == 'missing_identity': rule['shared_match_axes'].remove('period_end')
    elif mutation == 'wrong_projection': rule['target_axis_projections'][-1]['from_operand_id'] = 'scope1'
    else: rule['target_axis_projections'] = rule['target_axis_projections'][:-1]
    with pytest.raises(ValidationError): ExtensionDefinition.model_validate(raw)


def test_generated_schemas_match_executable_contracts():
    for name, model in [('definition', ExtensionDefinition), ('proposal', ExtensionProposal),
        ('approval', ExtensionApproval), ('release', CompiledExtension), ('registry', ExtensionRegistry),
        ('selection', ExtensionSelection), ('lineage', DerivedExtensionLineage)]:
        assert load_json(ROOT / 'extensions/schemas' / f'{name}.schema.json') == model.model_json_schema()
