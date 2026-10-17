"""Server-owned frozen identities for a read-only SQL delivery projection."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import re


COUNT_KEYS = ('archived_source_count', 'selected_candidate_count',
              'internal_candidate_count', 'published_observation_count')
STATUS_CODES = frozenset({'source_archived', 'candidates_imported', 'partial', 'not_imported',
    'no_artifact', 'committed_without_acknowledgement', 'failed', 'unknown',
    'identity_mismatch', 'artifact_unavailable'})
REASONS = {
    'session_not_found': '未找到明确绑定的报告提取会话，尚不能核对 SQL 交付状态',
    'session_binding_ambiguous': '报告会话存在多个绑定，需先核对固定身份',
    'native_session_unavailable': '提取会话暂不可读，已存档状态尚不能刷新',
    'native_session_identity_missing': '原生报告会话身份尚未固定',
    'native_session_identity_mismatch': '报告会话与原生会话身份不一致',
    'fixed_evidence_unavailable': '固定结构来源暂不可读，尚不能核对交付状态',
    'fixed_evidence_identity_mismatch': '固定报告、PDF 或结构来源身份不一致',
    'report_selection_required': '同一 PDF 对应多个登记版本，请明确选择报告',
    'report_identity_not_registered': '此 PDF 尚无明确的登记报告身份',
    'report_version_ambiguous': '所选报告仍有多个匹配的来源版本，尚不能确定版本',
    'report_identity_incomplete': '登记报告身份未完整确认，尚不能核对交付状态',
    'original_child_scope_unavailable': '原始子任务范围尚未完整固定',
    'artifact_scope_mismatch': '冻结成果不属于本次原始子任务或授权路径',
    'artifact_revision_ambiguous': '同一子任务有无法排序的成果版本，需核对明确版本',
    'publication_unavailable': '发布数据库暂不可读，SQL 交付状态尚不能刷新',
    'delivery_projection_invalid': 'SQL 状态响应未满足固定会话合同，尚不能确认',
    'delivery_projection_identity_mismatch': 'SQL 状态响应对应其他报告或原生会话',
}


class DeliveryScopeUnavailable(ValueError):
    def __init__(self, code, *, execution=None, report_options=None, status='unknown'):
        super().__init__(REASONS.get(code, '交付状态尚需核对'))
        self.code, self.execution, self.report_options, self.status = code, execution, report_options, status


def null_counts():
    return dict.fromkeys(COUNT_KEYS)


def unknown_delivery(session_id, code, *, execution=None, report_options=None, status='unknown'):
    label = REASONS.get(code, '交付状态尚需核对')
    return {'schema_version': 'delivery-status-v1', 'session_id': session_id,
        'status': status, 'available': False, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'identity_basis': 'unknown', 'native_session_id': None, 'report_ref': None,
        'report_counts_scope': 'fixed_report_version', 'publication_dataset_version': None,
        'report_counts': null_counts(), 'session_counts': null_counts(), 'current_revision_counts': null_counts(),
        'reason_codes': [code], 'reason_messages': [{'code': code, 'label_zh': label}],
        'blockers': [{'code': code, 'message': label}], 'jobs': [],
        'execution': execution, 'report_options': report_options or []}


def _nonnegative(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def execution_projection(session):
    """Native stage counts are execution facts, never SQL/import/publication counts."""
    stages = {s.get('job_id'): s for s in session.get('stages', [])}
    artifacts = session.get('delivery_artifacts') or []
    blocked = {a.get('job_id') for a in session.get('delivery_artifact_blockers') or []}
    jobs = []
    for job in session.get('jobs', []):
        stage = stages.get(job.get('job_id'), {})
        summary = job.get('summary') if isinstance(job.get('summary'), dict) else {}
        counts = stage.get('counts') or summary.get('counts') or {}
        if not isinstance(counts, dict):
            counts = {}
        request = job.get('request') if isinstance(job.get('request'), dict) else {}
        available = any(a.get('job_id') == job.get('job_id') and a.get('status') == 'available' for a in artifacts)
        jobs.append({'job_id': job.get('job_id'), 'package_id': request.get('package_id'),
            'status': job.get('status'), 'recorded_status': job.get('recorded_status'),
            **{key: _nonnegative(counts.get(key)) for key in ('selected_metrics', 'attempted_metrics', 'not_executed_metrics')},
            'artifact_state': 'available' if available else 'unavailable' if job.get('job_id') in blocked else 'not_saved'})
    return {'status': session.get('execution_status'), 'native_status': session.get('native_execution_status'),
        'result_status': session.get('result_status'), 'legacy_database_status': session.get('database_status'),
        'database_status_authoritative': False, 'jobs': jobs}


def _selected_artifacts(session, job_ids, root, execution):
    grouped = {job_id: [] for job_id in job_ids}
    artifacts = session.get('delivery_artifacts')
    if artifacts is None:
        # Old services expose only the original output directory. Platform will
        # validate the actual Bundle and all source/parent bindings again.
        artifacts = []
        for job_id in job_ids:
            job_root = (root / job_id).resolve()
            if job_root.parent != root:
                raise DeliveryScopeUnavailable('artifact_scope_mismatch', execution=execution, status='identity_mismatch')
            manifest = job_root / 'manifest.json'
            if manifest.is_file() and (job_root / 'exports/extraction-result.json').is_file():
                artifacts.append({'job_id': job_id, 'status': 'available', 'artifact_revision_id': 'original',
                    'bundle_relative_path': '.', 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()})
    for item in artifacts:
        if item.get('job_id') not in grouped:
            raise DeliveryScopeUnavailable('artifact_scope_mismatch', execution=execution, status='identity_mismatch')
        if item.get('status') == 'available':
            grouped[item['job_id']].append(item)
    selected = []
    for job_id, choices in grouped.items():
        if not choices:
            continue
        if len(choices) == 1:
            choice = choices[0]
        else:
            try:
                dated = [(datetime.fromisoformat(c['created_at'].replace('Z', '+00:00')), c) for c in choices]
                if any(when.tzinfo is None for when, _ in dated):
                    raise ValueError('timezone missing')
                latest = max(when for when, _ in dated)
                newest = [c for when, c in dated if when == latest]
                if len(newest) != 1:
                    raise ValueError('ambiguous current revision')
                choice = newest[0]
            except (KeyError, TypeError, ValueError, AttributeError):
                raise DeliveryScopeUnavailable('artifact_revision_ambiguous', execution=execution) from None
        revision = choice.get('artifact_revision_id')
        relative = choice.get('bundle_relative_path')
        valid_revision = revision == 'original' or (isinstance(revision, str) and re.fullmatch(r'partial-[a-f0-9]{32}', revision))
        expected_path = '.' if revision == 'original' else f'terminal-revisions/{revision}'
        digest = choice.get('manifest_sha256')
        job_root = (root / job_id).resolve()
        bundle = (job_root / str(relative)).resolve()
        valid_path = job_root.parent == root and (bundle == job_root if revision == 'original' else
            bundle.parent == job_root / 'terminal-revisions' and bundle.name == revision)
        if not valid_revision or relative != expected_path or not valid_path or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise DeliveryScopeUnavailable('artifact_scope_mismatch', execution=execution, status='identity_mismatch')
        selected.append({'job_id': job_id, 'artifact_revision_id': revision,
                         'bundle_path': str(bundle), 'manifest_sha256': digest})
    return selected


def resolve_delivery_query(processing, plans, session_detail, root, session_id, report_id=None):
    matches = [p for p in plans.list() if p.get('report_request') and
        (p.get('plan_id') == session_id or any(d.get('native_session_id') == session_id for d in p.get('documents', [])))]
    if len(matches) != 1:
        raise DeliveryScopeUnavailable('session_binding_ambiguous' if matches else 'session_not_found')
    plan = matches[0]
    if len(plan.get('documents', [])) != 1:
        raise DeliveryScopeUnavailable('session_binding_ambiguous')
    document = plan['documents'][0]
    session = session_detail(plan)
    if not isinstance(session, dict) or any(not isinstance(session.get(key, []), list) or
        any(not isinstance(row, dict) for row in session.get(key, [])) for key in
        ('jobs', 'stages', 'delivery_artifacts', 'delivery_artifact_blockers') if session.get(key) is not None):
        raise DeliveryScopeUnavailable('native_session_unavailable')
    execution = execution_projection(session)
    if session.get('native_unavailable'):
        raise DeliveryScopeUnavailable('native_session_unavailable', execution=execution)
    native = document.get('native_session_id')
    if not isinstance(native, str) or not re.fullmatch(r'report-[a-f0-9]{32}', native):
        raise DeliveryScopeUnavailable('native_session_identity_missing', execution=execution)
    if session.get('native_session_id') not in {None, native} or session.get('session_id') not in {None, native, plan['plan_id']}:
        raise DeliveryScopeUnavailable('native_session_identity_mismatch', execution=execution, status='identity_mismatch')
    ir_run_id = document.get('ir_run_id')
    if not ir_run_id:
        raise DeliveryScopeUnavailable('fixed_evidence_unavailable', execution=execution)
    try:
        manifest, _ = processing._manifest('ir', ir_run_id)
        processing.integrity_check('ir', ir_run_id)
    except (OSError, ValueError, KeyError):
        raise DeliveryScopeUnavailable('fixed_evidence_unavailable', execution=execution) from None
    if not isinstance(manifest, dict):
        raise DeliveryScopeUnavailable('fixed_evidence_unavailable', execution=execution)
    source = manifest.get('source')
    source_sha = source.get('pdf_sha256') if isinstance(source, dict) else None
    document_id = manifest.get('document_id')
    revision = manifest.get('ir_revision')
    if manifest.get('run_id') != ir_run_id or not isinstance(source_sha, str) or not re.fullmatch(r'[a-f0-9]{64}', source_sha) or not isinstance(document_id, str) or not document_id.strip() or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or document.get('source_sha256') not in {None, source_sha} or document.get('document_id') not in {None, document_id}:
        raise DeliveryScopeUnavailable('fixed_evidence_identity_mismatch', execution=execution, status='identity_mismatch')
    # Do not use asset_summary(): its old lookup chooses the first same-PDF row.
    try:
        registered = [processing.report(report_id)] if report_id else processing.reports()
    except KeyError:
        raise DeliveryScopeUnavailable('report_identity_not_registered', execution=execution) from None
    if not isinstance(registered, list) or any(not isinstance(report, dict) for report in registered):
        raise DeliveryScopeUnavailable('report_identity_not_registered', execution=execution)
    if any(not isinstance(report.get('metadata') or {}, dict) for report in registered):
        raise DeliveryScopeUnavailable('report_identity_incomplete', execution=execution)
    if any(not isinstance(report.get('asset_versions', []), list) or
           any(not isinstance(version, dict) for version in report.get('asset_versions', [])) for report in registered):
        raise DeliveryScopeUnavailable('report_identity_incomplete', execution=execution)
    candidates = [(report, version) for report in registered for version in report.get('asset_versions', [])
                  if version.get('source_sha256') == source_sha]
    options = [{'report_id': r['report_id'], 'title': (r.get('metadata') or {}).get('title'), 'asset_version': v.get('version')}
               for r, v in candidates]
    if not candidates:
        raise DeliveryScopeUnavailable('fixed_evidence_identity_mismatch' if report_id else 'report_identity_not_registered',
            execution=execution, status='identity_mismatch' if report_id else 'unknown')
    if not report_id and len(candidates) != 1:
        raise DeliveryScopeUnavailable('report_selection_required', execution=execution, report_options=options)
    if len(candidates) != 1:
        raise DeliveryScopeUnavailable('report_version_ambiguous', execution=execution, report_options=options)
    report, asset_version = candidates[0]
    metadata = report.get('metadata') or {}
    if not all(metadata.get(key) for key in ('title', 'publisher_entity_ref', 'period_start', 'period_end')):
        raise DeliveryScopeUnavailable('report_identity_incomplete', execution=execution)
    try:
        if date.fromisoformat(metadata['period_start']) > date.fromisoformat(metadata['period_end']):
            raise ValueError('invalid reporting period')
        source_asset = processing.assets.get(asset_version['asset_id'])
    except (KeyError, TypeError, ValueError):
        raise DeliveryScopeUnavailable('report_identity_incomplete', execution=execution) from None
    version_number = asset_version.get('version')
    if source_asset.get('sha256') != source_sha or not isinstance(version_number, int) or isinstance(version_number, bool) or version_number < 1:
        raise DeliveryScopeUnavailable('fixed_evidence_identity_mismatch', execution=execution, status='identity_mismatch')
    jobs = session.get('jobs', [])
    job_ids = [j.get('job_id') for j in jobs]
    if not job_ids or len(job_ids) > 100 or len(set(job_ids)) != len(job_ids) or any(not isinstance(j, str) or not re.fullmatch(r'tx-[a-zA-Z0-9._-]{1,150}', j) for j in job_ids) or (session.get('job_ids') is not None and set(session['job_ids']) != set(job_ids)):
        raise DeliveryScopeUnavailable('original_child_scope_unavailable', execution=execution)
    if any(not isinstance(j.get('request') or {}, dict) or
           (j.get('request') or {}).get('report_session_id') not in {None, native} for j in jobs):
        raise DeliveryScopeUnavailable('native_session_identity_mismatch', execution=execution, status='identity_mismatch')
    artifacts = _selected_artifacts(session, job_ids, root, execution)
    payload = {'native_session_id': native,
        'report_identity': {'upstream_system': 'ExtractESG', 'organization_ref': metadata['publisher_entity_ref'],
            'report_ref': report['report_id'], 'report_version_ref': f"{report['report_id']}:asset:{version_number}",
            'source_artifact_ref': asset_version['asset_id'], 'source_sha256': source_sha,
            'report_period_start': metadata['period_start'], 'report_period_end': metadata['period_end']},
        'fixed_source': {'document_id': document_id, 'ir_run_id': ir_run_id, 'ir_revision': revision},
        'job_ids': job_ids, 'artifacts': artifacts}
    return payload, {'session_id': session_id, 'plan_id': plan['plan_id'], 'execution': execution,
                     'report_options': options}


def validate_sql_projection(result, payload):
    if not isinstance(result, dict):
        raise DeliveryScopeUnavailable('delivery_projection_invalid')
    if result.get('native_session_id') != payload['native_session_id'] or result.get('report_ref') != payload['report_identity']['report_ref']:
        raise DeliveryScopeUnavailable('delivery_projection_identity_mismatch', status='identity_mismatch')
    if result.get('schema_version') != 'delivery-status-v1' or result.get('status') not in STATUS_CODES:
        raise DeliveryScopeUnavailable('delivery_projection_invalid')
    if result.get('report_counts_scope') != 'fixed_report_version' or (
        result.get('publication_dataset_version') is not None and _nonnegative(result['publication_dataset_version']) is None):
        raise DeliveryScopeUnavailable('delivery_projection_invalid')
    for key in ('report_counts', 'session_counts', 'current_revision_counts'):
        counts = result.get(key)
        if not isinstance(counts, dict) or any(name not in counts or (counts[name] is not None and _nonnegative(counts[name]) is None) for name in COUNT_KEYS):
            raise DeliveryScopeUnavailable('delivery_projection_invalid')
    jobs = result.get('jobs')
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs) or {j.get('job_id') for j in jobs} != set(payload['job_ids']) or len(jobs) != len(payload['job_ids']):
        raise DeliveryScopeUnavailable('delivery_projection_invalid')
    return result
