"""Join immutable historical job inputs without inventing report ownership."""
from __future__ import annotations

import json
from pathlib import Path


def standalone_jobs(jobs: list[dict], sessions: list[dict], root: Path, assets: list[dict]) -> list[dict]:
    members = {job_id for session in sessions for job_id in session.get("job_ids", [])}
    members.update(job["job_id"] for session in sessions for job in session.get("jobs", []) if job.get("job_id"))
    by_sha = {asset["sha256"]: asset["asset_id"] for asset in assets}
    result = []
    root = root.resolve()
    for job in jobs:
        request = job.get("request", {})
        if request.get("report_session_id") or job["job_id"] in members:
            continue
        item = {"reference_kind": "standalone_job", "job_id": job["job_id"],
                "title": "历史独立提取", "status": job["status"], "execution_status": job["status"],
                "stage": job.get("stage"), "progress": job.get("progress"),
                "created_at": job.get("created_at"), "updated_at": job.get("updated_at"),
                "result_status": (job.get("summary") or {}).get("result_status", "not_evaluated"),
                "lifecycle": job.get("lifecycle"), "package_id": request.get("package_id"),
                "package_version": request.get("package_version"), "ir_run_id": request.get("ir_run_id"),
                "selected_metric_ids": request.get("metric_ids", []), "selected_datapoint_ids": [],
                "source_asset_id": None, "source_sha256": None, "source_reference_status": "unresolved",
                "inspection_endpoint": f"/api/jobs/{job['job_id']}/inspection", "diagnostics": []}
        directory = (root / job["job_id"]).resolve()
        try:
            if directory.parent != root:
                raise ValueError("历史成果路径越界")
            frozen_request = json.loads((directory / "input/request.json").read_text(encoding="utf-8"))
            manifest = json.loads((directory / "input/ir-manifest.json").read_text(encoding="utf-8"))
            if frozen_request.get("report_session_id"):
                continue  # durable parent reference also protects old catalog records
            if any(frozen_request.get(k) != request.get(k) for k in ("ir_run_id", "package_id", "package_version")):
                raise ValueError("固定输入与任务目录身份不一致")
            if manifest.get("run_id") != request.get("ir_run_id"):
                raise ValueError("固定结构证据身份不一致")
            digest = manifest.get("source", {}).get("pdf_sha256")
            item.update(source_sha256=digest, source_asset_id=by_sha.get(digest),
                        ir_revision=manifest.get("ir_revision"),
                        document_label=manifest.get("document", {}).get("label") or manifest.get("source", {}).get("file_name"),
                        source_reference_status="frozen_input_verified" if digest in by_sha else "asset_unresolved")
            standard = json.loads((directory / "input/standard-package.json").read_text(encoding="utf-8"))
            selected = set(request.get("metric_ids", []))
            metrics = [metric for metric in standard.get("metrics", []) if metric["metric_id"] in selected]
            item["selected_datapoint_ids"] = [metric["source_datapoint_id"] for metric in metrics if metric.get("source_datapoint_id")]
            item["disclosure_requirement"] = next((metric.get("disclosure_requirement") for metric in metrics if metric.get("disclosure_requirement")), None)
            item["title"] = f"{item['disclosure_requirement'] or item['package_id']} · 历史独立提取"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            item["diagnostics"].append({"code": "frozen_input_unavailable", "message": str(exc)})
        result.append(item)
    return result


def fixed_session_evidence(sessions: list[dict], asset: dict, manifest_reader) -> list[dict]:
    result = {}
    for session in sessions:
        request = session.get("request", {})
        run = request.get("input", {}).get("ir_run_id") or session.get("evidence_policy", {}).get("ir_run_id")
        if not run:
            continue
        if run not in result:
            item = {"ir_run_id": run, "asset_id": asset["asset_id"], "session_ids": [],
                    "state": "unavailable", "reason": "固定来源暂不可验证", "ir_revision": None,
                    "ir_available": False, "pdf_available": True}
            try:
                manifest, _ = manifest_reader("ir", run)
                if manifest.get("run_id", run) == run and manifest.get("source", {}).get("pdf_sha256") == asset["sha256"]:
                    item.update(state="verified", reason="历史会话固定来源与报告指纹一致；不代表本次处理已完成",
                                ir_revision=manifest.get("ir_revision"), ir_available=True)
            except (OSError, ValueError, KeyError):
                pass
            result[run] = item
        result[run]["session_ids"].append(session["session_id"])
    return list(result.values())
