from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import hashlib
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from PlatESG.backend.main import create_app
from esg_targeted.contracts import ReportExtractionRequest
from esg_targeted.report_session import ReportSessionService
from esg_targeted.standards.catalog import StandardPackageCatalog

ROOT = Path(__file__).resolve().parents[2]
STANDARDS = ROOT / 'ExtractESG/standards'
DRAFT = STANDARDS / 'extensions/authoring/esrs/2023-set1/e1-6/scope1-scope2-total/1.0.0/proposal.json'


def accept_release(ledger_path: Path, release: dict, bundle_root: Path | None = None) -> None:
    """Bind one isolated acceptance to this release so the publication gate can be exercised.

    A synthetic fixture is not a real report acceptance; the ledger here is a
    per-test file and never the standards ledger.
    """
    from esg_standard_packages.clause_lifecycle import (
        ClauseRef, evaluate_run, extension_contract_digest, record_acceptance,
    )
    definition = release['definition']
    result = (json.loads((bundle_root / 'exports/extraction-result.json').read_text()) if bundle_root
              else {'records': {'reporting_tasks': [{'task_id': 'task-1', 'metric_id': definition['extension_id'],
                                                     'task_status': 'completed', 'found_status': 'reported',
                                                     'ir_run_id': 'ir-fixture', 'document_id': 'doc-fixture'}],
                                'quantitative_observations': [{'observation_id': 'obs-1', 'task_id': 'task-1',
                                                               'value_raw': '30', 'value_numeric': '30',
                                                               'unit_raw': 'tCO2e', 'reporting_year': 2024,
                                                               'reporting_boundary': 'china_own_operations',
                                                               'evidence_set_id': 'evset-1'}],
                                'evidence_references': [{'evidence_id': 'evidence-1', 'evidence_set_id': 'evset-1',
                                                         'ir_object_id': 'cell-fixture-1', 'pdf_page_index': 0}]},
                      'source_facts': {'gaps': []},
                      'summary': {'document': {'document_id': 'doc-fixture', 'document_label': '隔离合同回放'}}})
    acceptance = evaluate_run(result,
        clause=ClauseRef(kind='extension', extension_id=definition['extension_id'],
                         definition_version=definition['definition_version']),
        metric_ids=[definition['extension_id']], contract_digest_value=extension_contract_digest(definition),
        run_ref=bundle_root.name if bundle_root else 'fixture-run', recorded_by='fixture',
        reason='隔离合同回放：仅用于验证发布门禁与该版本登记，非真实报告验收')
    assert acceptance.outcome == 'accepted', [(check.check, check.outcome, check.detail) for check in acceptance.checks]
    record_acceptance(ledger_path, acceptance)


