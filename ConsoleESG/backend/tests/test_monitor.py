import json
from esg_console.monitor import monitor_snapshot


def test_monitor_is_an_explicit_projection_and_keeps_revision_stable():
    state={'session_id':'session','name':'report','status':'completed','current_phase':'finished','phase_label':'执行结束',
        'request':{'input':{'asset_id':'pdf-one'},'secret':'not-public'},'payload':{'path':'private-path'},
        'progress':{'current':1,'total':1},'run':{'summary':{'source_facts':3},'current_call':None}}
    snapshot=monitor_snapshot([state])
    assert snapshot.revision==monitor_snapshot([state]).revision
    raw=snapshot.model_dump_json()
    assert 'private-path' not in raw and 'not-public' not in raw
    assert snapshot.active_count==0 and snapshot.runs[0].facts==3
