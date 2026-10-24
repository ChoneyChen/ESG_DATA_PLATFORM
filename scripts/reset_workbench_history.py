#!/usr/bin/env python3
"""Clear derived reading and extraction history, keeping standards and PDFs.

This removes what the workbench produced — report sessions, queue tasks,
processing runs, page-reading (OCR) versions, structure (IR) versions and
their output folders — so a run can start from zero. It never deletes PDF
assets, standard packages, the publication database or configuration.

Every deletion goes through the owning service's own DELETE endpoint with the
payload that endpoint advertises, so blockers and reservations still apply.
"""
from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18081"
ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "ExtractESG"
OUTPUT_ROOT = EXTRACT / "document_ir_extract"


def call(method: str, path: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=180) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:  # noqa: PERF203 - report, keep going
        body = exc.read()
        try:
            detail = json.loads(body)
        except ValueError:
            detail = body.decode(errors="replace")[:200]
        return exc.code, detail


def rows(path: str, *keys: str) -> list[dict]:
    status, body = call("GET", path)
    if status != 200:
        print(f"  ! {path} -> {status} {body}")
        return []
    if isinstance(body, list):
        return body
    for key in keys:
        if isinstance(body.get(key), list):
            return body[key]
    return []


def delete(service_path: str, identity: str, action: dict | None, label: str) -> bool:
    payload = dict((action or {}).get("payload") or {})
    if not payload:
        print(f"  - {label} {identity}: no advertised delete identity, skipped")
        return False
    status, body = call("DELETE", service_path, payload)
    ok = status in (200, 202) and (body.get("deleted") is True or body.get("status") in {"deleted", "deletion_pending"})
    print(f"  {'✓' if ok else '!'} {label} {identity}: {status} {body.get('status') or body}")
    return ok


def delete_action(record: dict) -> dict | None:
    return next((item for item in record.get("available_actions") or [] if item.get("action") == "delete"), None)


def clear(dry: bool) -> None:
    sessions = rows("/document/api/report-sessions?include_archived=true")
    tasks = rows("/document/api/pipeline/tasks?include_archived=true")
    runs = rows("/document/api/report-processing/runs?include_archived=true", "runs")
    ocr = rows("/document/api/ocr-library", "items")
    ir = rows("/document/api/document-ir/jobs")
    print(f"sessions {len(sessions)} · tasks {len(tasks)} · runs {len(runs)} · ocr {len(ocr)} · ir {len(ir)}")
    if dry:
        return
    print("报告会话")
    for record in sessions:
        identity = record.get("session_id")
        detail = call("GET", f"/document/api/report-sessions/{identity}")[1]
        delete(f"/document/api/report-sessions/{identity}", identity, delete_action(detail or record), "会话")
    print("执行队列")
    for record in tasks:
        identity = record.get("task_id")
        delete(f"/document/api/pipeline/tasks/{identity}", identity, delete_action(record), "任务")
    print("处理任务")
    # Deleting the sessions above moves each run's revision, so the delete
    # identity is re-read right before use instead of trusting the list snapshot.
    for record in runs:
        identity = record.get("processing_id")
        detail = call("GET", f"/document/api/report-processing/runs/{identity}")[1]
        delete(f"/document/api/report-processing/runs/{identity}", identity, delete_action(detail or record), "处理")
    print("页面读取版本")
    # Deleting one version moves the library revision, so each identity is
    # re-read right before its delete instead of trusting the list snapshot.
    for record in ocr:
        identity = record.get("run_id")
        detail = call("GET", f"/document/api/ocr-library/{identity}")[1]
        delete(f"/document/api/ocr-library/{identity}", identity, delete_action(detail or record), "读取")
    print("结构证据版本")
    for record in rows("/document/api/document-ir/jobs"):
        identity = record.get("run_id")
        detail = call("GET", f"/document/api/document-ir/jobs/{identity}/deletion")[1]
        delete(f"/document/api/document-ir/jobs/{identity}", identity, delete_action(detail or {}), "结构")
    print("原生提取会话")
    for record in rows("/targeted/api/report-sessions") or []:
        identity = record.get("session_id")
        delete(f"/targeted/api/report-sessions/{identity}", identity, delete_action(record), "原生会话")
    print("原生提取任务")
    # Standalone projection jobs are deleted through the same permanent-deletion
    # endpoint the workbench UI uses; parent-owned ones disappear with their session.
    for record in rows("/targeted/api/jobs") or []:
        identity = record.get("job_id")
        detail = call("GET", f"/targeted/api/jobs/{identity}/deletion")[1]
        delete(f"/targeted/api/jobs/{identity}", identity, delete_action(detail or {}), "原生任务")
    print("遗留目录")
    for folder in (OUTPUT_ROOT / "document_ir_output",
                   EXTRACT / "document_ir_extract" / "ocr_output",
                   EXTRACT / "targeted_table_extract" / "targeted_extract_output"):
        if not folder.is_dir():
            continue
        for child in folder.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        print(f"  ✓ 清空 {folder}")
    print("完成。PDF 资产、标准包与发布数据库未改动。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="actually delete; without it only counts are printed")
    args = parser.parse_args()
    clear(dry=not args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
