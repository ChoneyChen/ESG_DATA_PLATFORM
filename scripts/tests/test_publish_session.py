"""Operator script contract tests; no service, database or model is contacted."""
import importlib.util
from pathlib import Path

import pytest


TOOL_PATH = Path(__file__).resolve().parents[1] / "publish_session.py"
spec = importlib.util.spec_from_file_location("publish_session_test_module", TOOL_PATH)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


IDENTITY = {
    "registered": {"report_id": "report-1", "revision": 2},
    "proposal": {"title": "示例报告", "period_start": "2025-01-01", "period_end": "2025-12-31", "language": "zh"},
}


def test_identity_read_failure_is_not_treated_as_report_data(monkeypatch):
    calls = []

    def call(method, path, body=None, base=None):
        calls.append((method, path))
        if path.endswith("/report-identity"):
            return 200, IDENTITY
        return 500, {"message": "database unavailable"}

    monkeypatch.setattr(tool, "call", call)
    with pytest.raises(SystemExit, match="读取报告详情失败"):
        tool.confirm_identity("plan-1", "发布者", "org:demo", "listed_company", "C0000", "CN", None, False)
    assert calls[0][0] == "GET"


def test_identity_is_returned_when_registered_and_unchanged(monkeypatch):
    current = {"report_id": "report-1", "revision": 2,
               "metadata": {"title": "示例报告", "publisher_name": "发布者",
                            "publisher_entity_ref": "org:demo", "period_start": "2025-01-01",
                            "period_end": "2025-12-31", "language": "zh", "report_kind": "sustainability"}}

    def call(method, path, body=None, base=None):
        if path.endswith("/report-identity"):
            return 200, IDENTITY
        return 200, current

    monkeypatch.setattr(tool, "call", call)
    report = tool.confirm_identity("plan-1", "发布者", "org:demo", "listed_company", "C0000", "CN", None, False)
    assert report["report_id"] == "report-1" and report["revision"] == 2


def test_entity_list_failure_stops_before_delivery(monkeypatch):
    monkeypatch.setattr(tool, "call", lambda *args, **kwargs: (503, {"message": "not ready"}))
    with pytest.raises(SystemExit, match="读取发布主体失败"):
        tool.ensure_entity("发布者", "org:demo", "listed_company", "C0000", "CN")


def test_candidate_read_failure_is_reported(monkeypatch):
    monkeypatch.setattr(tool, "call", lambda *args, **kwargs: (503, {"message": "not ready"}))
    with pytest.raises(SystemExit, match="读取候选失败"):
        tool.candidate_report("report-1")
