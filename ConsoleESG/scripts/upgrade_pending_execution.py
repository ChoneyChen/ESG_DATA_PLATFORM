#!/usr/bin/env python3
"""Explicit code-only handoff for pending runs; frozen business inputs stay intact."""
import argparse
import json
from pathlib import Path
from esg_extraction.pipeline.runtime import resources, engine_digest
from esg_extraction.pipeline.state import now
from esg_runtime.process import observed_alive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-fingerprint',required=True)
    parser.add_argument('--reason',required=True)
    parser.add_argument('--pending-snapshot',required=True)
    args = parser.parse_args()
    _,store,_,_ = resources()
    current = engine_digest()
    pending = {r['run_id']:r for r in json.loads(Path(args.pending_snapshot).read_text())['runs']}
    upgraded = []
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for rid,body in db.execute('SELECT run_id,body FROM runs').fetchall():
            state = json.loads(body)
            if rid not in pending or state.get('engine_fingerprint') != args.from_fingerprint:
                continue
            if state.get('cancel_requested') or state['status'] not in {'queued','running','interrupted','cancelled'}:
                continue
            if (state.get('delivery') or {}).get('export_id'):
                continue
            if state.get('worker_pid') and observed_alive(state['worker_pid'],state.get('worker_started')) is not False:
                raise RuntimeError('Stop the managed runtime before upgrading pending execution')
            if state['targets']['digest'] != pending[rid]['target_digest'] or state['request'] != pending[rid]['request']:
                raise RuntimeError('Frozen business input differs from the pending snapshot')
            transition = {'at':now(),'from':args.from_fingerprint,'to':current,'reason':args.reason}
            state.setdefault('engine_upgrades',[]).append(transition)
            state.update(engine_fingerprint=current,worker_pid=None,worker_started=None)
            if state['status'] in {'interrupted','cancelled'}:
                state.update(status='queued',error=None,finished_at=None)
                progress = db.execute('SELECT body FROM run_progress WHERE run_id=?',(rid,)).fetchone()
                patch = json.loads(progress[0]) if progress else {}
                patch['current_call'] = None
                patch.setdefault('progress',{})['stage'] = 'queued'
                db.execute('INSERT INTO run_progress VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET body=excluded.body',
                    (rid,json.dumps(patch,ensure_ascii=False)))
            db.execute('UPDATE runs SET body=? WHERE run_id=?',(json.dumps(state,ensure_ascii=False),rid))
            upgraded.append({'run_id':rid,'target_digest':state['targets']['digest'],**transition})
    print(json.dumps({'upgraded':upgraded},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