def test_authenticated_extension_definition_registration_and_platform_selection_are_independent(tmp_path, monkeypatch):
    runtime = tmp_path / 'extensions'
    monkeypatch.setenv('ESG_EXTENSION_ROOT', str(runtime))
    app = create_app({'PLATESG_DATA_DIR': str(tmp_path / 'data'), 'PLATESG_REVIEW_TOKEN': 'admin-token',
        'PLATESG_AUTO_PUBLISH': '0', 'ESG_EXTENSION_ROOT': str(runtime), 'ESG_CLAUSE_LIFECYCLE_LEDGER': str(tmp_path / 'ledger.json'),
        'PLATESG_OPERATORS_JSON': json.dumps([{'token': 'review-token', 'operator_ref': 'review-only', 'roles': ['read', 'review']}])})
    admin = {'Authorization': 'Bearer admin-token'}
    reviewer = {'Authorization': 'Bearer review-token'}
    with TestClient(app) as client:
        original = client.get('/v1/metrics').json()
        assert len(original['items']) == 55
        draft = client.get('/internal/v1/extensions', headers=admin).json()['authoring_drafts'][0]
        proposal = json.loads(DRAFT.read_text())
        proposal.update(proposer_kind='human', proposer_ref='local-reviewer')
        assert client.post('/internal/v1/extensions/proposals', json=proposal).status_code == 401
        assert client.post('/internal/v1/extensions/proposals', json={**proposal, 'approval': {'decision': 'approved'}}, headers=admin).status_code == 422
        assert client.post('/internal/v1/extensions/proposals', json={**proposal, 'proposer_ref': 'fabricated-actor'}, headers=admin).status_code == 409
        saved = client.post('/internal/v1/extensions/proposals', json=proposal, headers=admin)
        assert saved.status_code == 200, saved.text
        decision = {'proposal_id': proposal['proposal_id'], 'decision': 'approved', 'reason': '核对独立延伸身份、原点关联、单位和完整方法约束'}
        assert client.post('/internal/v1/extensions/decisions', json=decision, headers=reviewer).status_code == 403
        approved = client.post('/internal/v1/extensions/decisions', json=decision, headers=admin)
        assert approved.status_code == 200, approved.text
        assert approved.json()['approval']['actor_ref'] == 'local-reviewer'
        assert not (runtime / 'registry.json').exists()
        registered = client.post('/internal/v1/extensions/register', json={'proposal_id': proposal['proposal_id'], 'approval_id': approved.json()['approval']['approval_id']}, headers=admin)
        assert registered.status_code == 200, registered.text
        release = registered.json()['release']
        assert registered.json()['runtime_projection_state'] == 'ready'
        assert len(json.loads((runtime / 'registry.json').read_text())['registrations']) == 1
        assert client.get('/v1/metrics').json() == original
        assert client.post('/internal/v1/extensions/decisions', json={**decision, 'decision': 'rejected'}, headers=admin).status_code == 422
        selected = client.post('/internal/v1/extensions/platform-selection', json={'dictionary_version': '1.1.0', 'extension_ids': [release['definition']['extension_id']]}, headers=admin)
        assert selected.status_code == 409
        assert '真实验收' in json.dumps(selected.json(), ensure_ascii=False)
        accept_release(tmp_path / 'ledger.json', release)
        selected = client.post('/internal/v1/extensions/platform-selection', json={'dictionary_version': '1.1.0', 'extension_ids': [release['definition']['extension_id']]}, headers=admin)
        assert selected.status_code == 200, selected.text
        assert client.get('/v1/metrics').json() == original  # explicit publication hold survives both decisions
        assert client.post('/internal/v1/publication-process', headers=admin).status_code == 200
        current = client.get('/v1/metrics').json()
        assert len(current['items']) == 56
        original_by_id = {m['metric_id']: m for m in original['items']}
        assert {m['metric_id']: m for m in current['items'] if m['source_datapoint_id']} == original_by_id
        extension = next(m for m in current['items'] if m.get('clause_kind') == 'project_extension')
        assert extension['source_datapoint_id'] is None
        assert extension['related_source_datapoint_ids'] == ['E1-6_07', 'E1-6_09', 'E1-6_10']
        assert client.get('/v1/metrics', params={'dataset_version': original['dataset_version']}).json() == original
        assert client.get('/internal/v1/extensions/calculations', headers=reviewer).json()['items'] == []
        assert client.get('/internal/v1/publication-status', headers=admin).json()['derived_states'] == []
        # Registered projection is also the actual extraction planner input.
        coordinator = ReportSessionService.__new__(ReportSessionService)
        coordinator.settings = type('Settings', (), {'standard_dist_root': STANDARDS / 'releases',
            'qwen_model_path': Path.home() / 'Desktop/model/Qwen3.5-9B-4bit',
            'nuextract_model_path': Path.home() / 'Desktop/model/NuExtract',
            'qiniu_model': 'registered-cloud-model'})()
        coordinator.standards = StandardPackageCatalog(STANDARDS / 'releases')
        selection = {'extension_id': release['definition']['extension_id'], 'definition_version': release['definition']['definition_version'],
            'release_digest': release['release_digest'], 'value_origins': ['reported'], 'derivation_rule_ids': []}
        request = ReportExtractionRequest.model_validate({'input': {'kind': 'pdf', 'asset_id': 'pdf-isolated-planner'}, 'standards': [], 'extension_selections': [selection]})
        preview = coordinator.preview(request)
        assert preview['original_output_count'] == 0 and preview['extension_output_count'] == 1
        assert preview['internal_operand_count'] == 0
        assert preview['selected_outputs'][0]['source_datapoint_id'] is None
        assert draft['definition_digest'] == release['definition_digest']


