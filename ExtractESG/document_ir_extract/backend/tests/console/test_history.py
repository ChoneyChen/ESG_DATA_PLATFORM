import json
from esg_v2.api.console_history import standalone_jobs, fixed_session_evidence


def make_job(root, name, digest, parent=None):
    request = {"ir_run_id": "ir-"+name, "package_id": "esrs.e1-6", "package_version": "2.0.0", "metric_ids": ["metric-old"]}
    if parent:
        request["report_session_id"] = parent
    directory = root / name / "input"
    directory.mkdir(parents=True)
    for file, payload in {
        "request.json": request,
        "ir-manifest.json": {"run_id": "ir-"+name, "ir_revision": 2, "source": {"pdf_sha256": digest}},
        "standard-package.json": {"metrics": [{"metric_id": "metric-old", "source_datapoint_id": "E1-6_07", "disclosure_requirement": "E1-6"}]},
    }.items():
        (directory / file).write_text(json.dumps(payload))
    return {"job_id": name, "status": "completed", "request": request}


def test_historical_sources_join_by_sha_and_both_parent_contracts_deduplicate(tmp_path):
    jobs = [make_job(tmp_path, "job-a", "a"*64), make_job(tmp_path, "job-b", "b"*64),
            make_job(tmp_path, "job-parent-unavailable", "a"*64, "parent-missing"),
            make_job(tmp_path, "job-member", "a"*64)]
    assets = [{"asset_id": "asset-a", "sha256": "a"*64, "file_name": "same.pdf"},
              {"asset_id": "asset-b", "sha256": "b"*64, "file_name": "same.pdf"}]
    result = standalone_jobs(jobs, [{"job_ids": ["job-member"]}], tmp_path, assets)
    assert [j["job_id"] for j in result] == ["job-a", "job-b"]
    assert [j["source_asset_id"] for j in result] == ["asset-a", "asset-b"]
    assert all(j["selected_datapoint_ids"] == ["E1-6_07"] for j in result)
    assert all(j["package_version"] == "2.0.0" for j in result)
    assert all(j["result_status"] == "not_evaluated" for j in result)


def test_bad_frozen_input_remains_visible_without_hiding_other_records(tmp_path):
    good = make_job(tmp_path, "good", "a"*64)
    bad = make_job(tmp_path, "bad", "a"*64)
    (tmp_path/"bad/input/ir-manifest.json").write_text("broken")
    result = standalone_jobs([good, bad], [], tmp_path, [{"asset_id": "asset", "sha256": "a"*64}])
    assert len(result) == 2 and result[0]["source_asset_id"] == "asset"
    assert result[1]["source_reference_status"] == "unresolved" and result[1]["diagnostics"]


def test_historical_fixed_evidence_is_explicitly_separate_from_processing():
    session = {"session_id": "s", "request": {"input": {"ir_run_id": "fixed"}}}
    result = fixed_session_evidence([session], {"asset_id": "asset", "sha256": "a"*64},
        lambda *_: ({"ir_revision": 2, "source": {"pdf_sha256": "a"*64}}, None))
    assert result[0]["state"] == "verified" and result[0]["ir_revision"] == 2
    assert "不代表本次处理已完成" in result[0]["reason"]
