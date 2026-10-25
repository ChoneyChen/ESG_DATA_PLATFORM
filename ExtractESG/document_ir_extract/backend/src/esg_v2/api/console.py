"""Operator projections and explicit candidate handoff; no duplicated job state."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from esg_runtime.local_operator import operator_headers
from esg_v2.control_plane.crawl_index import CrawlIndexReader
from .console_history import standalone_jobs, fixed_session_evidence
from .report_session_read import summarize_report_session
from .console_delivery_identity import (
    DeliveryScopeUnavailable, resolve_delivery_query, unknown_delivery, validate_sql_projection,
)


class DeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(min_length=1,max_length=160)
    organization_ref: str | None = Field(default=None,max_length=160)
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="交付已保存的提取成果进入候选审核",min_length=1,max_length=2000)


class MaterializationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000)
    job_ids: list[str] | None = None


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(default="提取完成自动交付进入审核", min_length=1, max_length=2000)
    report_id: str | None = Field(default=None, max_length=160)


def console_router(processing, plans, session_detail, targeted, *, publication_url=None, output_root=None, crawl_index=None):
    router = APIRouter(prefix="/api/console")
    crawl = crawl_index or CrawlIndexReader()
    base = (publication_url or os.getenv("ESG_PUBLICATION_API_URL","http://127.0.0.1:8765")).rstrip("/")
    root = Path(output_root or os.getenv("ESG_TARGETED_OUTPUT_ROOT",
        Path(__file__).resolve().parents[5]/"targeted_table_extract/targeted_extract_output")).resolve()

    def publication(method,path,authorization=None,payload=None):
        try:
            response = requests.request(method,base+path,json=payload,
                headers={"Authorization":authorization} if authorization else {},timeout=15,allow_redirects=False)
            if response.status_code>=300:
                raise HTTPException(response.status_code, response.json().get("detail",response.json()))
            return response.json()
        except (requests.RequestException,ValueError) as exc:
            raise HTTPException(503,{"code":"publication_unavailable","layer":"publication",
                "message":"发布数据库暂不可读；已保存的报告与提取成果保留","retryable":True}) from exc

    def sessions_for(asset):
        selected = []
        for plan in plans.list():
            if not plan.get("report_request"):
                continue
            document = plan["documents"][0]
            matches = document.get("asset_id")==asset["asset_id"] or document.get("source_sha256")==asset["sha256"]
            if not matches and document.get("ir_run_id"):
                try:
                    manifest,_=processing._manifest("ir",document["ir_run_id"])
                    matches=manifest.get("source",{}).get("pdf_sha256")==asset["sha256"]
                except (FileNotFoundError,ValueError):
                    pass
            if matches:
                selected.append(summarize_report_session(session_detail(plan)))
        return selected

    def historical_catalog(assets, include_archived=False):
        try:
            jobs=targeted("GET",f"/api/jobs?limit=500&include_archived={str(include_archived).lower()}")
            sessions=targeted("GET","/api/report-sessions?limit=500&include_archived=true")
            return {"items":standalone_jobs(jobs,sessions,root,assets),
                    "status":"partial" if len(jobs)>=500 or len(sessions)>=500 else "available",
                    "limit":500,"truncated":len(jobs)>=500 or len(sessions)>=500}
        except (HTTPException, TypeError, ValueError) as exc:
            return {"items":[],"status":"unavailable","detail":getattr(exc,"detail",str(exc))}

    def report_projection(asset,authorization,summary=None,history=None):
        result={**asset,**processing.asset_summary(asset)}
        result["extraction_sessions"]=sessions_for(asset)
        result["processing_runs"]=[processing.detail(s) for s in processing.list(True) if s["asset_id"]==asset["asset_id"]]
        history = history if history is not None else historical_catalog([asset], True)
        result["standalone_jobs"]=[job for job in history["items"] if job["source_asset_id"]==asset["asset_id"]]
        result["historical_fixed_evidence"]=fixed_session_evidence(result["extraction_sessions"],asset,processing._manifest)
        current=result.get("current_evidence") or {}
        ready=current.get("ready") is True and bool(result.get("ir_run_id"))
        result["current_processing_evidence"]={**current,"state":"verified" if ready else "unavailable",
            "asset_id":asset["asset_id"],"pdf_available":True,"ir_available":ready,
            "ir_run_id":result.get("ir_run_id") if ready else None,
            "reason":"当前处理成果已通过使用条件检查" if ready else "当前处理尚无通过使用条件检查的结构证据；历史固定引用单独列示"}
        result["selected_datapoint_ids"]=sorted({original for session in result["extraction_sessions"]
            for original in (session.get("request",{}).get("selection_snapshot") or {}).get("source_datapoint_ids",[])})
        result["review_count"]=None
        result["published_count"]=None
        result["dependencies"]={}
        result["dependencies"]["historical_extractions"]={k:v for k,v in history.items() if k!="items"}
        if authorization and result.get("report_id"):
            try:
                summaries=summary if summary is not None else publication("GET","/internal/v1/report-summaries",authorization)
                if summaries.get("error"):
                    raise HTTPException(503,summaries["error"])
                matching=next((item for item in summaries["items"] if item["report_ref"]==result["report_id"]),{})
                result["review_count"]=matching.get("review_count",0)
                result["published_count"]=matching.get("published_count",0)
                result["candidate_mapping_count"]=matching.get("candidate_mapping_count",0)
            except HTTPException as exc:
                result["dependencies"]["publication"]={"status":"unavailable","detail":exc.detail}
        else:
            result["dependencies"]["publication"]={"status":"credentials_required"}
        result["next_action"] = "confirm_metadata" if not all(result.get("report_metadata",{}).get(k) for k in
            ("title","publisher_entity_ref","period_start","period_end")) else "process_report" if not result.get("current_evidence",{}).get("ready") else "review" if result["review_count"] else "extract"
        return result

    @router.get("/crawl-index/status")
    def crawl_status():
        """Read-only crawl connection state; ESEF ZIPs are not PDF assets."""
        return crawl.status()

    @router.get("/crawl-index")
    def crawl_reports(query: str = Query(default="", max_length=160), fiscal_year: int | None = Query(default=None, ge=1900, le=2200),
                      limit: int = Query(default=50, ge=1, le=100)):
        """Only expose local filing candidates; confirmation stays with the operator."""
        return crawl.reports(query=query, fiscal_year=fiscal_year, limit=limit)

    @router.get("/crawl-index/companies")
    def crawl_companies(query: str = Query(default="", max_length=160), limit: int = Query(default=50, ge=1, le=100)):
        return crawl.companies(query=query, limit=limit)

    @router.get("/reports")
    def reports(include_archived: bool=False,authorization: str | None=Header(default=None)):
        summary=None
        if authorization:
            try:
                summary=publication("GET","/internal/v1/report-summaries",authorization)
            except HTTPException as exc:
                summary={"error":exc.detail}
        assets=processing.assets.refresh(include_archived=include_archived)["assets"]
        history=historical_catalog(assets,include_archived)
        return {"schema_version":"console-report-v1","items":[report_projection(a,authorization,summary,history) for a in assets]}

    @router.get("/standalone-jobs")
    def historical_jobs(include_archived: bool=False):
        assets=processing.assets.refresh(include_archived=True)["assets"]
        return {"schema_version":"console-standalone-v1",**historical_catalog(assets,include_archived)}

    @router.get("/reports/{asset_id}")
    def report(asset_id: str,authorization: str | None=Header(default=None)):
        try:
            return report_projection(processing.assets.get(asset_id),authorization)
        except KeyError as exc:
            raise HTTPException(404,"报告资产不存在") from exc

    @router.get('/report-sessions/{session_id}/delivery-status')
    def delivery_status(session_id: str, report_id: str | None = Query(default=None, min_length=1, max_length=160),
                        authorization: str | None = Header(default=None)):
        if operator_headers(authorization) is None:
            raise HTTPException(401, '查看 SQL 交付状态需要工作台操作员授权')
        scope = None
        try:
            principal = publication('GET', '/internal/v1/me', authorization)
            if not isinstance(principal, dict) or not set(principal.get('roles', [])) & {'read', 'admin'}:
                raise HTTPException(403, '当前操作员没有交付状态读取权限')
            payload, scope = resolve_delivery_query(processing, plans, session_detail, root, session_id, report_id)
            result = publication('POST', '/internal/v1/delivery-status/query', authorization, payload)
            result = validate_sql_projection(result, payload)
            return {**result, **scope,
                'available': result['status'] not in {'unknown', 'identity_mismatch', 'artifact_unavailable'},
                'blockers': result.get('reason_messages', [])}
        except DeliveryScopeUnavailable as exc:
            return unknown_delivery(session_id, exc.code, execution=exc.execution or (scope or {}).get('execution'),
                report_options=exc.report_options, status=exc.status)
        except HTTPException as exc:
            if exc.status_code in {401, 403}:
                raise
            return unknown_delivery(session_id, 'publication_unavailable', execution=(scope or {}).get('execution'),
                report_options=(scope or {}).get('report_options'))
        except (OSError, ValueError, TypeError, KeyError):
            return unknown_delivery(session_id, 'delivery_projection_invalid', execution=(scope or {}).get('execution'))

    @router.post("/report-sessions/{session_id}/deliver")
    def deliver(session_id: str,request: DeliveryRequest,authorization: str | None=Header(default=None)):
        if operator_headers(authorization) is None:
            raise HTTPException(401,"交付候选需要工作台操作员授权")
        principal=publication("GET","/internal/v1/me",authorization)
        if not set(principal["roles"]) & {"review","admin"}:
            raise HTTPException(403,"当前操作员没有候选交付权限")
        plan=next((p for p in plans.list() if p.get("report_request") and (p["plan_id"]==session_id
            or p["documents"][0].get("native_session_id")==session_id)),None)
        if not plan:
            raise HTTPException(404,"报告提取会话不存在")
        if plan["status"] in {"active","cancelling"}:
            raise HTTPException(409,"等待当前提取动作保存并结束后再交付，避免读取正在写入的成果")
        try:
            report=processing.report(request.report_id)
        except KeyError as exc:
            raise HTTPException(404,"请先登记报告身份") from exc
        if report["revision"]!=request.expected_revision:
            raise HTTPException(409,"报告元信息已变化，请刷新再交付")
        metadata=report["metadata"]
        missing=[k for k in ("title","publisher_entity_ref","period_start","period_end") if not metadata.get(k)]
        if missing:
            raise HTTPException(409,{"code":"report_identity_incomplete","field_blockers":missing})
        if request.organization_ref and request.organization_ref!=metadata["publisher_entity_ref"]:
            raise HTTPException(409,"交付主体与已确认的报告主体不一致")
        session=session_detail(plan)
        doc=plan["documents"][0]
        if not doc.get("ir_run_id"):
            raise HTTPException(409,"会话尚未固定结构证据")
        if targeted and not session.get("delivery_artifacts") and doc.get("native_session_id"):
            # Terminal sessions created before automatic materialization still
            # deliver: the bundle is content-addressed, so saving it here is safe.
            try:
                targeted("POST",f"/api/report-sessions/{quote(str(doc['native_session_id']),safe='')}/materialize",
                    {"reason":"交付前保存可交付成果"})
                session=session_detail(plan)
            except HTTPException:
                pass
        manifest,_=processing._manifest("ir",doc["ir_run_id"])
        try:
            processing.integrity_check("ir",doc["ir_run_id"])
        except (OSError,ValueError,KeyError) as exc:
            raise HTTPException(409,{"code":"fixed_evidence_invalid","message":"固定结构证据未通过完整性检查，交付被阻止"}) from exc
        source_sha=manifest.get("source",{}).get("pdf_sha256")
        asset_version=next((v for v in report["asset_versions"] if v["source_sha256"]==source_sha),None)
        if not asset_version:
            raise HTTPException(409,"所选报告与会话的固定 PDF 来源不一致")
        source_asset=processing.assets.get(asset_version["asset_id"])
        if source_asset["sha256"]!=source_sha:
            raise HTTPException(409,"原始PDF已变化，交付被阻止")
        deliveries=[]
        artifacts=session.get('delivery_artifacts')
        for job in session.get("jobs",[]):
            job_id=job["job_id"]
            job_root=(root/job_id).resolve()
            if job_root.parent!=root:
                raise HTTPException(409,'成果任务路径不在授权目录中')
            selected=None
            if artifacts is not None:
                choices=[item for item in artifacts if item.get('job_id')==job_id and item.get('status')=='available']
                selected=max(choices,key=lambda item:item.get('created_at',''),default=None)
                if not selected:
                    deliveries.append({'job_id':job_id,'status':'blocked','reason':'尚无已验证的冻结成果版本'})
                    continue
                relative=selected.get('bundle_relative_path')
                revision=selected.get('artifact_revision_id')
                if relative!='.' and relative!=f'terminal-revisions/{revision}':
                    raise HTTPException(409,'冻结成果版本路径无效')
                bundle=(job_root/relative).resolve()
                if bundle!=job_root and (bundle.parent!=job_root/'terminal-revisions' or not bundle.name.startswith('partial-')):
                    raise HTTPException(409,'冻结成果版本路径越界')
            else:
                bundle=job_root  # Compatibility with a pre-revision extraction service.
            if not (bundle/"manifest.json").is_file():
                deliveries.append({"job_id":job_id,"status":"blocked","reason":"规范成果尚未保存"})
                continue
            try:
                if selected:
                    frozen_manifest=json.loads((bundle/'manifest.json').read_text())
                    if frozen_manifest.get('job_id')!=job_id or hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest()!=selected.get('manifest_sha256'):
                        raise HTTPException(409,'冻结成果清单或原任务身份已变化')
                request_snapshot=json.loads((bundle/"input/request.json").read_text())
                fixed_ir=json.loads((bundle/"input/ir-manifest.json").read_text())
            except (OSError,ValueError) as exc:
                deliveries.append({"job_id":job_id,"status":"blocked","reason":"规范成果的输入快照缺失或无效"})
                continue
            if fixed_ir!=manifest:
                raise HTTPException(409,"投影的固定证据清单与来源成果不一致，交付被阻止")
            if request_snapshot.get("ir_run_id")!=doc["ir_run_id"]:
                raise HTTPException(409,"投影成果引用了不同的结构证据，交付被阻止")
            payload={"bundle_path":str(bundle),"report_metadata":{
                "upstream_system":"ExtractESG","organization_ref":metadata["publisher_entity_ref"],
                "report_ref":report["report_id"],"report_version_ref":f"{report['report_id']}:asset:{asset_version['version']}",
                "source_artifact_ref":source_asset["asset_id"],"source_sha256":source_sha,
                "report_title":metadata["title"],"language_code":metadata.get("language") or "zh",
                "report_period_start":metadata["period_start"],"report_period_end":metadata["period_end"]}}
            if request_snapshot.get("selection_snapshot"):
                payload["selection_snapshot"]=request_snapshot["selection_snapshot"]
            try:
                imported=publication("POST","/internal/v1/deliveries",authorization,payload)
                deliveries.append({"job_id":job_id,**({'artifact_revision_id':selected['artifact_revision_id'],
                    'saved_source_fact_count':selected['source_fact_count'],'saved_core_fact_count':selected['core_fact_count']} if selected else {}),**imported})
            except HTTPException as exc:
                deliveries.append({"job_id":job_id,"status":"acknowledgement_unknown" if exc.status_code==503 else "blocked",
                    "receipt_id":None,"detail":exc.detail,"next_action":"retry_same_delivery"})
        return {"session_id":session_id,"report_id":report["report_id"],"deliveries":deliveries,
            "status":"imported" if deliveries and all(d["status"]=="imported" for d in deliveries) else "partial" if any(d["status"]=="imported" for d in deliveries) else "blocked",
            "review_url":"/#/review?report="+quote(report["report_id"],safe="")}

    @router.post("/report-sessions/{session_id}/handoff")
    def handoff(session_id: str, request: HandoffRequest | None = None,
                authorization: str | None = Header(default=None)):
        """One automatic step from a finished extraction to reviewable candidates.

        Saving the immutable bundle and delivering it are both content-addressed
        and idempotent, so repeating this call after a crash converges on the
        same state instead of duplicating candidates.
        """
        if operator_headers(authorization) is None:
            raise HTTPException(401, "自动衔接需要工作台操作员授权")
        principal = publication("GET", "/internal/v1/me", authorization)
        if not set(principal.get("roles", [])) & {"review", "admin"}:
            raise HTTPException(403, "当前操作员没有候选交付权限")
        reason = (request.reason if request else None) or "提取完成自动交付进入审核"
        plan = next((p for p in plans.list() if p.get("report_request") and (p["plan_id"] == session_id
            or p["documents"][0].get("native_session_id") == session_id)), None)
        if not plan:
            raise HTTPException(404, "报告提取会话不存在")
        document = plan["documents"][0]
        native_id = document.get("native_session_id") or plan["plan_id"]
        if plan["status"] in {"planned", "active", "cancelling"}:
            return {"session_id": plan["plan_id"], "status": "waiting", "stage": "extraction",
                "plan_status": plan["status"], "message": "提取仍在执行；结束后自动保存成果并交付审核"}
        session = session_detail(plan)
        if session.get("execution_status") in {"queued", "running"}:
            return {"session_id": plan["plan_id"], "status": "waiting", "stage": "extraction",
                "plan_status": plan["status"], "message": "提取仍在执行；结束后自动保存成果并交付审核"}
        available = [a for a in (session.get("delivery_artifacts") or []) if a.get("status") == "available"]
        if not available:
            try:
                targeted("POST", f"/api/report-sessions/{quote(str(native_id), safe='')}/materialize",
                    {"reason": reason})
            except HTTPException as exc:
                if exc.status_code in {401, 403}:
                    raise
                return {"session_id": plan["plan_id"], "status": "blocked", "stage": "materialize",
                    "message": "可交付成果未能保存", "detail": exc.detail}
            session = session_detail(plan)
            available = [a for a in (session.get("delivery_artifacts") or []) if a.get("status") == "available"]
        if not available and session.get("delivery_counts", {}).get("available_bundle_count") is None:
            return {"session_id": plan["plan_id"], "status": "no_artifacts", "stage": "materialize",
                "message": "本次提取没有可交付的来源事实"}
        reports = processing.reports()
        if request and request.report_id:
            report = next((r for r in reports if r["report_id"] == request.report_id), None)
        else:
            bound = [r for r in reports if any(v["asset_id"] == document.get("asset_id")
                or v["source_sha256"] == document.get("source_sha256") for v in r.get("asset_versions", []))]
            report = bound[0] if len(bound) == 1 else None
        if not report:
            return {"session_id": plan["plan_id"], "status": "needs_identity", "stage": "identity",
                "message": "尚未登记这份 PDF 的报告身份，请确认后自动交付",
                "report_options": [r["report_id"] for r in reports]}
        metadata = report.get("metadata") or {}
        missing = [k for k in ("title", "publisher_entity_ref", "period_start", "period_end") if not metadata.get(k)]
        if missing:
            return {"session_id": plan["plan_id"], "status": "needs_identity", "stage": "identity",
                "report_id": report["report_id"], "field_blockers": missing,
                "message": "报告身份不完整，请补全后再交付"}
        try:
            result = deliver(session_id, DeliveryRequest(report_id=report["report_id"],
                organization_ref=metadata["publisher_entity_ref"], expected_revision=report["revision"],
                reason=reason), authorization)
        except HTTPException as exc:
            if exc.status_code in {401, 403}:
                raise
            return {"session_id": plan["plan_id"], "status": "blocked", "stage": "deliver",
                "report_id": report["report_id"], "message": "候选交付被阻止", "detail": exc.detail}
        reviews = [d["auto_review"] for d in result.get("deliveries", [])
            if isinstance(d.get("auto_review"), dict)]
        automatic = None
        if reviews:
            automatic = {"rule_version": next((r.get("verification_rule_version") for r in reviews
                if r.get("verification_rule_version")), None),
                "approved": sum(int(r.get("applied") or 0) for r in reviews),
                "needs_human": sum(int(r.get("needs_human") or 0) for r in reviews),
                "failed": sum(1 for r in reviews if r.get("status") == "failed")}
        return {"session_id": plan["plan_id"], "status": result["status"],
            "stage": "review" if result["status"] in {"imported", "partial"} else "deliver",
            "report_id": report["report_id"], "deliveries": result.get("deliveries", []),
            "auto_review": automatic, "review_url": result.get("review_url"),
            "message": "已自动交付到候选审核" if result["status"] == "imported" else "部分成果包未能交付"}

    # Manual handoff and batch production use the same immutable delivery
    # command, including report revision, evidence identity and import receipts.
    router.deliver_candidates = deliver
    router.handoff = handoff
    return router
