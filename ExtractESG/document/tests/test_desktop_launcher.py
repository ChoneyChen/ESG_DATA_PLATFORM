"""The desktop controller must not stop unrelated or active work."""
import importlib.util
from pathlib import Path
import pytest


def controller(monkeypatch,tmp_path):
    file=Path(__file__).resolve().parents[2]/'scripts/workbench.py'
    spec=importlib.util.spec_from_file_location('desktop_controller',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'CONTROL',tmp_path/'control')
    monkeypatch.setattr(module,'REGISTRY',tmp_path/'control/launcher.json')
    return module


def test_stop_never_touches_a_reused_or_pid_replaced_service(monkeypatch,tmp_path):
    m=controller(monkeypatch,tmp_path);stopped=[]
    monkeypatch.setattr(m,'identity',lambda pid:'current' if pid in {11,12} else 'new-owner')
    monkeypatch.setattr(m.os,'killpg',lambda pid,sig:stopped.append(pid))
    m.stop_owned({'services':{'ours':{'pid':11,'identity':'current','owned':True},
        'reused':{'pid':12,'identity':'current','owned':False},
        'replaced':{'pid':13,'identity':'old-owner','owned':True}}})
    assert stopped==[11]


def test_stop_refuses_an_active_extraction(monkeypatch,tmp_path):
    m=controller(monkeypatch,tmp_path)
    monkeypatch.setattr(m,'load',lambda:{'supervisor':{'pid':1,'identity':'same'}})
    monkeypatch.setattr(m,'belongs',lambda row:True)
    monkeypatch.setattr(m,'active',lambda:1)
    monkeypatch.setattr(m.os,'kill',lambda *args:pytest.fail('Active task must remain running'))
    with pytest.raises(RuntimeError,match='仍有任务'):m.stop()


def test_uncertain_task_observation_is_not_idle(monkeypatch,tmp_path):
    m=controller(monkeypatch,tmp_path)
    monkeypatch.setattr(m,'get',lambda *args,**kwargs:(_ for _ in ()).throw(OSError('unavailable')))
    monkeypatch.setattr(m,'listening',lambda port:True)
    with pytest.raises(RuntimeError,match='无法确认'):m.active()


def test_another_checkout_owner_is_not_stopped(monkeypatch,tmp_path):
    m=controller(monkeypatch,tmp_path)
    monkeypatch.setattr(m,'load',lambda:{'root':'/another/checkout'})
    monkeypatch.setattr(m.os,'kill',lambda *args:pytest.fail('Other checkout must be retained'))
    with pytest.raises(RuntimeError,match='另一工作区'):m.stop()


def test_desktop_entry_is_current_relocatable_and_executable(tmp_path):
    file=Path(__file__).resolve().parents[2]/'scripts/install_desktop_launcher.py'
    spec=importlib.util.spec_from_file_location('desktop_installer',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    destination=tmp_path/'启动工作台.command'
    module.install(destination)
    body=destination.read_text()
    assert 'start.sh' in body and 'start_local_workbenches' not in body
    assert 'ESG_PROJECT_ROOT' in body and 'TASK_RESULT=' in body
    assert destination.stat().st_mode&0o100