def test_reported_extension_bundle_reaches_review_publication_and_public_source_without_inventing_method(tmp_path, monkeypatch):
    runtime = tmp_path / 'extensions'
    monkeypatch.setenv('ESG_EXTENSION_ROOT', str(runtime))
    app = create_app({'PLATESG_DATA_DIR': str(tmp_path / 'data'), 'PLATESG_REVIEW_TOKEN': 'admin-token',
        'PLATESG_AUTO_PUBLISH': '0', 'ESG_EXTENSION_ROOT': str(runtime), 'PLATESG_IMPORT_ROOTS': str(tmp_path),
        'ESG_CLAUSE_LIFECYCLE_LEDGER': str(tmp_path / 'ledger.json')})
    auth = {'Authorization': 'Bearer admin-token'}
    with TestClient(app) as client:
        proposal = json.loads(DRAFT.read_text())
        proposal.update(proposer_kind='human', proposer_ref='local-reviewer')
        assert client.post('/internal/v1/extensions/proposals', json=proposal, headers=auth).status_code == 200
        approved = client.post('/internal/v1/extensions/decisions', json={'proposal_id': proposal['proposal_id'], 'decision': 'approved', 'reason': '隔离合同回放：核对独立合计的定义和完整比较限制'}, headers=auth).json()
        registered = client.post('/internal/v1/extensions/register', json={'proposal_id': proposal['proposal_id'], 'approval_id': approved['approval']['approval_id']}, headers=auth)
        assert registered.status_code == 200, registered.text
        release = registered.json()['release']
        assert client.post('/internal/v1/extensions/platform-selection', json={'dictionary_version': '1.1.0', 'extension_ids': [release['definition']['extension_id']]}, headers=auth).status_code == 409
        accept_release(tmp_path / 'ledger.json', release)
        assert client.post('/internal/v1/extensions/platform-selection', json={'dictionary_version': '1.1.0', 'extension_ids': [release['definition']['extension_id']]}, headers=auth).status_code == 200
        assert client.post('/internal/v1/publication-process', headers=auth).status_code == 200
        assert client.post('/internal/v1/entities', json={'entity_ref': 'fixture-extension-entity', 'name_zh': '隔离虚构主体', 'entity_kind': 'group', 'country_code': 'CN', 'industry_code': 'fixture-industry'}, headers=auth).status_code == 200
        release_path = tmp_path / 'compiled-release.json'
        release_path.write_text(json.dumps(release, ensure_ascii=False))
        fixture_root = tmp_path / 'replay'
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT / 'ExtractESG/targeted_table_extract/backend'), str(ROOT / 'ExtractESG/targeted_table_extract/backend/src'), str(ROOT / 'ExtractESG/document_ir_extract/backend/src'), str(ROOT / 'ExtractESG/shared_runtime/src'), str(STANDARDS / 'src'), str(ROOT)])}
        built = subprocess.run([sys.executable, '-m', 'tests.extension_replay', '--release', str(release_path), '--output', str(fixture_root)], env=env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert built.returncode == 0, built.stderr[-4000:]
        replay = json.loads((fixture_root / 'replay.json').read_text())
        bundle_root = Path(replay['artifact_root'])
        body = {'bundle_path': str(bundle_root), 'report_metadata': {'upstream_system': 'synthetic-extension-contract', 'organization_ref': 'fixture-extension-entity',
            'report_ref': 'synthetic-extension-report', 'report_version_ref': 'synthetic-extension-report-v1',
            'source_artifact_ref': 'synthetic-extension-contract://report', 'source_sha256': hashlib.sha256(b'synthetic-extension-report').hexdigest(),
            'report_title': '隔离合成报告（非真实企业披露）', 'language_code': 'zh', 'report_period_start': '2024-01-01', 'report_period_end': '2024-12-31'}}
        imported = client.post('/internal/v1/import-batches', json=body, headers=auth)
        assert imported.status_code == 200, imported.text
        assert imported.json()['imported'] == 1
        assert imported.json()['selection_snapshot']['original_output_count'] == 0
        assert imported.json()['selection_snapshot']['extension_output_count'] == 1
        assert client.get('/v1/observations').json()['items'] == []
        candidate = client.get('/internal/v1/candidates', headers=auth).json()['items'][0]
        assert candidate['source_datapoint_id'] is None
        assert candidate['extension_id'] == release['definition']['extension_id']
        assert candidate['candidate_scope_role'] == 'selected_output'
        for identity in [{'disclosure_id': candidate['disclosure_id']}, {'mapping_id': candidate['mapping_id'], 'mapping_relation': 'exact', 'satisfaction_status': 'supports'}]:
            response = client.post('/internal/v1/reviews', json={**identity, 'operator_ref': 'ignored-request-actor', 'decision': 'approved', 'reason': '隔离回放核对原值30、单位、独立合计含义与物理来源'}, headers=auth)
            assert response.status_code == 200, response.text
        context = {'mapping_id': candidate['mapping_id'], 'entity_ref': 'fixture-extension-entity', 'period_start': '2024-01-01', 'period_end': '2024-12-31',
            'boundary_code': 'china_own_operations', 'value_role': 'actual', 'measurement_method': None, 'reported_unit': 'tCO2e',
            'dimensions': {'ghg_scope': 'scope_1_2', 'aggregation_role': 'total', 'entity_kind': 'group'}, 'operator_ref': 'ignored-request-actor',
            'reason': '仅确认fixture明示的期间、主体与中国境内运营；未知方法、毛净、GWP及合并依据保持缺失'}
        confirmed = client.post('/internal/v1/measurement-contexts', json=context, headers=auth)
        assert confirmed.status_code == 200, confirmed.text
        assert client.post('/internal/v1/publication-process', headers=auth).status_code == 200
        data = client.get('/v1/observations').json()
        assert len(data['items']) == 1, client.get('/internal/v1/candidates', headers=auth).json()
        row = data['items'][0]
        assert row['value_numeric'] == '30' and row['value_origin'] == 'reported'
        assert row['source_datapoint_id'] is None and row['comparison_eligibility'] == 'not_comparable'
        exported = client.get('/v1/exports', params={'format': 'csv', 'dataset_version': data['dataset_version']})
        assert exported.status_code == 200, exported.text
        csv_rows = list(csv.DictReader(io.StringIO(exported.text.removeprefix('\ufeff'))))
        assert len(csv_rows) == 1
        assert csv_rows[0]['clause_kind'] == 'project_extension'
        assert csv_rows[0]['extension_id'] == release['definition']['extension_id']
        assert csv_rows[0]['source_datapoint_id'] == ''
        assert json.loads(csv_rows[0]['related_source_datapoint_ids']) == ['E1-6_07', 'E1-6_09', 'E1-6_10']
        assert csv_rows[0]['value_origin'] == 'reported' and csv_rows[0]['value_numeric'] == '30'
        assert all(axis not in row['dimensions'] for axis in replay['unknown_axes'])
        detail = client.get(f"/v1/observations/{row['observation_id']}", params={'dataset_version': data['dataset_version']}).json()
        assert detail['sources'][0]['raw_value_text'] == '30tCO2e'
        assert detail['sources'][0]['raw_unit'] == 'tCO2e'
        assert detail['calculation'] is None and 'history' not in detail and 'review' not in detail
        assert client.post('/internal/v1/import-batches', json=body, headers=auth).json()['idempotent'] is True
        baseline = deepcopy(data)
        # Exact same row cannot enter scope1 or either scope2 original column.
        assert not {r['source_datapoint_id'] for r in data['items']} & {'E1-6_07', 'E1-6_09', 'E1-6_10'}
        assert client.get('/v1/observations', params={'dataset_version': data['dataset_version']}).json() == baseline


def test_actual_internal_operand_bundle_delivers_original_candidates_without_expanding_outputs_or_inventing_context(tmp_path, monkeypatch):
    runtime = tmp_path / 'extensions'
    monkeypatch.setenv('ESG_EXTENSION_ROOT', str(runtime))
    app = create_app({'PLATESG_DATA_DIR': str(tmp_path / 'data'), 'PLATESG_REVIEW_TOKEN': 'admin-token',
        'PLATESG_AUTO_PUBLISH': '0', 'ESG_EXTENSION_ROOT': str(runtime), 'PLATESG_IMPORT_ROOTS': str(tmp_path),
        'ESG_CLAUSE_LIFECYCLE_LEDGER': str(tmp_path / 'ledger.json')})
    auth = {'Authorization': 'Bearer admin-token'}
    with TestClient(app) as client:
        proposal = json.loads(DRAFT.read_text())
        proposal.update(proposer_kind='human', proposer_ref='local-reviewer')
        assert client.post('/internal/v1/extensions/proposals', json=proposal, headers=auth).status_code == 200
        approved = client.post('/internal/v1/extensions/decisions', json={'proposal_id': proposal['proposal_id'],
            'decision': 'approved', 'reason': '隔离内部操作数回放：批准定义，数值与完整科学口径继续独立审核'}, headers=auth)
        assert approved.status_code == 200, approved.text
        registered = client.post('/internal/v1/extensions/register', json={'proposal_id': proposal['proposal_id'],
            'approval_id': approved.json()['approval']['approval_id']}, headers=auth)
        assert registered.status_code == 200, registered.text
        release = registered.json()['release']
        extension_id = release['definition']['extension_id']
        accept_release(tmp_path / 'ledger.json', release)
        selected = client.post('/internal/v1/extensions/platform-selection', json={'dictionary_version': '1.1.0',
            'extension_ids': [extension_id]}, headers=auth)
        assert selected.status_code == 200, selected.text
        assert client.post('/internal/v1/publication-process', headers=auth).status_code == 200
        registered_entity = client.post('/internal/v1/entities', json={'entity_ref': 'fixture-internal-report-owner',
            'name_zh': '隔离虚构报告登记主体', 'entity_kind': 'group', 'country_code': 'CN',
            'industry_code': 'fixture-industry'}, headers=auth)
        assert registered_entity.status_code == 200, registered_entity.text
        release_path = tmp_path / 'compiled-release.json'
        release_path.write_text(json.dumps(release, ensure_ascii=False))
        fixture_root = tmp_path / 'internal-replay'
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT / 'ExtractESG/targeted_table_extract/backend'),
            str(ROOT / 'ExtractESG/targeted_table_extract/backend/src'), str(ROOT / 'ExtractESG/document_ir_extract/backend/src'),
            str(ROOT / 'ExtractESG/shared_runtime/src'), str(STANDARDS / 'src'), str(ROOT)])}
        built = subprocess.run([sys.executable, '-m', 'tests.extension_replay', '--kind', 'internal',
            '--release', str(release_path), '--output', str(fixture_root)], env=env, cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        assert built.returncode == 0, built.stderr[-4000:]
        replay = json.loads((fixture_root / 'replay.json').read_text())
        bundle_root = Path(replay['artifact_root'])
        assert replay['fixture_kind'] == 'synthetic_offline_internal_operand_delivery'
        assert replay['original_output_count'] == 0 and replay['extension_output_count'] == 1
        assert sorted(replay['semantic_metric_calls']) == ['esrs.2023-set1.e1-6.dp07', 'esrs.2023-set1.e1-6.dp09']
        assert not (bundle_root / 'results/quantitative-observations.jsonl').read_text().strip()
        internal = json.loads((bundle_root / 'results/internal-dependencies.json').read_text())
        assert internal['schema_version'] == 'internal-dependency-facts-v2'
        assert internal['selected_output_metric_ids'] == [extension_id]
        assert set(internal['source_datapoint_ids']) == {'E1-6_07', 'E1-6_09'}
        assert len(internal['records']['quantitative_observations']) == 2
        body = {'bundle_path': str(bundle_root), 'report_metadata': {'upstream_system': 'synthetic-internal-contract',
            'organization_ref': 'fixture-internal-report-owner', 'report_ref': 'synthetic-internal-report',
            'report_version_ref': 'synthetic-internal-report-v1', 'source_artifact_ref': 'synthetic-internal-contract://report',
            'source_sha256': hashlib.sha256(b'synthetic-internal-report').hexdigest(),
            'report_title': '隔离内部操作数样本（非真实企业披露）', 'language_code': 'zh'}}
        imported = client.post('/internal/v1/import-batches', json=body, headers=auth)
        assert imported.status_code == 200, imported.text
        assert imported.json()['imported'] == 2
        scope = imported.json()['selection_snapshot']
        assert scope['source_datapoint_ids'] == []
        assert scope['original_output_count'] == 0 and scope['extension_output_count'] == 1
        assert set(scope['internal_dependency_scope']['source_datapoint_ids']) == {'E1-6_07', 'E1-6_09'}
        assert scope['internal_dependency_scope']['output_policy'] == 'excluded_from_selected_output_and_coverage'
        candidates = client.get('/internal/v1/candidates', params={'report_ref': 'synthetic-internal-report'}, headers=auth).json()['items']
        assert len(candidates) == 2
        assert {c['source_datapoint_id']: c['numeric_value'] for c in candidates} == replay['expected_internal_values']
        before = client.get('/v1/observations').json()
        assert before['items'] == []
        for candidate in candidates:
            assert candidate['clause_kind'] == 'original_datapoint' and candidate['extension_id'] is None
            assert candidate['candidate_scope_role'] == 'internal_dependency'
            assert candidate['required_by_extension_ids'] == [extension_id]
            assert candidate['fact_review_status'] == 'candidate' and candidate['mapping_review_status'] == 'review_required'
            assert candidate['context'] is None
            raw_identity = candidate['source_identity']['identity_raw']
            assert all(raw_identity.get(axis) in (None, 'unknown', 'unspecified') for axis in replay['unknown_axes'])
            for identity in [{'disclosure_id': candidate['disclosure_id']}, {'mapping_id': candidate['mapping_id'],
                'mapping_relation': 'exact', 'satisfaction_status': 'supports'}]:
                reviewed = client.post('/internal/v1/reviews', json={**identity, 'decision': 'approved',
                    'reason': '仅核对合成样本的原值、条款映射与证据；科学身份和方法没有得到确认'}, headers=auth)
                assert reviewed.status_code == 200, reviewed.text
        assert client.post('/internal/v1/publication-process', headers=auth).status_code == 200
        assert client.get('/v1/observations').json() == before
        after = client.get('/internal/v1/candidates', params={'report_ref': 'synthetic-internal-report'}, headers=auth).json()['items']
        assert all(c['context'] is None and 'reviewed_context_missing' in c['reason_codes'] for c in after)
        assert client.get('/internal/v1/extensions/calculations', headers=auth).json()['items'] == []
        assert client.get('/internal/v1/publication-status', headers=auth).json()['derived_states'] == []
        assert client.post('/internal/v1/import-batches', json=body, headers=auth).json()['idempotent'] is True
        assert len(client.get('/internal/v1/candidates', params={'report_ref': 'synthetic-internal-report'}, headers=auth).json()['items']) == 2
