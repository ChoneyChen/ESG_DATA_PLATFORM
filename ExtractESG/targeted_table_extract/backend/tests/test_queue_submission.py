import httpx
import pytest

from esg_targeted.cli import build_parser
from esg_targeted.queue_client import enqueue_existing


def test_normal_submission_uses_unified_queue_and_never_executes(monkeypatch):
    sent = []
    def post(url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(202, json={"task_id": "task-fixture", "native_job_id": "report-fixture"})
    monkeypatch.setattr(httpx, "post", post)
    result = enqueue_existing("report-session", "report-fixture", backend_url="http://queue.test/", qiniu_api_key="temporary-secret")
    assert result["native_job_id"] == "report-fixture"
    assert sent[0][0] == "http://queue.test/api/pipeline/tasks/report-session/report-fixture/run-existing"
    assert sent[0][1]["json"] == {"qiniu_api_key": "temporary-secret"}
    parser = build_parser()
    assert not parser.parse_args(["existing", "--job-id", "job-fixture"]).worker
    assert parser.parse_args(["existing", "--job-id", "job-fixture", "--worker"]).worker


def test_failed_submission_does_not_echo_credentials(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(409, text="temporary-secret"))
    with pytest.raises(ValueError) as failure:
        enqueue_existing("targeted-extraction", "job-fixture", qiniu_api_key="temporary-secret")
    assert "temporary-secret" not in str(failure.value)
