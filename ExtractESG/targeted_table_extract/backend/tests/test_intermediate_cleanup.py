"""Synthetic files/fake ledgers only: no real report, SQL service or models."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from esg_runtime.lifecycle import LifecycleBlocked
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.intermediate_cleanup import IntermediateCleanup, _digest, assert_intermediates_restored


class Guard:
    def __init__(self):
        self.scopes = []
        self.verified = True

    def check(self, scope):
        self.scopes.append(scope)
        return {'schema_version': 'lifecycle-references-v1', 'verified': self.verified,
            'blockers': [{'code': 'published_source_reference', 'message': '合成已存档来源受保护'}], 'dependencies': []}


def put(root, relative, value):
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value, ensure_ascii=False))
    return file


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def fixture(tmp_path):
    output = tmp_path / 'output'
    artifacts = ArtifactStore(output)
    sid = 'report-' + 'a' * 32
    ids = ['tx-synthetic-main', 'tx-synthetic-unexecuted']
    roots = [artifacts.job_dir(identity) for identity in ids]
    shared = artifacts.report_session_dir(sid)
    state = {'session_id': sid, 'execution_status': 'failed', 'revision': 8,
        'lifecycle': {'state': 'archived', 'revision': 2}, 'projections': [{'job_id': job} for job in ids]}
    records = {identity: SimpleNamespace(job_id=identity, status=SimpleNamespace(value='failed'),
        request=SimpleNamespace(report_session_id=sid, ir_run_id='ir-synthetic')) for identity in ids}
    sql_commands = []

    @contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda sql: sql_commands.append(sql))

    guard = Guard()
    lifecycle = SimpleNamespace(artifacts=artifacts, _lock=threading.RLock(), guard=guard,
        jobs=SimpleNamespace(get=records.__getitem__, _connect=connection),
        sessions=SimpleNamespace(_state=lambda _: state), _idle_blockers=lambda *_: [],
        _scope=lambda jobs: {'job_ids': [job.job_id for job in jobs], 'ir_run_ids': ['ir-synthetic']})
    put(shared, 'request.json', {'input': {'ir_run_id': 'ir-synthetic'}, 'packages': ['E1-5', 'E1-6']})
    for root in roots:
        put(root, 'input/request.json', {'report_session_id': sid})
        put(root, 'config.json', {'fixture': True})
    root = roots[0]
    put(root, 'agent/calls/0001.json', {'diagnostic': 'preserved by decision'})
    put(root, 'agent/calls/0002.json', {'diagnostic': 'unreferenced diagnostic'})
    put(root, 'packets/a.agent.json', {'synthetic': True})
    put(root, 'packets/b.agent.json', {'synthetic': 'frozen durable reference'})
    put(root, 'packets/a.regions.json', {'freshness': True})
    put(root, 'agent/state.json', {'phase': 'failed'})
    put(root, 'decisions/a.json', {'attempts': [{'artifact': 'agent/calls/0001.json'}]})
    put(root, 'retrieval/semantic-index.json', {'external_cache': '/unowned/cache/never/touched'})
    put(root, 'retrieval/inventory.json', {'retain': True})
    put(root, 'guards/a.json', {'retain': True})
    put(root, 'results/source-facts.json', {'facts': [{'identity_raw': {}, 'quality_status': 'needs_confirmation'}]})
    return SimpleNamespace(output=output, artifacts=artifacts, sid=sid, roots=roots, state=state,
        lifecycle=lifecycle, cleaner=IntermediateCleanup(lifecycle), guard=guard, records=records, sql_commands=sql_commands)


def terminal_proof(f):
    original = f.roots[0]
    shared = f.artifacts.report_session_path(f.sid)
    durable = {'job:packets/b.agent.json': {'role': 'durable_projection', 'sha256': sha(original / 'packets/b.agent.json')},
        'job:decisions/a.json': {'role': 'projection_freshness', 'sha256': sha(original / 'decisions/a.json')}}
    inputs = {'input/request.json': sha(original / 'input/request.json')}
    basis = {'durable_inputs': durable, 'inputs': inputs, 'master_scope': json.loads((shared / 'request.json').read_text())}
    basis_bytes = json.dumps(basis, ensure_ascii=False).encode()
    digest = hashlib.sha256(basis_bytes).hexdigest()
    revision = original / ('terminal-revisions/partial-' + digest[:32])
    put(revision, 'input/materialization-basis.json', basis)
    put(revision, 'manifest.json', {'synthetic': 'frozen', 'job_id': original.name,
        'artifact_revision': {'revision_id': revision.name, 'materialization_digest': digest}})
    proof = {'schema_version': 'terminal-result-revision-v1', 'job_id': original.name,
        'revision_id': revision.name, 'materialization_digest': digest,
        'artifact_sha256': {p.relative_to(revision).as_posix(): sha(p) for p in revision.rglob('*') if p.is_file()},
        'frozen_input_sha256': inputs, 'durable_fragment_inputs': durable,
        'frozen_master_request': {'session_id': f.sid, 'relative_path': f'.report-sessions/{f.sid}/request.json',
            'file_sha256': sha(shared / 'request.json'), 'canonical_sha256': _digest(basis['master_scope'])}}
    return put(revision, 'artifact-revision.json', proof)


def inventory(f):
    return {str(p.relative_to(f.output)): sha(p) for root in [*f.roots, f.artifacts.report_session_path(f.sid)] for p in root.rglob('*') if p.is_file()}


def execute(f):
    plan = f.cleaner.plan(f.sid)
    return f.cleaner.execute(f.sid, plan_id=plan.plan_id, expected_revision=2,
        reason='合成可恢复缓存清理', operator_ref='synthetic-admin')


def test_plan_is_read_only_and_keeps_full_guard_scope_and_literal_call_references(tmp_path):
    f = fixture(tmp_path); terminal_proof(f); before = inventory(f)
    plan = f.cleaner.plan(f.sid)
    assert inventory(f) == before and not (f.output / '.lifecycle').exists()
    assert plan.calls_model is False and plan.permanent_deletion is False
    assert f.guard.scopes == [{'job_ids': list(f.records), 'ir_run_ids': ['ir-synthetic']}]
    assert plan.reference_check['blockers'][0]['code'] == 'published_source_reference'
    paths = {item.relative_path for item in plan.candidates}
    assert paths == {'agent/calls/0002.json', 'packets/a.agent.json', 'retrieval/semantic-index.json'}
    assert any(row['path'].endswith('agent/calls/0001.json') and any('decisions/a.json' in r for r in row['referrers']) for row in plan.excluded)
    assert any(row['path'].endswith('packets/b.agent.json') for row in plan.excluded)
    assert all(item.job_id != f.roots[1].name for item in plan.candidates)


@pytest.mark.parametrize('change', ['active', 'not_terminal', 'live_worker', 'scope_empty', 'foreign_child', 'guard_offline'])
def test_plan_rejects_unowned_nonterminal_or_unverified_scope(tmp_path, change):
    f = fixture(tmp_path)
    if change == 'active': f.state['lifecycle']['state'] = 'active'
    elif change == 'not_terminal': f.state['execution_status'] = 'running'
    elif change == 'live_worker': f.lifecycle._idle_blockers = lambda *_: [{'code': 'worker_active', 'message': '合成活动worker'}]
    elif change == 'scope_empty': f.state['projections'] = []
    elif change == 'foreign_child': f.records[f.roots[0].name].request.report_session_id = 'other'
    else: f.guard.verified = False
    with pytest.raises(LifecycleBlocked): f.cleaner.plan(f.sid)
    assert not (f.output / '.lifecycle').exists()


@pytest.mark.parametrize('change', ['candidate', 'preserved', 'lifecycle_revision', 'parent_revision', 'new_reference', 'symlink'])
def test_execute_requires_exact_plan_hash_and_expected_revision(tmp_path, change):
    f = fixture(tmp_path); plan = f.cleaner.plan(f.sid)
    if change == 'candidate': put(f.roots[0], 'packets/a.agent.json', {'changed': True})
    elif change == 'preserved': put(f.roots[0], 'guards/a.json', {'changed': True})
    elif change == 'lifecycle_revision': f.state['lifecycle']['revision'] += 1
    elif change == 'parent_revision': f.state['revision'] += 1
    elif change == 'new_reference': put(f.roots[0], 'decisions/new.json', {'artifact': 'packets/a.agent.json'})
    else:
        target = f.roots[0] / 'packets/a.agent.json'; target.unlink(); target.symlink_to(f.roots[0] / 'config.json')
    with pytest.raises(LifecycleBlocked):
        f.cleaner.execute(f.sid, plan_id=plan.plan_id, expected_revision=2, reason='过期计划', operator_ref='synthetic-admin')
    assert not list((f.output / '.lifecycle/intermediates/receipts').glob('*.json'))


def test_quarantine_restore_keeps_all_source_bytes_and_blocks_visibility_restore(tmp_path):
    f = fixture(tmp_path); terminal_proof(f); before = inventory(f)
    receipt = execute(f)
    assert receipt['state'] == 'quarantined'
    assert f.sql_commands == ['BEGIN IMMEDIATE']
    assert not (f.roots[0] / 'packets/a.agent.json').exists()
    assert (f.roots[0] / 'agent/calls/0001.json').is_file()
    with pytest.raises(LifecycleBlocked, match='先恢复中间文件'):
        assert_intermediates_restored(f.output, f.sid)
    # An unrelated parent does not inherit this receipt's blockers.
    assert_intermediates_restored(f.output, 'report-other')
    result = IntermediateCleanup(f.lifecycle).restore(f.sid, cleanup_id=receipt['cleanup_id'],
        expected_revision=2, reason='恢复合成缓存', operator_ref='synthetic-admin')
    assert result['state'] == 'restored' and inventory(f) == before
    assert_intermediates_restored(f.output, f.sid)
    assert f.cleaner.restore(f.sid, cleanup_id=receipt['cleanup_id'], expected_revision=2,
        reason='重复恢复', operator_ref='synthetic-admin')['state'] == 'restored'


@pytest.mark.parametrize('crash', [False, True])
def test_partial_move_failure_rolls_back_and_crash_can_restore_from_journal(tmp_path, monkeypatch, crash):
    f = fixture(tmp_path); before = inventory(f); move = f.cleaner._move; calls = 0
    def fail(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            if crash: raise SystemExit('synthetic process crash')
            raise OSError('synthetic move failure')
        move(source, destination)
    monkeypatch.setattr(f.cleaner, '_move', fail)
    with pytest.raises(SystemExit if crash else OSError): execute(f)
    receipt_path = next((f.output / '.lifecycle/intermediates/receipts').glob('*.json'))
    receipt = json.loads(receipt_path.read_text())
    assert receipt['state'] == ('moving' if crash else 'rolled_back')
    if crash:
        assert_intermediates_restored_exception = pytest.raises(LifecycleBlocked)
        with assert_intermediates_restored_exception: assert_intermediates_restored(f.output, f.sid)
        IntermediateCleanup(f.lifecycle).restore(f.sid, cleanup_id=receipt['cleanup_id'], expected_revision=2,
            reason='恢复合成中断', operator_ref='synthetic-admin')
    assert inventory(f) == before


@pytest.mark.parametrize('change', ['conflict', 'tamper', 'protected_change', 'wrong_session'])
def test_restore_never_overwrites_or_accepts_tampered_recycle_content(tmp_path, change):
    f = fixture(tmp_path); receipt = execute(f)
    entry = receipt['plan']['candidates'][0]
    source, destination = f.cleaner._paths(entry, receipt['cleanup_id'])
    if change == 'conflict': source.write_text('new unrelated bytes')
    elif change == 'tamper': destination.write_text('changed quarantine bytes')
    elif change == 'protected_change': put(f.roots[0], 'guards/a.json', {'changed': True})
    else: f.state['session_id'] = 'report-other'
    with pytest.raises(LifecycleBlocked):
        f.cleaner.restore('report-other' if change == 'wrong_session' else f.sid, cleanup_id=receipt['cleanup_id'],
            expected_revision=2, reason='拒绝覆盖', operator_ref='synthetic-admin')


@pytest.mark.parametrize('change', ['unknown_role', 'digest', 'missing', 'basis_mismatch', 'master_mismatch'])
def test_frozen_proof_must_be_complete_and_unchanged(tmp_path, change):
    f = fixture(tmp_path); proof_file = terminal_proof(f); proof = json.loads(proof_file.read_text())
    if change == 'unknown_role': proof['durable_fragment_inputs']['job:packets/b.agent.json']['role'] = 'unknown_future_role'
    elif change == 'digest': proof['artifact_sha256']['manifest.json'] = '0' * 64
    elif change == 'missing': (f.roots[0] / 'packets/b.agent.json').unlink()
    elif change == 'basis_mismatch': proof['durable_fragment_inputs'] = {}
    else: proof['frozen_master_request']['session_id'] = 'other'
    proof_file.write_text(json.dumps(proof))
    with pytest.raises(LifecycleBlocked): f.cleaner.plan(f.sid)


def test_transitive_durable_reference_and_recovery_plan_tamper_are_protected(tmp_path):
    f = fixture(tmp_path)
    put(f.roots[0], 'packets/b.agent.json', {'artifact': 'agent/calls/0002.json'})
    terminal_proof(f)
    plan = f.cleaner.plan(f.sid)
    assert 'agent/calls/0002.json' not in {entry.relative_path for entry in plan.candidates}
    receipt = execute(f)
    path = f.cleaner._receipt_path(receipt['cleanup_id'])
    modified = json.loads(path.read_text())
    modified['plan']['session_revision'] += 1
    path.write_text(json.dumps(modified))
    with pytest.raises(LifecycleBlocked, match='固定计划'):
        f.cleaner.restore(f.sid, cleanup_id=receipt['cleanup_id'], expected_revision=2,
            reason='拒绝篡改回收计划', operator_ref='synthetic-admin')


@pytest.mark.parametrize('relative', ['agent/calls/0002.json', 'packets/a.agent.json'])
def test_preserved_json_dictionary_key_is_a_reference_not_a_cleanup_candidate(tmp_path, relative):
    f = fixture(tmp_path)
    put(f.roots[0], 'guards/key-reference.json', {'index': {relative: {'diagnostic': 'retained key reference'}}})
    plan = f.cleaner.plan(f.sid)
    assert relative not in {entry.relative_path for entry in plan.candidates}
    assert any(row['path'].endswith(relative) and 'tx-synthetic-main/guards/key-reference.json' in row['referrers'] for row in plan.excluded)
    receipt = execute(f)
    assert (f.roots[0] / relative).is_file()
    f.cleaner.restore(f.sid, cleanup_id=receipt['cleanup_id'], expected_revision=2,
        reason='恢复合成key闭包检查', operator_ref='synthetic-admin')


def test_durable_mkdir_syncs_every_new_child_and_parent_before_source_move(tmp_path, monkeypatch):
    from esg_targeted.storage import intermediate_cleanup as module
    events = []
    sync = module._fsync_dir
    def synced(directory):
        events.append(Path(directory)); sync(directory)
    monkeypatch.setattr(module, '_fsync_dir', synced)
    root = tmp_path / 'new-receipts'
    module._write(root / 'journal.json', {'synthetic': True})
    assert events[:2] == [root, tmp_path]
    assert events[-1] == root
    source = tmp_path / 'source.json'; source.write_text('synthetic bytes')
    destination = tmp_path / 'content/cleanup/job/packets/packet.agent.json'
    events.clear()
    module.IntermediateCleanup._move(source, destination)
    expected = []
    for folder in ['content', 'content/cleanup', 'content/cleanup/job', 'content/cleanup/job/packets']:
        child = tmp_path / folder
        expected.extend([child, child.parent])
    assert events == expected + [destination.parent, source.parent]
    assert destination.read_text() == 'synthetic bytes' and not source.exists()


def test_durable_mkdir_refuses_symlink_ancestors_without_outside_write(tmp_path):
    from esg_targeted.storage.intermediate_cleanup import _write, IntermediateCleanup
    outside = tmp_path / 'outside'; outside.mkdir()
    linked = tmp_path / 'linked'; linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LifecycleBlocked, match='符号链接'):
        _write(linked / 'receipts/journal.json', {'synthetic': True})
    source = tmp_path / 'source.json'; source.write_text('synthetic bytes')
    with pytest.raises(LifecycleBlocked, match='符号链接'):
        IntermediateCleanup._move(source, linked / 'content/job/cache.json')
    assert source.is_file() and list(outside.iterdir()) == []
