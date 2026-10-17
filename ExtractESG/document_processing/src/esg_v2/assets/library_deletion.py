"""PDF and OCR libraries: explicit, guarded permanent deletion.

The publication reservation prevents candidate import from racing deletion.
No source bytes are retained in a trash directory. A small operation journal
records intent and the exact owned paths, so a failed operation is never
reported as a successful deletion. OCR tombstones contain run IDs, never the
shared PDF hash; deleting an old OCR run does not disable processing that PDF.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from esg_runtime.lifecycle import LifecycleBlocked, PublicationReferenceGuard, blocker
from esg_runtime.production_references import production_references


def _now():
    return datetime.now(timezone.utc).isoformat()


class LibraryDeletionService:
    def __init__(self, catalog, inventory=None, *, ocr_store=None, ir_store=None):
        self.catalog, self.inventory = catalog, inventory
        self.ocr_store, self.ir_store = ocr_store, ir_store
        self.guard = catalog.reference_guard
        self._lock = threading.RLock()
        self.journal_root = (inventory.cleanup_root if inventory else catalog.root / ".library-operations") / "permanent-deletions"
        self.cancel_task = self.cleanup_records = self.processing = self.plans = self.automations = None
        self._stop = threading.Event()
        self._thread = None

    def configure(self, *, cancel_task=None, processing=None, plans=None, automations=None,
                  native_request=None, cleanup_records=None):
        self.cancel_task, self.processing, self.plans, self.automations = cancel_task, processing, plans, automations
        self.native_request, self.cleanup_records = native_request, cleanup_records

    @contextmanager
    def _owners_lock(self):
        with ExitStack() as stack:
            for owner in (self.automations, self.processing, self.plans):
                if owner is not None:
                    stack.enter_context(owner._lock)
            yield

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._pending_loop, daemon=True, name="library-deletions")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _pending_loop(self):
        while not self._stop.wait(1):
            for path in self.journal_root.glob("pending-*.json"):
                pending={}
                try:
                    pending = json.loads(path.read_text())
                    if pending.get("status") != "deletion_pending":
                        continue
                    self._cancel_pending([],pending)
                    if pending["kind"] == "pdf":
                        asset = self.catalog.get(pending["identity"])
                        if asset["sha256"] != pending["source_sha256"]:
                            raise ValueError("PDF 身份已改变，删除已停止")
                        result = self.delete_pdf(pending["identity"], expected_revision=asset["lifecycle"].get("revision", 0),
                            identity_sha256=pending["source_sha256"], defer=False)
                    elif pending['kind']=='ocr':
                        _, _, _, digest = self._ocr_parts(pending["identity"])
                        result = self.delete_ocr(pending["identity"], identity_sha256=digest, defer=False)
                    else:
                        _,_,_,digest=self._ir_parts(pending['identity'])
                        result=self.delete_ir(pending['identity'],identity_sha256=digest,defer=False)
                    pending.update(result)
                except LifecycleBlocked as exc:
                    # Cancellation remains in progress; do not destroy a live worker's inputs.
                    if all(row["code"] in {"active_source_task","active_source_owner","active_source_worker","native_worker_active"} for row in exc.blockers):
                        continue
                    pending.update(status="deletion_failed", error=str(exc))
                except KeyError:
                    pending.update(status="deleted", deleted=True)
                except Exception as exc:
                    pending.update(status="deletion_failed", error=str(exc))
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(pending, ensure_ascii=False)); temporary.replace(path)

    @staticmethod
    def _action(endpoint, payload, *, linked_count=0):
        return {"action": "delete", "label": "删除", "endpoint": endpoint, "method": "DELETE",
                "payload": payload, "requires_confirmation": True, "requires_reason": False,
                "calls_model": False, "incurs_cost": False, "permanent": True,
                "linked_ir_count": linked_count}

    @contextmanager
    def _queue_lock(self):
        path = self.catalog.pipeline_queue_db or (self.inventory.pipeline_queue_db if self.inventory else None)
        if not path or not Path(path).exists():
            yield None
            return
        try:
            with sqlite3.connect(Path(path), timeout=30) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                db.execute("BEGIN IMMEDIATE")
                yield db
        except sqlite3.Error as exc:
            raise LifecycleBlocked([blocker("local_reference_check_unavailable", "无法确认任务引用，删除未完成。")]) from exc

    def _local_blockers(self, db, *, asset_id=None, source_sha256=None, run_ids=()):
        if db is None:
            return []
        result = []
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "pipeline_tasks" in tables:
            from esg_v2.control_plane.store import PipelineQueueStore
            for row in db.execute("SELECT * FROM pipeline_tasks"):
                if row['status'] not in {'queued','running'} and not PipelineQueueStore._row_worker_alive(row):continue
                try:
                    payload = json.loads(row["payload_json"])
                    if not isinstance(payload, dict):
                        raise ValueError("invalid queue payload")
                except (TypeError, ValueError) as exc:
                    raise LifecycleBlocked([blocker("local_reference_check_unavailable", "存在无法核实的活动任务，暂不能删除来源。")]) from exc
                if ((asset_id and payload.get("asset_id") == asset_id)
                        or (source_sha256 and payload.get("source_sha256") == source_sha256)
                        or row["native_job_id"] in run_ids
                        or any(payload.get(key) in run_ids for key in ("ocr_run_id", "ir_run_id", "input_run_id", "parent_run_id"))):
                    result.append(blocker("active_source_task", "报告仍被活动任务使用；先取消并等待任务退出。", task_id=row["task_id"]))
        if 'report_processing_runs' in tables:
            for row in db.execute("SELECT state_json FROM report_processing_runs"):
                state=json.loads(row[0])
                if ((asset_id and state.get('asset_id') == asset_id) or (source_sha256 and state.get('source_sha256') == source_sha256)
                    or state.get('ocr_run_id') in run_ids or state.get('ir_run_id') in run_ids):
                    if state.get('status') in {'queued','running','cancelling'} or production_references(db,processing_id=state['processing_id']):
                        result.append(blocker('active_source_task','删除时会先停止相关报告处理。',processing_id=state['processing_id']))
        if production_references(db,asset_id=asset_id,source_sha256=source_sha256):
            result.append(blocker('active_source_task','删除时会从自动生产计划移除此报告。'))
        if 'pipeline_plans' in tables:
            for row in db.execute('SELECT state_json FROM pipeline_plans'):
                state=json.loads(row[0])
                for document in state.get('documents',[]):
                    matches=((asset_id and document.get('asset_id')==asset_id) or
                        (source_sha256 and document.get('source_sha256')==source_sha256) or
                        document.get('ir_run_id') in run_ids or document.get('ocr_run_id') in run_ids)
                    if matches and document.get('status') not in {'completed','partial','failed','interrupted','cancelled','waiting_review'}:
                        result.append(blocker('active_source_task','删除时会停止此报告的提取任务。',plan_id=state['plan_id']))
        if "ir_input_pins" in tables and "pipeline_tasks" in tables and run_ids:
            placeholders=','.join('?' for _ in run_ids)
            for row in db.execute(f"SELECT DISTINCT t.task_id FROM pipeline_tasks t JOIN ir_input_pins p ON p.owner_id=t.task_id WHERE t.status IN ('queued','running') AND p.ir_run_id IN ({placeholders})", tuple(run_ids)):
                if not any(item.get('task_id') == row[0] for item in result):
                    result.append(blocker('active_source_task', '删除时会先停止使用此版本的任务。', task_id=row[0]))
        return result

    def _ocr_rows(self):
        rows=self.inventory._discover_ocr_runs() if self.inventory else {}
        path=self.catalog.pipeline_queue_db
        if path and Path(path).exists():
            with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'report_processing_artifacts' in tables:
                    for identity,sha in db.execute("SELECT run_id,source_sha256 FROM report_processing_artifacts WHERE stage='ocr'"):
                        if identity in rows and not rows[identity].get('source_pdf_sha256'):
                            rows[identity]['source_pdf_sha256']=sha
                if 'report_processing_runs' in tables:
                    for raw,sha in db.execute('SELECT state_json,source_sha256 FROM report_processing_runs'):
                        state=json.loads(raw);identity=state.get('ocr_run_id') or state.get('steps',{}).get('ocr',{}).get('run_id')
                        if identity in rows and not rows[identity].get('source_pdf_sha256'):rows[identity]['source_pdf_sha256']=sha
                if 'pipeline_tasks' in tables:
                    for identity,raw in db.execute("SELECT native_job_id,payload_json FROM pipeline_tasks WHERE task_type='ocr'"):
                        if identity in rows and not rows[identity].get('source_pdf_sha256'):
                            sha=json.loads(raw).get('source_sha256')
                            if sha:rows[identity]['source_pdf_sha256']=sha
        return rows

    @staticmethod
    def _path_snapshot(paths):
        rows = []
        for path in paths:
            path = Path(path)
            if path.is_symlink():
                raise LifecycleBlocked([blocker("unsafe_source_path", "来源包含符号链接，删除已停止。")])
            children = [path] if path.is_file() else [path, *sorted(path.rglob("*"))]
            for child in children:
                if child.is_symlink():
                    raise LifecycleBlocked([blocker("unsafe_source_path", "来源包含符号链接，删除已停止。")])
                stat = child.stat()
                row = {"path": str(child), "size": stat.st_size, "modified_ns": stat.st_mtime_ns, "directory": child.is_dir()}
                if child.is_file() and child.name in {"manifest.json", "state.json"}:
                    row["contents_sha256"] = hashlib.sha256(child.read_bytes()).hexdigest()
                rows.append(row)
        return rows

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _ocr_parts(self, run_id):
        if not self.inventory:
            raise ValueError("OCR library is not configured")
        ocr = self._ocr_rows()
        row = ocr.get(run_id)
        if row is None:
            raise KeyError(run_id)
        ir = self.inventory._discover_ir_runs(ocr)
        selected = {key for key, value in ir.items() if value.get("ocr_run_id") == run_id}
        # All descendants belong to the same deleted evidence lineage, even if
        # an old repair manifest omitted its direct OCR link.
        changed = True
        while changed:
            before = len(selected)
            selected.update(key for key, value in ir.items() if value.get("parent_ir_run_id") in selected)
            changed = len(selected) != before
        entries = self.inventory._owned_path_entries("ocr", run_id)
        for identity in sorted(selected):
            entries.extend(self.inventory._owned_path_entries("ir", identity))
        paths = [Path(entry["absolute_path"]) for entry in entries]
        retention=self.inventory.cleanup_root/'ir-retention'/'current'/f'{run_id}.json'
        if retention.is_file():paths.append(retention)
        for pin in (self.inventory.ir_output_root/'.pins').glob('*.json'):
            try:
                record=json.loads(pin.read_text())
                if (record.get('run_id') or record.get('ir_run_id')) in selected:paths.append(pin)
            except (OSError,ValueError):continue
        for path in paths:
            if path != retention:self.inventory._require_owned_path(path)
        snapshot = {"row": row, "linked_ir_ids": sorted(selected), "paths": self._path_snapshot(paths)}
        return row, sorted(selected), paths, self._digest(snapshot)

    def pdf_library(self):
        assets=self.catalog.refresh(include_archived=True)['assets']
        identities={}
        if self.catalog.pipeline_queue_db and Path(self.catalog.pipeline_queue_db).is_file():
            with sqlite3.connect(Path(self.catalog.pipeline_queue_db).resolve().as_uri()+'?mode=ro',uri=True) as db:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if {'reports','report_asset_versions'}<=tables:
                    for report_id,metadata,sha in db.execute('SELECT r.report_id,r.metadata_json,v.source_sha256 FROM reports r JOIN report_asset_versions v USING(report_id)'):
                        identities[sha]={'report_id':report_id,'report_metadata':json.loads(metadata)}
        rows=[]
        for asset in assets:
            row={key:asset.get(key) for key in ('asset_id','file_name','size_bytes','modified_at','sha256','lifecycle')}
            row.update(identities.get(asset['sha256'],{}))
            action=self._action(f"/api/report-assets/{asset['asset_id']}",
                {'expected_revision':asset['lifecycle'].get('revision',0),'identity_sha256':asset['sha256']})
            action.update(preflight_endpoint=f"/api/report-assets/{asset['asset_id']}/deletion",requires_operator=True,
                confirmation_message='确认删除这份 PDF、全部 OCR 版本及关联本地处理记录？删除后无法恢复。')
            row['available_actions']=[action];rows.append(row)
        return {'schema_version':'pdf-library-v1','items':rows,'count':len(rows)}

    def pdf_description(self, asset_id):
        asset=self.catalog.get(asset_id)
        versions=[row for row in self._ocr_rows().values() if row.get('source_pdf_sha256') == asset['sha256']]
        action=self._action(f'/api/report-assets/{asset_id}',
            {'expected_revision':asset['lifecycle'].get('revision',0),'identity_sha256':asset['sha256']})
        return {'asset_id':asset_id,'blockers':[], 'ocr_count':len(versions),
                'message':'同时清理该 PDF 的 OCR、结构及本地任务记录；已入库的数据快照保留。',
                'available_actions':[action]}

    def ocr_library(self):
        rows=self._ocr_rows()
        items=[]
        for identity,row in sorted(rows.items(),reverse=True):
            action=self._action(f'/api/ocr-library/{identity}',{})
            action.update(preflight_endpoint=f'/api/ocr-library/{identity}',requires_operator=True,
                confirmation_message='确认删除这份 OCR、关联结构与本地处理记录？保留 PDF 和其他 OCR 版本。')
            items.append({**row,'title':row.get('document_label'),'available_actions':[action]})
        return {'schema_version':'ocr-library-v1','items':items,'count':len(items),'generated_at':_now()}

    def report_library(self):
        pdf=self.pdf_library()['items'];ocr=self.ocr_library()['items']
        ir=self.inventory._discover_ir_runs({row['run_id']:row for row in ocr}) if self.inventory else {}
        children={}
        for row in ir.values():
            action=self._action(f"/api/document-ir/jobs/{row['run_id']}",{})
            action.update(preflight_endpoint=f"/api/document-ir/jobs/{row['run_id']}/deletion",requires_operator=True,accepts_revision=False,
                confirmation_message='确认删除这个结构版本及其后续修订、关联提取记录？保留 PDF 和 OCR。')
            row['available_actions']=[action]
            children.setdefault(row.get('ocr_run_id'),[]).append(row)
        for row in ocr:
            row['ir_versions']=sorted(children.get(row['run_id'],[]),key=lambda r:r.get('updated_at') or '',reverse=True)
            row['linked_ir_ids']=[child['run_id'] for child in row['ir_versions']]
        grouped={};linked=set()
        for row in pdf:
            if row['asset_id'] in grouped:
                grouped[row['asset_id']]['file_names'].append(row['file_name']);continue
            versions=[child for child in ocr if child.get('source_pdf_sha256') == row['sha256']]
            for child in versions:
                child['source_asset_id']=row['asset_id'];linked.add(child['run_id'])
            grouped[row['asset_id']]={**row,'file_names':[row['file_name']], 'ocr_versions':versions,'ocr_count':len(versions)}
        pending=[]
        for path in self.journal_root.glob('pending-*.json'):
            try:
                record=json.loads(path.read_text())
                if record.get('status') in {'deletion_pending','deletion_failed'}:pending.append(record)
            except (OSError,ValueError):continue
        return {'schema_version':'report-library-v1','items':list(grouped.values()),
                'unlinked_ocr':[row for row in ocr if row['run_id'] not in linked],
                'unlinked_ir':[row for row in ir.values() if row.get('ocr_run_id') not in {r['run_id'] for r in ocr}],
                'count':len(grouped),'ocr_count':len(ocr),'pending_deletions':pending,'compatibility_view':False}

    def _ir_parts(self,run_id):
        ir=self.inventory._discover_ir_runs(self._ocr_rows())
        if run_id not in ir:raise KeyError(run_id)
        selected={run_id}
        while True:
            count=len(selected)
            selected.update(key for key,row in ir.items() if row.get('parent_ir_run_id') in selected)
            if len(selected)==count:break
        paths=[Path(entry['absolute_path']) for identity in sorted(selected)
               for entry in self.inventory._owned_path_entries('ir',identity)]
        for path in paths:self.inventory._require_owned_path(path)
        digest=self._digest({'row':ir[run_id],'selected':sorted(selected),'paths':self._path_snapshot(paths)})
        return ir[run_id],selected,paths,digest

    def ir_description(self,run_id):
        row,selected,_,digest=self._ir_parts(run_id)
        action=self._action(f'/api/document-ir/jobs/{run_id}',{'identity_sha256':digest})
        action['accepts_revision']=False
        return {**row,'identity_sha256':digest,'blockers':[],'linked_ir_ids':sorted(selected),
                'available_actions':[action]}

    def delete_ir(self,run_id,*,identity_sha256,defer=True):
        with self._owners_lock(),self._lock,self.inventory._lock,self._queue_lock() as db:
            row,selected,paths,digest=self._ir_parts(run_id)
            if identity_sha256!=digest:raise LifecycleBlocked([blocker('revision_conflict','结构版本已变化，请刷新后重试。')])
            blockers=self._local_blockers(db,run_ids=selected)
            if blockers:
                if defer:return self._schedule('ir',run_id,blockers,run_ids=selected)
                raise LifecycleBlocked(blockers)
            try:self._detach(db,run_ids=selected)
            except LifecycleBlocked as exc:
                if defer and all(b['code']=='native_worker_active' for b in exc.blockers):
                    return self._schedule('ir',run_id,exc.blockers,run_ids=selected)
                raise
            result=self._remove(paths,scope={'job_ids':[],'ir_run_ids':sorted(selected)},identity=run_id,fingerprint=digest)
            if self.ir_store:
                for identity in selected:self.ir_store.discard(identity)
            return {**result,'deleted_ir_run_ids':sorted(selected),'source_pdf_deleted':False,'source_ocr_deleted':False}

    def _ocr_plan(self, run_id, db):
        row, linked, paths, digest = self._ocr_parts(run_id)
        blockers=self._local_blockers(db, run_ids={run_id, *linked})
        # Publication checks may include the PDF hash, but the durable deletion
        # reservation is deliberately scoped only to the removed run identities.
        scope = {"job_ids": [run_id], "ir_run_ids": linked}
        return row, linked, paths, digest, scope, blockers

    def ocr_description(self, run_id):
        with self._lock, self.inventory._lock, self._queue_lock() as db:
            row, linked, _, digest, _, blockers = self._ocr_plan(run_id, db)
            return {**row, "schema_version": "ocr-library-item-v1", "identity_sha256": digest,
                "linked_ir_ids": linked, "blockers": [], "active_task_count":len(blockers),
                "available_actions": [self._action(f"/api/ocr-library/{run_id}", {"identity_sha256": digest}, linked_count=len(linked))]}

    def delete_ocr(self, run_id, *, identity_sha256, defer=True):
        with self._owners_lock(), self._lock, self.catalog._lock, self.inventory._lock, self._queue_lock() as db:
            row, linked, paths, digest, scope, blockers = self._ocr_plan(run_id, db)
            if digest != identity_sha256:
                raise LifecycleBlocked([blocker("revision_conflict", "识别成果已变化，请刷新后重试。")])
            if blockers:
                if defer:
                    return self._schedule('ocr',run_id,blockers,source_sha256=row.get('source_pdf_sha256'),run_ids={run_id,*linked})
                raise LifecycleBlocked(blockers)
            try:self._detach(db,run_ids={run_id,*linked})
            except LifecycleBlocked as exc:
                if defer and all(b['code']=='native_worker_active' for b in exc.blockers):
                    return self._schedule('ocr',run_id,exc.blockers,run_ids={run_id,*linked},source_sha256=row.get('source_pdf_sha256'))
                raise
            result = self._remove(paths, scope=scope, identity=run_id, fingerprint=digest)
            if self.ocr_store:
                self.ocr_store.discard(run_id)
            if self.ir_store:
                for identity in linked:
                    self.ir_store.discard(identity)
            result.update(run_id=run_id, deleted_ir_run_ids=linked, source_pdf_deleted=False)
            return result

    def delete_pdf(self, asset_id, *, expected_revision, identity_sha256, defer=True):
        with self._owners_lock(), self._lock, self.catalog._lock, self._queue_lock() as db:
            asset = self.catalog.get(asset_id)
            if asset["lifecycle"].get("revision", 0) != expected_revision or asset["sha256"] != identity_sha256:
                raise LifecycleBlocked([blocker("revision_conflict", "PDF 已变化，请刷新后重试。")])
            versions=[row for row in self._ocr_rows().values() if row.get('source_pdf_sha256') == asset['sha256']]
            parts=[self._ocr_parts(row['run_id']) for row in versions]
            run_ids={row['run_id'] for row in versions}
            run_ids.update(identity for _,ids,_,_ in parts for identity in ids)
            blockers = self._local_blockers(db, asset_id=asset_id, source_sha256=asset["sha256"],run_ids=run_ids)
            if blockers:
                if defer:
                    return self._schedule('pdf',asset_id,blockers,source_sha256=asset['sha256'],run_ids=run_ids)
                raise LifecycleBlocked(blockers)
            matches = [entry for entry in self.catalog.refresh(include_archived=True)["assets"] if entry["asset_id"] == asset_id]
            paths = []
            for entry in matches:
                path = self.catalog.root / entry["relative_path"]
                if path.is_symlink() or path.resolve().parent != self.catalog.root or entry["sha256"] != identity_sha256:
                    raise LifecycleBlocked([blocker("source_identity_conflict", "PDF 身份不一致，删除已停止。")])
                from esg_v2.utils.hashing import sha256_file
                if sha256_file(path) != identity_sha256:
                    raise LifecycleBlocked([blocker("revision_conflict", "PDF 内容已变化，请刷新后重试。")])
                paths.append(path)
            paths.extend(path for _,_,owned,_ in parts for path in owned)
            try:self._detach(db,asset_id=asset_id,source_sha256=asset['sha256'],run_ids=run_ids)
            except LifecycleBlocked as exc:
                if defer and all(b['code']=='native_worker_active' for b in exc.blockers):
                    return self._schedule('pdf',asset_id,exc.blockers,run_ids=run_ids,source_sha256=asset['sha256'])
                raise
            if db is not None:
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'report_asset_versions' in tables:
                    report_ids=[row[0] for row in db.execute('SELECT report_id FROM report_asset_versions WHERE source_sha256=?',(asset['sha256'],))]
                    db.execute('DELETE FROM report_asset_versions WHERE source_sha256=?',(asset['sha256'],))
                    for report_id in report_ids:
                        if not db.execute('SELECT 1 FROM report_asset_versions WHERE report_id=?',(report_id,)).fetchone():
                            if 'report_metadata_history' in tables:db.execute('DELETE FROM report_metadata_history WHERE report_id=?',(report_id,))
                            if 'reports' in tables:db.execute('DELETE FROM reports WHERE report_id=?',(report_id,))
            result = self._remove(paths, scope={"source_sha256": identity_sha256, "job_ids": [r['run_id'] for r in versions], "ir_run_ids": sorted(run_ids-{r['run_id'] for r in versions})}, identity=asset_id, fingerprint=identity_sha256)
            if self.ocr_store:
                for row in versions:self.ocr_store.discard(row['run_id'])
            if self.ir_store:
                for _,ids,_,_ in parts:
                    for identity in ids:self.ir_store.discard(identity)
            records = self.catalog._read_lifecycle()
            records.pop(asset_id, None)
            self.catalog._write_lifecycle(records)
            result.update(asset_id=asset_id, remaining_asset_count=self.catalog.refresh(include_archived=True)["asset_count"])
            return result

    def _detach(self,db,**scope):
        if db is not None and self.cleanup_records:
            import os
            token=self.guard.token if isinstance(self.guard,PublicationReferenceGuard) else None
            if not token:
                default=Path(__file__).resolve().parents[5]/'PlatESG/.local/review-token'
                path=Path(os.getenv('PLATESG_LIFECYCLE_TOKEN_FILE',default))
                if path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o077:
                    token=path.read_text().strip()
            return self.cleanup_records(db,**scope,native_request=self.native_request,
                authorization='Bearer '+token if token else None)

    def _schedule(self,kind,identity,blockers,*,source_sha256=None,run_ids=()):
        if not self.cancel_task or any(row['code'] not in {'active_source_task','native_worker_active'} for row in blockers):
            raise LifecycleBlocked(blockers)
        request={'kind':kind,'identity':identity,'source_sha256':source_sha256,
                 'run_ids':sorted(run_ids),
                 'status':'deletion_pending','deleted':False,'created_at':_now(),
                 'message':'正在停止关联任务，退出后自动删除。'}
        self.journal_root.mkdir(parents=True,exist_ok=True)
        path=self.journal_root/f'pending-{self._digest([kind,identity])}.json'
        path.write_text(json.dumps(request,ensure_ascii=False))
        # The queue transaction must finish before cancellation writes its own transaction.
        task_ids=[row['task_id'] for row in blockers if row.get('task_id')]
        threading.Thread(target=self._cancel_pending,args=(task_ids,request),daemon=True).start()
        self.start()
        return request

    def _cancel_pending(self,task_ids,request):
        run_ids=set(request.get('run_ids',[]))
        def match(state):
            return ((request['kind']=='pdf' and (state.get('asset_id')==request['identity']
                    or state.get('source_sha256')==request.get('source_sha256')))
                    or state.get('ocr_run_id') in run_ids or state.get('ir_run_id') in run_ids)
        with self._owners_lock():
            matched_processing={state['processing_id'] for state in self.processing.list(include_archived=True) if match(state)} if self.processing else set()
            matched_plans={state['plan_id']:[document for document in state.get('documents',[]) if match(document)] for state in self.plans.list()} if self.plans else {}
            if self.automations:
                for state in self.automations.list(include_archived=True):
                    changed=False
                    for item in state['items']:
                        if match(item) or item.get('processing_id') in matched_processing or any(
                                (document.get('asset_id') and document.get('asset_id')==item.get('asset_id'))
                                or (document.get('source_sha256') and document.get('source_sha256')==item.get('source_sha256'))
                                for document in matched_plans.get(item.get('extraction_plan_id'),[])):
                            item.update(status='cancelled',source_deleted=True);changed=True
                    if changed:self.automations._save(state,action='source_deletion')
            if self.processing:
                for state in self.processing.list(include_archived=True):
                    if match(state) and state['status'] in {'queued','running','cancelling'}:
                        self.processing.action(state['processing_id'],'cancel')
            if self.plans:
                for state in self.plans.list():
                    changed=False
                    for document in state.get('documents',[]):
                        if match(document):
                            for step in document.get('steps',{}).values():
                                if step.get('task_id'):task_ids.append(step['task_id'])
                            document.update(status='cancelled',source_deleted=True);changed=True
                    if changed:
                        if all(d.get('status')=='cancelled' for d in state['documents']):state['status']='cancelled'
                        self.plans.save(state)
        for task_id in task_ids:
            try:self.cancel_task(task_id)
            except KeyError:pass

    def _remove(self, paths, *, scope, identity, fingerprint):
        self._path_snapshot(paths)  # Refuse symlinks again immediately before reservation.
        reason = "用户删除本地来源文件和处理记录，保留已入库的数据快照。"
        operation_id = str(uuid4())
        journal = {"schema_version": "permanent-library-deletion-v1", "operation_id": operation_id,
            "identity": identity, "fingerprint": fingerprint, "scope": scope,
            "state": "reserving", "paths": [str(path) for path in paths], "deleted_paths": [], "created_at": _now()}
        self.journal_root.mkdir(parents=True, exist_ok=True)
        destination = self.journal_root / (operation_id + ".json")
        def save():
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
        save()
        try:
            if isinstance(self.guard, PublicationReferenceGuard):
                reservation = self.guard.reserve(scope, reason, reservation_id=operation_id, local_files_only=True)
            else:
                reservation = self.guard.reserve(scope, reason)
            journal.update(state="reserved", reservation_id=reservation); save()
            # These are database reservation states, not a filesystem quarantine.
            self.guard.update_reservation(reservation, "quarantined", reason)
            journal.update(state="deleting"); save()
            for path in paths:
                if not path.exists():continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                journal["deleted_paths"].append(str(path)); save()
            self.guard.update_reservation(reservation, "released", reason)
            journal.update(state="deleted", completed_at=_now()); save()
        except Exception as exc:
            journal.update(state="incomplete", error_type=type(exc).__name__)
            try:
                self.guard.update_reservation(journal.get('reservation_id') or operation_id,'released',reason)
                journal['reservation_release_confirmed']=True
            except Exception:
                journal['reservation_release_confirmed']=False
            save()
            raise
        return {"deleted": True, "status": "deleted", "operation": "delete", "recoverable": False,
                "deleted_path_count": len(paths), "operation_id": operation_id}
