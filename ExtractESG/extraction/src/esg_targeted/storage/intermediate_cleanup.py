"""Narrow, reversible cleanup of unreferenced intermediates, never sources.

The publication guard receives the complete original scope. SQL reference
blockers remain visible: only files outside the preserved artifact/reference
closure may move. No reservation, import, source deletion or purge occurs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.lifecycle import LifecycleBlocked, blocker, require_reason

SCHEMA = 'intermediate-cleanup-plan-v1'
JOURNAL_SCHEMA = 'recoverable-intermediates-v1'
TERMINAL = {'completed', 'partial', 'finished', 'failed', 'cancelled', 'interrupted'}
ROLES = {'frozen_input', 'parent_manifest', 'durable_projection', 'validated_checkpoint',
    'source_interpretation', 'source_history', 'core_binding_projection', 'mapping_fragment',
    'projection_freshness', 'frozen_master_scope', 'shared_source_invalidation'}
MAX_FILES, MAX_JSON_BYTES = 20000, 128 * 1024 * 1024


class IntermediateFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str
    relative_path: str
    sha256: str
    size_bytes: int = Field(strict=True, ge=0)
    kind: str


class IntermediateCleanupPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: str = SCHEMA
    plan_id: str
    session_id: str
    expected_revision: int = Field(strict=True, ge=0)
    session_revision: int = Field(strict=True, ge=0)
    scope: dict
    preservation_sha256: str
    preserved_files: dict[str, str]
    candidates: list[IntermediateFile]
    excluded: list[dict]
    reference_check: dict
    total_size_bytes: int = Field(strict=True, ge=0)
    calls_model: bool = False
    source_artifacts_removed: bool = False
    permanent_deletion: bool = False


def _block(code, message, **detail):
    raise LifecycleBlocked([blocker(code, message, **detail)])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def _sha(file):
    digest = hashlib.sha256()
    with file.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_durable(directory):
    """Persist each new directory entry, including every ancestor we create.

    No symlink ancestors are accepted. Existing directories need no mutation;
    each new child is synced before its parent's directory entry is synced.
    Filesystem/hardware guarantees remain those of mkdir/rename/fsync.
    """
    directory = Path(directory).absolute()
    ancestors = list(reversed([directory, *directory.parents]))
    for current in ancestors:
        if current.is_symlink():
            _block('intermediate_symlink', '中间文件回收路径包含符号链接。')
        if current.exists():
            if not current.is_dir():
                _block('intermediate_restore_conflict', '回收目录路径已有非目录内容。')
            continue
        try:
            current.mkdir()
        except FileExistsError:
            # Another creator must also provide a real directory. Sync the
            # entry here; do not depend on that creator's durability policy.
            if current.is_symlink() or not current.is_dir():
                _block('intermediate_symlink', '中间文件回收目录在创建期间发生变化。')
        _fsync_dir(current)
        _fsync_dir(current.parent)


def _write(path, payload):
    _mkdir_durable(path.parent)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write('\n'); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _kind(relative):
    p = PurePosixPath(relative)
    if len(p.parts) == 3 and p.parts[:2] == ('agent', 'calls') and p.suffix == '.json':
        return 'model_call_diagnostic'
    if len(p.parts) == 2 and p.parts[0] == 'packets' and p.name.endswith('.agent.json'):
        return 'regenerable_agent_packet'
    if relative == 'retrieval/semantic-index.json':
        return 'local_semantic_index_metadata'
    return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _strings(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def assert_intermediates_restored(output_root, session_id):
    """Restoring visibility must not implicitly regenerate pruned model inputs."""
    for file in (Path(output_root) / '.lifecycle/intermediates/receipts').glob('intermediate-*.json'):
        try:
            receipt = json.loads(file.read_text())
            if receipt.get('schema_version') != JOURNAL_SCHEMA:
                raise ValueError('Unknown intermediate journal')
        except (ValueError, OSError):
            _block('intermediate_journal_unavailable', '中间文件回收日志不可核实，请先修复或恢复回收记录。')
        if receipt.get('session_id') == session_id and receipt.get('state') not in {'restored', 'rolled_back'}:
            _block('intermediates_not_restored', '此会话有已回收或中断移动的中间文件；先恢复中间文件，再恢复记录或继续提取。',
                cleanup_id=receipt.get('cleanup_id'))


class IntermediateCleanup:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.output = lifecycle.artifacts.output_root.resolve()
        self.root = self.output / '.lifecycle/intermediates'

    def receipts(self, session_id):
        """Read a bounded recovery index without inspecting source quality or writing."""
        state = self.lifecycle.sessions._state(session_id)
        phase = state.get('lifecycle', {})
        folder = self.root / 'receipts'
        if any(path.is_symlink() for path in (self.output / '.lifecycle', self.root, folder)):
            _block('intermediate_symlink', '中间文件回收索引不是受控普通目录。')
        files = sorted(folder.glob('intermediate-*.json')) if folder.exists() else []
        if len(files) > 1000:
            _block('intermediate_inventory_limit', '回收索引超过读取上限，请联系管理员整理。')
        result, budget = [], 0
        for file in files:
            if file.is_symlink() or not file.is_file():
                _block('intermediate_journal_unavailable', '回收日志不是普通文件。')
            budget += file.stat().st_size
            if budget > MAX_JSON_BYTES:
                _block('intermediate_inventory_limit', '回收索引超过安全解析上限。')
            try:
                receipt = json.loads(file.read_text())
                if not isinstance(receipt, dict) or receipt.get('schema_version') != JOURNAL_SCHEMA:
                    raise ValueError('Unrecognized recovery journal')
                if receipt.get('session_id') != session_id:
                    continue
                plan = IntermediateCleanupPlan.model_validate(receipt['plan']).model_dump()
                body = {key: plan[key] for key in ('session_id', 'expected_revision', 'session_revision',
                    'scope', 'preserved_files', 'candidates')}
                identity = 'intermediate-' + _digest(body)
                if receipt.get('cleanup_id') != identity or file.name != identity + '.json' \
                        or plan['session_id'] != session_id or plan['plan_id'] != 'intermediate-plan-' + _digest(body) \
                        or plan['preservation_sha256'] != _digest(plan['preserved_files']):
                    raise ValueError('Recovery identity differs from the fixed plan')
                receipt_state = receipt.get('state')
                if receipt_state not in {'moving', 'quarantined', 'rolled_back', 'rollback_pending',
                        'restoring', 'restore_pending', 'restored'}:
                    raise ValueError('Unknown recovery state')
            except (KeyError, ValueError, OSError, UnicodeError) as exc:
                _block('intermediate_journal_unavailable', '回收日志不可核实，恢复入口已停止。',
                    journal=file.name, error=str(exc))
            enabled = phase.get('state') == 'archived' and state.get('execution_status') in TERMINAL \
                and receipt_state not in {'restored', 'rolled_back'}
            action = {'action': 'restore', 'enabled': enabled, 'calls_model': False}
            if not enabled:
                action['disabled_reason'] = '中间文件已经恢复。' if receipt_state in {'restored', 'rolled_back'} else '仅能恢复已归档、已结束会话的中间文件。'
            result.append({'cleanup_id': identity, 'state': receipt_state,
                'reason': receipt.get('reason'), 'created_at': receipt.get('created_at'),
                'moved_count': len(receipt.get('moved', [])), 'bytes_size': plan['total_size_bytes'],
                'available_actions': [action], 'permanent_deletion': False})
        return {'session_id': session_id, 'expected_revision': phase.get('revision', 0),
            'items': sorted(result, key=lambda row: row.get('created_at') or '', reverse=True)}

    def _session(self, session_id, expected_revision=None):
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            _block('revision_conflict', '预期版本必须为非负整数。')
        state = self.lifecycle.sessions._state(session_id)
        phase = state.get('lifecycle', {})
        if phase.get('state') != 'archived' or state.get('execution_status') not in TERMINAL:
            _block('intermediate_session_not_archived_terminal', '只可清理已归档且结束的报告会话。')
        projections = state.get('projections')
        if not isinstance(projections, list) or not projections or len(projections) > 100:
            _block('intermediate_scope_invalid', '原始子任务范围不完整，不能清理。')
        jobs = [self.lifecycle.jobs.get(p['job_id']) for p in projections]
        if len({job.job_id for job in jobs}) != len(jobs) or any(job.request.report_session_id != session_id for job in jobs):
            _block('intermediate_scope_invalid', '子任务父会话身份不一致。')
        blocked = self.lifecycle._idle_blockers(state, jobs)
        if blocked:
            raise LifecycleBlocked(blocked)
        if expected_revision is not None and expected_revision != phase.get('revision', 0):
            _block('revision_conflict', '归档记录已变化，请重新生成清理计划。')
        return state, jobs

    def _files(self, state, jobs):
        roots = [(job.job_id, self.lifecycle.artifacts._job_path(job.job_id)) for job in jobs]
        roots.append(('@session', self.lifecycle.artifacts.report_session_path(state['session_id'])))
        files = {}
        for owner, root in roots:
            if root.is_symlink() or not root.is_dir() or (owner != '@session' and (root.parent != self.output or root.name != owner)):
                _block('intermediate_scope_unavailable', '原成果目录缺失或不是受控普通目录。')
            if owner != '@session':
                try:
                    request = json.loads((root / 'input/request.json').read_text())
                except (ValueError, OSError):
                    _block('intermediate_scope_unavailable', '子任务原始固定请求不可核实。')
                if not isinstance(request, dict) or request.get('report_session_id') != state['session_id']:
                    _block('intermediate_scope_invalid', '子任务固定请求的父会话不一致。')
            for file in sorted(root.rglob('*')):
                if file.is_symlink():
                    _block('intermediate_symlink', '成果包含符号链接，不能清理中间文件。')
                if file.is_file():
                    files[owner + '/' + file.relative_to(root).as_posix()] = file
                    if len(files) > MAX_FILES:
                        _block('intermediate_inventory_limit', '成果文件数量超过清理核对上限。')
        return files

    def _closure(self, files, jobs, session_id):
        by_path = {file.resolve(): key for key, file in files.items()}
        candidates = {key for key in files if not key.startswith('@session/') and _kind(key.split('/', 1)[1])}
        protected = set(files) - candidates
        why = {key: [] for key in candidates}
        parsed, budget = {}, 0
        for key, file in files.items():
            if file.suffix not in {'.json', '.jsonl'}:
                continue
            # Unreferenced candidates need no interpretation; preserved JSON
            # documents establish the literal reference closure below.
            if key in candidates:
                continue
            budget += file.stat().st_size
            if budget > MAX_JSON_BYTES:
                _block('intermediate_inventory_limit', '保全引用文档超过安全解析上限。')
            try:
                raw = file.read_text()
                parsed[key] = [json.loads(line) for line in raw.splitlines() if line.strip()] if file.suffix == '.jsonl' else json.loads(raw)
            except (OSError, ValueError, UnicodeError):
                _block('intermediate_reference_invalid', '保留文档不是有效JSON，不能确认引用闭包。', path=key)
        def protect(file, expected, origin):
            nonlocal budget
            key = by_path.get(file.resolve())
            if not key or not re.fullmatch('[a-f0-9]{64}', str(expected)) or _sha(file) != expected:
                _block('intermediate_frozen_reference_changed', '冻结引用缺失、越界或摘要不符，不能清理。', origin=origin)
            if key in candidates:
                why[key].append(origin)
                if key not in parsed:
                    budget += file.stat().st_size
                    if budget > MAX_JSON_BYTES:
                        _block('intermediate_inventory_limit', '保全引用文档超过安全解析上限。')
                    try:
                        parsed[key] = json.loads(file.read_text())
                    except (OSError, ValueError, UnicodeError):
                        _block('intermediate_reference_invalid', '冻结引用的中间文件不可解析。', path=key)
            protected.add(key)
        for key, value in list(parsed.items()):
            if not key.endswith('/artifact-revision.json'):
                continue
            job_id = key.split('/', 1)[0]
            original = self.lifecycle.artifacts._job_path(job_id)
            revision = files[key].parent
            if not isinstance(value, dict) or value.get('schema_version') != 'terminal-result-revision-v1' or value.get('job_id') != job_id:
                _block('intermediate_reference_invalid', '终态版本引用合同不可识别。', path=key)
            for field, base in (('artifact_sha256', revision), ('frozen_input_sha256', original)):
                mapping = value.get(field)
                if not isinstance(mapping, dict) or not mapping:
                    _block('intermediate_reference_invalid', '终态冻结文件闭包缺失。', path=key)
                for relative, digest in mapping.items():
                    target = (base / relative).resolve()
                    if base not in target.parents:
                        _block('intermediate_reference_invalid', '终态引用路径越界。')
                    protect(target, digest, key + ':' + field)
            durable = value.get('durable_fragment_inputs')
            basis = parsed.get(job_id + '/' + (revision / 'input/materialization-basis.json').relative_to(original).as_posix())
            if not isinstance(durable, dict) or not isinstance(basis, dict) or basis.get('durable_inputs') != durable or basis.get('inputs') != value['frozen_input_sha256']:
                _block('intermediate_reference_invalid', '终态持久片段与物化依据不一致。', path=key)
            digest = _sha(revision / 'input/materialization-basis.json')
            manifest = parsed.get(job_id + '/' + (revision / 'manifest.json').relative_to(original).as_posix())
            if value.get('materialization_digest') != digest or value.get('revision_id') != 'partial-' + digest[:32] \
                    or revision.parent != original / 'terminal-revisions' or revision.name != value['revision_id'] \
                    or not isinstance(manifest, dict) or manifest.get('job_id') != job_id \
                    or manifest.get('artifact_revision', {}).get('revision_id') != value['revision_id'] \
                    or manifest.get('artifact_revision', {}).get('materialization_digest') != digest:
                _block('intermediate_reference_invalid', '终态版本内容身份或原子任务不一致。')
            if value.get('parent_manifest_sha256') is not None:
                protect(original / 'manifest.json', value['parent_manifest_sha256'], key + ':parent_manifest_sha256')
                if basis.get('parent_manifest_sha256') != value['parent_manifest_sha256']:
                    _block('intermediate_reference_invalid', '终态版本原清单依据不一致。')
            for identity, entry in durable.items():
                if not isinstance(entry, dict) or entry.get('role') not in ROLES or ':' not in identity:
                    _block('intermediate_reference_invalid', '不可识别的冻结片段引用。')
                prefix, relative = identity.split(':', 1)
                base = original if prefix == 'job' else self.output if prefix == 'output' else None
                if base is None or base not in (base / relative).resolve().parents:
                    _block('intermediate_reference_invalid', '冻结片段引用越界。')
                protect(base / relative, entry.get('sha256'), key + ':durable_fragment_inputs')
            master = value.get('frozen_master_request')
            if not isinstance(master, dict) or master.get('session_id') != session_id:
                _block('intermediate_reference_invalid', '终态版本缺少原父会话冻结证明。')
            target = self.lifecycle.artifacts.report_session_path(session_id) / 'request.json'
            if not isinstance(master.get('relative_path'), str) or (self.output / master['relative_path']).resolve() != target:
                _block('intermediate_reference_invalid', '冻结主请求路径身份不一致。')
            protect(target, master.get('file_sha256'), key + ':frozen_master_request')
            if _digest(json.loads(target.read_text())) != master.get('canonical_sha256') or basis.get('master_scope') != json.loads(target.read_text()):
                _block('intermediate_reference_invalid', '冻结主请求规范摘要或物化范围不一致。')
        # Literal references include diagnostic attempts[].artifact. A call
        # referenced by a preserved decision is protected, not tombstoned.
        pending = list(parsed)
        while pending:
            origin = pending.pop()
            owner = origin.split('/', 1)[0]
            values = list(_strings(parsed[origin]))
            for key in candidates - protected:
                job_id, relative = key.split('/', 1)
                if any(relative in value and (owner == job_id or job_id in value or str(files[key]) in value) for value in values):
                    protected.add(key); why[key].append(origin)
                    file = files[key]; budget += file.stat().st_size
                    if budget > MAX_JSON_BYTES:
                        _block('intermediate_inventory_limit', '保全引用文档超过安全解析上限。')
                    try:
                        parsed[key] = json.loads(file.read_text()); pending.append(key)
                    except (OSError, ValueError, UnicodeError):
                        _block('intermediate_reference_invalid', '已引用的中间文件不可解析，不能清理。', path=key)
        return candidates - protected, why

    def plan(self, session_id, *, expected_revision=None):
        state, jobs = self._session(session_id, expected_revision)
        scope = self.lifecycle._scope(jobs)
        references = self.lifecycle.guard.check(scope)
        if references.get('verified') is not True:
            raise LifecycleBlocked(references.get('blockers') or [blocker('reference_check_unavailable', '来源保护核对不可用。')])
        files = self._files(state, jobs)
        movable, why = self._closure(files, jobs, session_id)
        preserved = {key: _sha(file) for key, file in files.items() if key not in movable}
        entries = [IntermediateFile(job_id=key.split('/', 1)[0], relative_path=key.split('/', 1)[1],
            sha256=_sha(files[key]), size_bytes=files[key].stat().st_size, kind=_kind(key.split('/', 1)[1])) for key in sorted(movable)]
        body = {'session_id': session_id, 'expected_revision': state['lifecycle']['revision'],
            'session_revision': state['revision'], 'scope': scope, 'preserved_files': preserved,
            'candidates': [entry.model_dump() for entry in entries]}
        return IntermediateCleanupPlan(**body, plan_id='intermediate-plan-' + _digest(body),
            preservation_sha256=_digest(preserved), excluded=[{'path': key, 'reason': 'preserved_reference',
            'referrers': refs} for key, refs in sorted(why.items()) if key not in movable],
            reference_check=references, total_size_bytes=sum(entry.size_bytes for entry in entries))

    @contextmanager
    def _locked(self):
        with self.lifecycle._lock, self.lifecycle.jobs._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if any(path.is_symlink() for path in (self.output / '.lifecycle', self.root)):
                _block('intermediate_symlink', '中间文件回收区不是受控普通目录。')
            _mkdir_durable(self.root)
            with (self.root / 'operation.lock').open('a') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                yield

    def _receipt_path(self, cleanup_id):
        if not re.fullmatch('intermediate-[a-f0-9]{64}', cleanup_id):
            raise ValueError('无效中间文件回收标识。')
        return self.root / 'receipts' / (cleanup_id + '.json')

    def _paths(self, entry, cleanup_id):
        source = self.lifecycle.artifacts._job_path(entry['job_id']) / entry['relative_path']
        destination = self.root / 'content' / cleanup_id / entry['job_id'] / entry['relative_path']
        if not _kind(entry['relative_path']) or source.is_symlink() or destination.is_symlink():
            _block('intermediate_reference_invalid', '回收日志不是允许的普通中间文件。')
        for target, base in ((source, self.lifecycle.artifacts._job_path(entry['job_id'])), (destination, self.root / 'content' / cleanup_id)):
            if base not in target.resolve().parents or any(parent.is_symlink() for parent in [target, *target.parents] if parent != self.output.parent):
                _block('intermediate_symlink', '回收或恢复路径越界或包含符号链接。')
        return source, destination

    def _preserved(self, plan):
        for key, digest in plan['preserved_files'].items():
            owner, relative = key.split('/', 1)
            root = self.lifecycle.artifacts.report_session_path(plan['session_id']) if owner == '@session' else self.lifecycle.artifacts._job_path(owner)
            file = root / relative
            if not file.is_file() or any(path.is_symlink() for path in [file, *file.parents]) or _sha(file) != digest:
                _block('intermediate_preservation_changed', '保全文件发生变化，中间文件操作已停止。', path=key)

    @staticmethod
    def _move(source, destination):
        _mkdir_durable(destination.parent)
        if destination.exists():
            _block('intermediate_restore_conflict', '目标路径已有内容，不覆盖已有文件。')
        os.rename(source, destination)
        # Persist the destination name before acknowledging the source unlink.
        _fsync_dir(destination.parent); _fsync_dir(source.parent)

    def execute(self, session_id, *, plan_id, expected_revision, reason, operator_ref):
        reason = require_reason(reason)
        if not isinstance(operator_ref, str) or not operator_ref.strip():
            raise ValueError('中间文件操作需要明确操作员身份。')
        with self._locked():
            plan = self.plan(session_id, expected_revision=expected_revision).model_dump()
            if plan['plan_id'] != plan_id:
                _block('intermediate_plan_changed', '候选文件或保全闭包已变化，请重新核对计划。')
            if not plan['candidates']:
                _block('intermediate_no_candidates', '没有可安全回收的未引用中间文件。')
            cleanup_id = 'intermediate-' + plan_id.removeprefix('intermediate-plan-')
            path = self._receipt_path(cleanup_id)
            if path.exists():
                _block('intermediate_existing_receipt', '此计划已有回收记录，请核对或恢复该记录。', cleanup_id=cleanup_id)
            receipt = {'schema_version': JOURNAL_SCHEMA, 'cleanup_id': cleanup_id, 'session_id': session_id,
                'created_at': datetime.now(timezone.utc).isoformat(), 'state': 'moving',
                'reason': reason, 'operator_ref': operator_ref, 'plan': plan, 'moved': [],
                'source_scope_guard_unchanged': True, 'permanent_deletion': False}
            _write(path, receipt)
            try:
                for entry in plan['candidates']:
                    source, destination = self._paths(entry, cleanup_id)
                    if not source.is_file() or _sha(source) != entry['sha256']:
                        _block('intermediate_plan_changed', '中间文件在移动前已变化。')
                    self._move(source, destination)
                    if _sha(destination) != entry['sha256']:
                        _block('intermediate_content_changed', '移动后的中间文件摘要不一致，需恢复回收记录。')
                    receipt['moved'].append(entry['job_id'] + '/' + entry['relative_path']); _write(path, receipt)
                self._preserved(plan)
                receipt['state'] = 'quarantined'; _write(path, receipt)
            except Exception as exc:
                receipt['error'] = str(exc)
                try:
                    self._restore_files(receipt)
                    receipt['state'] = 'rolled_back'
                except Exception as rollback:
                    receipt['state'] = 'rollback_pending'; receipt['recovery_error'] = str(rollback)
                _write(path, receipt)
                raise
            return receipt

    def _restore_files(self, receipt):
        # Preflight all entries before making any move; a journal saved before
        # an interrupted rename is recovered from actual file locations.
        plan = IntermediateCleanupPlan.model_validate(receipt['plan']).model_dump()
        body = {key: plan[key] for key in ('session_id', 'expected_revision', 'session_revision',
            'scope', 'preserved_files', 'candidates')}
        if plan['plan_id'] != 'intermediate-plan-' + _digest(body) or receipt['cleanup_id'] != 'intermediate-' + _digest(body) \
                or receipt['session_id'] != plan['session_id'] or plan['preservation_sha256'] != _digest(plan['preserved_files']) \
                or any(entry['job_id'] not in plan['scope'].get('job_ids', []) for entry in plan['candidates']):
            _block('intermediate_journal_changed', '回收日志的固定计划或原子任务范围已变化，恢复已停止。')
        pairs = []
        for entry in receipt['plan']['candidates']:
            source, destination = self._paths(entry, receipt['cleanup_id'])
            if source.exists() == destination.exists():
                _block('intermediate_restore_conflict', '原路径与回收文件冲突或同时缺失，恢复不会覆盖。')
            actual = source if source.exists() else destination
            if not actual.is_file() or _sha(actual) != entry['sha256']:
                _block('intermediate_content_changed', '回收内容摘要不符，恢复已停止。')
            pairs.append((source, destination))
        self._preserved(receipt['plan'])
        for source, destination in pairs:
            if destination.exists():
                self._move(destination, source)
        self._preserved(receipt['plan'])

    def restore(self, session_id, *, cleanup_id, expected_revision, reason, operator_ref):
        reason = require_reason(reason)
        if not isinstance(operator_ref, str) or not operator_ref.strip():
            raise ValueError('中间文件操作需要明确操作员身份。')
        with self._locked():
            self._session(session_id, expected_revision)
            path = self._receipt_path(cleanup_id)
            receipt = json.loads(path.read_text())
            if receipt.get('schema_version') != JOURNAL_SCHEMA or receipt.get('session_id') != session_id:
                _block('intermediate_scope_invalid', '回收记录不属于此父会话。')
            if receipt['state'] in {'restored', 'rolled_back'}:
                return receipt
            receipt.update(state='restoring', restore_reason=reason, restore_operator_ref=operator_ref)
            _write(path, receipt)
            try:
                self._restore_files(receipt)
                receipt['state'] = 'restored'
            except Exception as exc:
                receipt.update(state='restore_pending', recovery_error=str(exc)); _write(path, receipt)
                raise
            _write(path, receipt)
            return receipt
