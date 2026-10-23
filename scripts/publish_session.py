#!/usr/bin/env python3
"""Run one extraction session through delivery and report what publishing needs.

This is the operator-facing end-to-end entry point: it confirms the report
identity, registers the publisher, delivers the frozen result bundles, and then
prints every candidate with the exact reason it can or cannot be published.
Nothing is approved or published here — review decisions stay explicit.

    python scripts/publish_session.py --session plan-... \
        --publisher "宁德时代新能源科技股份有限公司" --entity org:catl \
        --kind listed_company --industry C3841 --country CN
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:18081"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, body: dict | None = None, base: str = BASE):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with OPENER.open(request, timeout=180) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors="replace")[:400]


def items(value) -> list[dict]:
    if isinstance(value, list):
        return value
    return value.get("items") or value.get("reports") or value.get("sessions") or []


def confirm_identity(session: str, publisher: str, entity: str, kind: str, industry: str,
                     country: str, language: str | None, force: bool, title: str | None = None) -> dict:
    status, proposal = call("GET", f"/document/api/report-sessions/{session}/report-identity")
    if status != 200:
        raise SystemExit(f"读取报告身份失败：{status} {proposal}")
    registered = proposal.get("registered")
    if not registered:
        raise SystemExit("这份 PDF 还没有登记报告身份，请先在操作台报告页登记。")
    title = title or proposal["proposal"]["title"]
    period_start = proposal["proposal"]["period_start"]
    period_end = proposal["proposal"]["period_end"]
    if not (title and period_start and period_end):
        raise SystemExit(f"报告身份不完整：title={title} period={period_start}~{period_end}")
    current = call("GET", f"/document/api/reports/{registered['report_id']}")[1]
    metadata = {**(current.get("metadata") or {}),
                "title": title, "publisher_name": publisher, "publisher_entity_ref": entity,
                "period_start": period_start, "period_end": period_end,
                "language": language or proposal["proposal"].get("language") or "zh",
                "report_kind": "sustainability"}
    if force or any((current.get("metadata") or {}).get(key) != value for key, value in metadata.items()):
        status, updated = call("PATCH", f"/document/api/reports/{registered['report_id']}",
                               {"expected_revision": current["revision"], "metadata": metadata})
        if status != 200:
            raise SystemExit(f"保存报告身份失败：{status} {updated}")
        registered = {"report_id": updated["report_id"], "revision": updated["revision"], "metadata": updated["metadata"]}
    else:
        registered["metadata"] = current.get("metadata")
    return registered


def ensure_entity(publisher: str, entity: str, kind: str, industry: str, country: str) -> str:
    status, body = call("GET", "/publication/internal/v1/entities")
    known = {row.get("entity_ref") for row in items(body)}
    if entity in known:
        return "existing"
    status, created = call("POST", "/publication/internal/v1/entities", {
        "entity_ref": entity, "name_zh": publisher, "entity_kind": kind,
        "industry_code": industry, "country_code": country, "is_demo": False,
        "metadata": {"source": "publish_session"}})
    if status not in (200, 201):
        raise SystemExit(f"登记发布主体失败：{status} {created}")
    return "registered"


def deliver(session: str, report: dict, entity: str, reason: str) -> list[dict]:
    status, body = call("POST", f"/document/api/console/report-sessions/{session}/deliver", {
        "report_id": report["report_id"], "expected_revision": report["revision"],
        "organization_ref": entity, "reason": reason})
    if status != 200:
        raise SystemExit(f"交付失败：{status} {body}")
    return body.get("deliveries") or []


def candidate_report(report_ref: str) -> list[dict]:
    status, body = call("GET", f"/publication/internal/v1/candidates?report_ref={report_ref}&limit=1000")
    if status != 200:
        raise SystemExit(f"读取候选失败：{status} {body}")
    return items(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True)
    parser.add_argument("--publisher", required=True, help="报告发布者的中文名称")
    parser.add_argument("--entity", required=True, help="稳定的主体引用，例如 org:catl")
    parser.add_argument("--kind", default="listed_company",
                        choices=["group", "listed_company", "subsidiary", "other"])
    parser.add_argument("--industry", default="unknown")
    parser.add_argument("--country", default="CN")
    parser.add_argument("--language", default=None)
    parser.add_argument("--title", default=None, help="报告正式标题；默认用文档标签（文件名），需人工确认")
    parser.add_argument("--reason", default="核对报告身份与原文后交付已保存的提取成果")
    parser.add_argument("--force-identity", action="store_true", help="即使身份未变化也重新保存")
    args = parser.parse_args(argv)

    report = confirm_identity(args.session, args.publisher, args.entity, args.kind,
                              args.industry, args.country, args.language, args.force_identity, args.title)
    entity_state = ensure_entity(args.publisher, args.entity, args.kind, args.industry, args.country)
    deliveries = deliver(args.session, report, args.entity, args.reason)
    imported = sum(1 for row in deliveries if row.get("status") == "imported")
    print(f"报告 {report['report_id']} · 主体 {args.entity}（{entity_state}）· 交付 {imported}/{len(deliveries)} 个成果包")
    for row in deliveries:
        if row.get("status") != "imported":
            print(f"  ! {row.get('job_id')} {row.get('status')}: {(row.get('detail') or {}).get('message') or row.get('reason')}")

    candidates = candidate_report(report["report_id"])
    by_code: dict[str, list[dict]] = {}
    for row in candidates:
        by_code.setdefault(row.get("source_datapoint_id") or "?", []).append(row)
    publishable, needs_context, blocked = [], [], []
    for code, rows in sorted(by_code.items()):
        for row in rows:
            reasons = row.get("reason_codes") or []
            if row.get("publication_status") == "published":
                continue
            if not reasons or set(reasons) <= {"fact_candidate", "mapping_review_required", "mapping_not_equivalent"}:
                publishable.append((code, row))
            elif any(str(reason).startswith("missing_required_dimension") or reason in {"reviewed_context_missing"} for reason in reasons):
                needs_context.append((code, row, reasons))
            else:
                blocked.append((code, row, reasons))
    print(f"\n候选 {len(candidates)} 条：可直接审核 {len(publishable)} · 需补口径 {len(needs_context)} · 阻塞 {len(blocked)}")
    for code, row, reasons in blocked:
        print(f"  阻塞 {code}: {'；'.join(str(r) for r in reasons)}")
    for code, row, reasons in needs_context:
        print(f"  需口径 {code} {row.get('raw_value_text')} {row.get('raw_unit') or ''}: {'；'.join(str(r) for r in reasons)}")
    print("\n审核与发布仍在操作台的审核页完成；本命令只到候选为止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
