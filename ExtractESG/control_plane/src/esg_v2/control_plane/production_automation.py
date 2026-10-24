"""Durable ordered batch production above the existing single worker queue.

No model execution, worker slot, publication approval, or persisted credential is
owned here. Dispatch intent uses stable child identities so a process restart
cannot silently duplicate a report's preparation or extraction plan.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import threading
import time
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from esg_v2.control_plane.contracts import utc_now
from esg_v2.control_plane.report_processing import ProcessingConfig, ProcessingRequest, ProcessingActionRequest
from esg_v2.control_plane.shared_contracts import ReportExtractionRequest
from esg_runtime.local_operator import acting_for, held_authorization

SCHEMA = "production-automation-v1"
TERMINAL = {"completed", "partial", "cancelled"}
ITEM_TERMINAL = {"completed", "partial", "failed", "cancelled"}
STATUS_LABELS = {"planned": "待部署", "active": "自动执行中", "pausing": "当前阶段收尾后暂停",
    "paused": "已暂停", "waiting_credentials": "等待重新连接操作员权限", "completed": "全部完成",
    "partial": "部分完成，请查看未完成报告", "cancelling": "正在取消", "cancelled": "已取消"}
STAGE_LABELS = {"pending": "等待顺序推进", "processing": "报告处理", "extraction": "所选条款提取",
    "delivery": "交付候选审核", "completed": "生产完成"}


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AutomationItem(Strict):
    asset_id: str = Field(min_length=1, max_length=160)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_id: str | None = Field(default=None, min_length=1, max_length=160)
    report_revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def report_pair(self):
        if bool(self.report_id) != (self.report_revision is not None):
            raise ValueError("报告身份与确认版本必须一起提供")
        return self


class AutomationProcessing(Strict):
    mode: Literal["auto", "rebuild_ir", "reprocess"] = "auto"
    config: ProcessingConfig = Field(default_factory=ProcessingConfig)

    @model_validator(mode="after")
    def local_only(self):
        if self.config.allow_cloud_ocr or self.config.ocr_provider != "local_paddleocr" or self.config.review_provider == "qiniu":
            raise ValueError("持久批量自动化本轮仅支持本地处理；云授权凭据不能保存到计划")
        return self


class AutomationRequest(Strict):
    name: str = Field(default="批量报告生产", min_length=1, max_length=120)
    items: list[AutomationItem] = Field(min_length=1, max_length=500)
    target: Literal["processing", "processing_extraction", "processing_extraction_delivery"] = "processing"
    processing: AutomationProcessing = Field(default_factory=AutomationProcessing)
    extraction: ReportExtractionRequest | None = None
    failure_policy: Literal["continue", "pause"] = "continue"

    @model_validator(mode="after")
    def coherent(self):
        if len({x.asset_id for x in self.items}) != len(self.items) or len({x.source_sha256 for x in self.items}) != len(self.items):
            raise ValueError("同一批次的报告来源不可重复")
        if (self.target == "processing") != (self.extraction is None):
            raise ValueError("提取目标必须提供所选条款；仅处理目标不应带提取配置")
        if self.target == "processing_extraction_delivery" and any(not i.report_id for i in self.items):
            raise ValueError("自动交付需要每份报告的已确认身份及版本")
        if self.extraction:
            role = self.extraction.role_config
            if self.extraction.qiniu_api_key is not None or self.extraction.ocr_token is not None or role.cloud_authorization.allowed or role.cloud_authorization.allow_ocr_api or any(
                    r.provider == "qiniu_vlm" for r in (role.semantic_brain, role.specialist)):
                raise ValueError("持久自动化只支持本地角色，计划不接收或保存模型凭据")
        return self


class AutomationCreateRequest(Strict):
    request: AutomationRequest
    preview_id: str = Field(pattern=r"^automation-preview-[a-f0-9]{64}$")
    submission_key: str = Field(min_length=1, max_length=160)
    start: bool = Field(default=True, strict=True)


class AutomationAction(Strict):
    expected_revision: int = Field(ge=1, strict=True)
    reason: str = Field(default="操作员调整批量生产", min_length=1, max_length=2000)


class AutomationOrder(AutomationAction):
    item_ids: list[str] = Field(min_length=1, max_length=500)


def _automation_plan_matches(plan: dict, key: str) -> bool:
    """Correlation is separate from the label shown to an operator."""
    if plan.get("automation_key") == key:
        return True
    if any(document.get("automation_key") == key for document in plan.get("documents", [])):
        return True
    # Plans written before the display-name split remain resumable.
    return (plan.get("report_request") or {}).get("name") == "自动生产:" + key


def foundation_processing_config(processing, source_sha256: str, config: ProcessingConfig) -> ProcessingConfig:
    """Preparation for a foundation16 extraction of one PDF.

    A compatible full IR is reused as is; a compatible full OCR is built into a
    review-free IR; otherwise only the candidate pages are recognised.
    """
    fast = {"execute_required_reviews": False, "execute_optional_reviews": False, "allow_limited_evidence": True}
    review_free = config.model_copy(update=fast)
    targeted = config.model_copy(update={**fast, "page_selection": "foundation16"})

    def reusable(candidate: ProcessingConfig, stage: str) -> bool:
        state = {"source_sha256": source_sha256, "engine_versions": processing.engine_versions,
                 "request": {"config": candidate.model_dump(mode="json")}, "reuse_diagnostics": []}
        return bool(processing._find_reusable(state, stage))

    for candidate, stage in ((config, "ir"), (review_free, "ir"), (targeted, "ir"), (review_free, "ocr")):
        if reusable(candidate, stage):
            return candidate
    return targeted


def readonly_asset(assets, asset_id, *, allow_archived=False):
    """Read the indexed PDF and hash its bytes without refreshing/writing catalog."""
    rows = assets._load_raw().get("assets", [])
    item = next((deepcopy(x) for x in rows if x.get("asset_id") == asset_id), None)
    if not item:
        raise KeyError(asset_id)
    path = (assets.root / item["relative_path"]).resolve()
    if path.parent != assets.root.resolve() or path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError("原始 PDF 已不存在或路径无效")
    checksum = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    if checksum.hexdigest() != item["sha256"]:
        raise ValueError("原始 PDF 已变化，请先刷新报告资产")
    lifecycle = assets._read_lifecycle().get(asset_id, {"state": "active"})
    if lifecycle.get("state") not in ({"active", "archived"} if allow_archived else {"active"}):
        raise ValueError("该报告已归档或移入回收区，请恢复后再提交")
    return item


class ProductionAutomationCoordinator:
    def __init__(self, store, processing, plans, *, extraction_preview, extraction_detail,
                 deliver, authorize, require_ready=None, asset_lookup=None):
        self.store, self.processing, self.plans = store, processing, plans
        self.extraction_preview, self.extraction_detail = extraction_preview, extraction_detail
        self.deliver, self.authorize, self.require_ready = deliver, authorize, require_ready
        self.asset_lookup = asset_lookup or (lambda asset_id: readonly_asset(processing.assets, asset_id))
        self.frozen_asset_lookup = asset_lookup or (lambda asset_id: readonly_asset(processing.assets, asset_id, allow_archived=True))
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self._authorizations = {}  # Runtime only; never part of SQL or returned state.
        self._lease_owner = uuid4().hex
        with store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS production_automations (
                    automation_id TEXT PRIMARY KEY, submission_key TEXT UNIQUE NOT NULL,
                    request_fingerprint TEXT NOT NULL, revision INTEGER NOT NULL,
                    state_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS production_automation_lease (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS production_automation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, automation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, detail_json TEXT NOT NULL);
            """)

    def _ready(self, target):
        if self.require_ready:
            self.require_ready(target)

    def _principal(self, authorization, role="admin"):
        principal = self.authorize(authorization, role)
        if not isinstance(principal, dict) or not isinstance(principal.get("operator_ref"), str) or not principal["operator_ref"].strip():
            raise ValueError("操作员身份无效")
        return principal

    def _report_request(self, request, item, ir_run_id=None):
        payload = request.extraction.model_dump(mode="json")
        payload["input"] = {"kind": "document_ir", "ir_run_id": ir_run_id} if ir_run_id else {"kind": "pdf", "asset_id": item["asset_id"]}
        return ReportExtractionRequest.model_validate(payload)

    def preview(self, request: AutomationRequest):
        self._ready(request.target)
        items = []
        for position, selected in enumerate(request.items, 1):
            row = {**selected.model_dump(), "position": position, "status": "ready", "blockers": [],
                   "stages": [STAGE_LABELS[s] for s in ("processing", "extraction", "delivery")[:1 if request.target == "processing" else 2 if request.target == "processing_extraction" else 3]]}
            try:
                asset = self.asset_lookup(selected.asset_id)
                if asset["sha256"] != selected.source_sha256:
                    raise ValueError("PDF 内容身份已变化")
                row["file_name"] = asset["file_name"]
                if selected.report_id:
                    report = self.processing.report(selected.report_id)
                    if report["revision"] != selected.report_revision:
                        raise ValueError("报告信息版本已变化，请刷新后预览")
                    if not any(v["asset_id"] == selected.asset_id and v["source_sha256"] == selected.source_sha256 for v in report["asset_versions"]):
                        raise ValueError("报告身份与所选 PDF 不匹配")
                    if request.target == "processing_extraction_delivery":
                        missing = [k for k in ("title", "publisher_entity_ref", "period_start", "period_end") if not report["metadata"].get(k)]
                        if missing:
                            raise ValueError("候选交付缺少已确认报告身份：" + "、".join(missing))
                    row["report_title"] = report["metadata"].get("title")
                    row["report_code"] = report.get("report_code")
                if request.extraction:
                    preview = self.extraction_preview(self._report_request(request, row))
                    row["extraction_preview"] = preview
                    row["selected_outputs"] = preview.get("selected_outputs", [])
                if request.processing.mode == "rebuild_ir":
                    candidate = {"source_sha256": selected.source_sha256, "engine_versions": self.processing.engine_versions,
                        "request": {"config": request.processing.config.model_dump(mode="json")}, "reuse_diagnostics": []}
                    reusable = self.processing._find_reusable(candidate, "ocr")
                    if not reusable:
                        raise ValueError("仅重建结构需要已有配置及版本兼容的识别成果")
                    row["processing_reuse"] = {"ocr_run_id": reusable["run_id"], "compatible": True}
                row["engine_versions"] = deepcopy(self.processing.engine_versions)
            except Exception as exc:
                row.update(status="blocked", blockers=[{"code": "batch_input_blocked", "message": str(getattr(exc, "detail", exc))}])
            items.append(row)
        snapshot = {"request": request.model_dump(mode="json"), "items": items, "engine_versions": self.processing.engine_versions}
        return {"schema_version": SCHEMA, "preview_id": "automation-preview-" + digest(snapshot),
                "can_submit": all(i["status"] == "ready" for i in items), "items": items,
                "calls_model": False, "queue_mutations": 0, "publication_approval": "manual", "snapshot": snapshot}

    def create(self, request: AutomationCreateRequest, authorization):
        principal = self._principal(authorization)
        fingerprint = digest(request.request.model_dump(mode="json"))
        with self._lock:
            with self.store._connect() as db:
                old = db.execute("SELECT state_json,request_fingerprint FROM production_automations WHERE submission_key=?", (request.submission_key,)).fetchone()
            if old:
                if old[1] != fingerprint:
                    raise ValueError("相同提交键不能用于不同批量计划")
                state = json.loads(old[0])
                self._authorizations[state["automation_id"]] = held_authorization(authorization)
                return self.detail(state)
            preview = self.preview(request.request)
            if not preview["can_submit"]:
                raise ValueError("整批预览仍有阻塞，未创建任何自动化或队列任务")
            if preview["preview_id"] != request.preview_id:
                raise ValueError("预览后输入、配置、标准或构建版本发生变化，请重新预览")
            now, automation_id = utc_now(), "automation-" + uuid4().hex
            state = {"schema_version": SCHEMA, "automation_id": automation_id, "name": request.request.name,
                "request": request.request.model_dump(mode="json"), "snapshot": preview["snapshot"],
                "preview_id": preview["preview_id"], "status": "active" if request.start else "planned", "revision": 1,
                "archived_at": None, "lifecycle": {"state": "active"},
                "created_at": now, "updated_at": now, "created_by": principal["operator_ref"],
                "items": [{**{k: v for k, v in i.items() if k != "extraction_preview"}, "item_id": f"item-{i['position']}",
                    "status": "pending", "stage": "pending", "message": "等待顺序推进", "processing_id": None,
                    "extraction_plan_id": None, "delivery": None} for i in preview["items"]]}
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                # Another caller may have committed while this preview was reading.
                old = db.execute("SELECT state_json,request_fingerprint FROM production_automations WHERE submission_key=?", (request.submission_key,)).fetchone()
                if old:
                    if old[1] != fingerprint:
                        raise ValueError("相同提交键不能用于不同批量计划")
                    state = json.loads(old[0])
                else:
                    db.execute("INSERT INTO production_automations VALUES (?,?,?,?,?)", (automation_id, request.submission_key, fingerprint, 1, json.dumps(state, ensure_ascii=False)))
                    self._event(db, state, "submitted", principal["operator_ref"], "批量输入和执行顺序已冻结")
            self._authorizations[state["automation_id"]] = held_authorization(authorization)
            return self.detail(state)

    def get(self, automation_id):
        with self.store._connect() as db:
            row = db.execute("SELECT state_json FROM production_automations WHERE automation_id=?", (automation_id,)).fetchone()
        if not row:
            raise KeyError(automation_id)
        return json.loads(row[0])

    def list(self, include_archived=False):
        with self.store._connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT state_json FROM production_automations WHERE json_extract(state_json,'$.archived_at') IS NULL OR ? ORDER BY rowid DESC LIMIT 500", (int(include_archived),))]

    def _runnable_states(self):
        # UI recency limit must never starve an older queued batch.
        with self.store._connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT state_json FROM production_automations WHERE json_extract(state_json,'$.status') IN ('active','pausing','cancelling') ORDER BY rowid ASC")]

    def events(self, automation_id, after=0):
        self.get(automation_id)
        with self.store._connect() as db:
            return [{"event_id": r["event_id"], "created_at": r["created_at"], **json.loads(r["detail_json"])} for r in db.execute(
                "SELECT * FROM production_automation_events WHERE automation_id=? AND event_id>? ORDER BY event_id LIMIT 500", (automation_id, after))]

    def _event(self, db, state, action, operator_ref=None, reason=None):
        db.execute("INSERT INTO production_automation_events(automation_id,created_at,detail_json) VALUES (?,?,?)",
            (state["automation_id"], utc_now(), json.dumps({"action": action, "revision": state["revision"], "status": state["status"],
                "operator_ref": operator_ref, "reason": reason}, ensure_ascii=False)))

    def _save(self, state, action="progress", operator_ref=None, reason=None):
        old = self.get(state["automation_id"])
        if {k: v for k, v in old.items() if k not in {"revision", "updated_at"}} == {k: v for k, v in state.items() if k not in {"revision", "updated_at"}}:
            return
        expected = state["revision"]
        state.update(revision=expected + 1, updated_at=utc_now())
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE production_automations SET revision=?,state_json=? WHERE automation_id=? AND revision=?",
                (state["revision"], json.dumps(state, ensure_ascii=False), state["automation_id"], expected))
            if not cursor.rowcount:
                raise ValueError("批量计划状态已变化，请刷新后重试")
            self._event(db, state, action, operator_ref, reason)

    def _plan(self, plan_id):
        return next((p for p in self.plans.list() if p["plan_id"] == plan_id), None)

    def _create_extraction(self, state, item, request, processed):
        # Stable key is durable correlation, never an operator-facing task name.
        key = state["automation_id"] + ":" + item["item_id"]
        with self.plans._lock:
            matches = [p for p in self.plans.list() if _automation_plan_matches(p, key)]
            if len(matches) > 1:
                raise ValueError("自动化提取子计划存在重复身份，需人工检查")
            if matches:
                return matches[0]
            extracted = self._report_request(request, item, processed["ir_run_id"])
            frozen = next(i["extraction_preview"] for i in state["snapshot"]["items"] if i["asset_id"] == item["asset_id"])
            # Validate current packages before any child task or queue creation.
            fresh = self.extraction_preview(extracted)
            for field in ("standards", "runtime_standard_snapshots", "selection_snapshot", "selected_outputs", "internal_dependencies"):
                if fresh.get(field) != frozen.get(field):
                    raise ValueError("冻结的标准条款或主要范围已变化；禁止自动换版")
            title = item.get("report_title") or item.get("file_name") or item["asset_id"]
            extracted = extracted.model_copy(update={"name": f"{request.name} · {title}"[:120], "frozen_runtime_standard_snapshots": frozen.get("runtime_standard_snapshots", {})})
            # The plan must carry the preparation actually used for this item, not
            # the request default: a foundation16 item may have recognised only
            # its candidate pages, and provenance that claims "all" would make
            # the enrollment reject the very artifact it just built.
            preparation = item.get("processing_config") or request.processing.config.model_dump(mode="json")
            return self.plans.create_report(extracted, {"asset_id": item["asset_id"], "file_name": item["file_name"],
                "source_sha256": item["source_sha256"], "ir_run_id": processed["ir_run_id"], "ocr_run_id": processed.get("ocr_run_id"),
                "automation_key": key, "processing_config": preparation, "processing_engine_versions": self.processing.engine_versions})

    def _fail(self, state, item, code, message):
        item.update(status="failed", message=message, blockers=[{"code": code, "message": message}])
        if state["request"]["failure_policy"] == "pause":
            state.update(status="paused", pause_reason="单项失败，按策略暂停后续报告", retry_item_id=item["item_id"])

    def _advance(self, state, item):
        request = AutomationRequest.model_validate(state["request"])
        self._ready(request.target)
        if self.processing.engine_versions != state["snapshot"]["engine_versions"]:
            raise ValueError("报告处理构建版本已变化，请以新预览创建下一批计划")
        asset = self.frozen_asset_lookup(item["asset_id"])
        if asset["sha256"] != item["source_sha256"]:
            raise ValueError("冻结 PDF 内容身份已变化，停止自动推进")
        if item.get("report_id"):
            report = self.processing.report(item["report_id"])
            # The frozen binding is the PDF, not the metadata revision: the
            # delivery step re-reads and re-validates the identity anyway, so an
            # operator correcting a title, entity ref or period must not make a
            # finished report impossible to deliver.
            if not any(v["asset_id"] == item["asset_id"] and v["source_sha256"] == item["source_sha256"]
                       for v in report["asset_versions"]):
                raise ValueError("报告身份与所选 PDF 的绑定已变化，请重新规划")
            item["report_revision"] = report["revision"]
            item["report_code"] = report.get("report_code")
        if not item.get("processing_id"):
            if "processing_config" not in item:
                route = getattr(request.extraction, "extraction_route", "standard") if request.extraction else "standard"
                config = request.processing.config
                if route == "foundation16" and request.processing.mode == "auto":
                    config = foundation_processing_config(self.processing, item["source_sha256"], config)
                # Pinned before submission so a resumed item resubmits the identical request.
                item["processing_config"] = config.model_dump(mode="json")
            processed = self.processing.create(ProcessingRequest(asset_id=item["asset_id"], source_sha256=item["source_sha256"],
                report_id=item.get("report_id"), mode=request.processing.mode,
                config=ProcessingConfig.model_validate(item["processing_config"]),
                submission_key=state["automation_id"] + ":" + item["item_id"] + ":processing"))
            item.update(processing_id=processed["processing_id"], stage="processing", status="processing")
        processed = self.processing.get(item["processing_id"])
        item["progress"] = {"stages": self.processing.detail(processed).get("stages", []), "evidence_status": processed.get("evidence_status")}
        item["message"] = next((s.get("message") or s.get("label") for s in reversed(item["progress"]["stages"]) if s.get("status") not in {"pending", "blocked"}), "报告处理排队中")
        if processed["status"] in {"failed", "interrupted", "cancelled", "needs_attention", "discarded", "purged"}:
            self._fail(state, item, "processing_failed", processed.get("error") or "报告处理未满足证据条件")
            return
        if processed["status"] not in {"ready", "ready_with_limits"}:
            return
        if not self.processing.current_evidence(processed).get("ready"):
            self._fail(state, item, "evidence_blocked", "固定结构证据未通过使用条件检查")
            return
        if request.target == "processing":
            item.update(status="completed", stage="completed", message="文档处理完成，结构证据可用")
            return
        if not item.get("extraction_plan_id"):
            plan = self._create_extraction(state, item, request, processed)
            item.update(extraction_plan_id=plan["plan_id"], stage="extraction", status="extracting")
        plan = self._plan(item["extraction_plan_id"])
        if not plan:
            raise ValueError("提取子计划记录缺失，禁止自动重复创建")
        detail = self.extraction_detail(plan)
        item["progress"] = {**item["progress"], "extraction_stages": detail.get("stages", []), "execution_status": detail.get("execution_status"),
            "result_status": detail.get("result_status"), "job_ids": detail.get("job_ids", [])}
        item["native_session_id"] = plan["documents"][0].get("native_session_id")
        item["message"] = "所选条款提取排队或执行中"
        if detail.get("native_unavailable"):
            item.update(message="提取服务暂不可读，已排队记录与成果保留", blockers=[{"code": "extraction_service_unavailable", "message": "等待提取服务恢复后继续读取真实进度"}])
            return
        item["blockers"] = []
        if plan["status"] in {"needs_attention", "interrupted", "cancelled"}:
            self._fail(state, item, "extraction_failed", detail.get("error") or "提取阶段未完成，成果保留待处理")
            return
        if plan["status"] not in {"completed", "partial"}:
            return
        if request.target == "processing_extraction":
            item.update(status=plan["status"], stage="completed", message="所选提取成果已保存" if plan["status"] == "completed" else "部分提取成果已保存，仍有覆盖缺口")
            return
        item.update(stage="delivery", status="delivering", message="正在交付冻结成果至候选审核")
        held = self._authorizations.get(state["automation_id"])
        if not held:
            state.update(status="waiting_credentials", pause_reason="服务重启或操作员授权失效，请重新连接权限并恢复，已完成报告不会重复执行")
            return
        with acting_for(held) as authorization:
            self._principal(authorization, "review")
            result = self.deliver(item["extraction_plan_id"], item["report_id"], item["report_revision"], authorization)
        item["delivery"] = result
        if result.get("status") == "imported":
            item.update(status="partial" if plan["status"] == "partial" else "completed", stage="completed", message="已进入候选审核；正式发布仍由人工审核决定")
        elif result.get("status") in {"partial", "blocked"}:
            self._fail(state, item, "delivery_blocked", "候选交付未完整确认；已保存成果保留，查看交付明细后重试")
        else:
            state.update(status="paused", pause_reason="候选交付确认未知，需恢复后使用同一冻结成果重试", retry_item_id=item["item_id"])

    def _recover_child_refs(self, state):
        """Recover child-create / parent-checkpoint gaps without dispatching work."""
        processing = self.processing.list(True)
        plans = self.plans.list()
        for item in state["items"]:
            if item.get("record_deleted") or item.get("library_deleted"):
                continue
            key = state["automation_id"] + ":" + item["item_id"]
            if not item.get("processing_id"):
                found = [p for p in processing if p.get("request", {}).get("submission_key") == key + ":processing"]
                if len(found) > 1:
                    raise ValueError("报告处理提交身份重复，禁止自动猜测恢复")
                if found:
                    item.update(processing_id=found[0]["processing_id"], stage="processing", status="processing")
            if not item.get("extraction_plan_id"):
                found = [p for p in plans if _automation_plan_matches(p, key)]
                if len(found) > 1:
                    raise ValueError("提取提交身份重复，禁止自动猜测恢复")
                if found:
                    item.update(extraction_plan_id=found[0]["plan_id"], stage="extraction", status="extracting")

    def _lease(self):
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            row = db.execute("SELECT owner,expires_at FROM production_automation_lease WHERE singleton=1").fetchone()
            if row and row[0] != self._lease_owner and row[1] > now:
                return False
            db.execute("INSERT INTO production_automation_lease VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at", (self._lease_owner, now + 120))
            return True

    def _release_lease(self):
        with self.store._connect() as db:
            db.execute("DELETE FROM production_automation_lease WHERE singleton=1 AND owner=?", (self._lease_owner,))

    def _children_idle(self, item):
        if item.get("processing_id"):
            processed = self.processing.get(item["processing_id"])
            if processed["status"] in {"queued", "running", "cancelling"}:
                return False
            for step in processed.get("steps", {}).values():
                if step.get("task_id"):
                    task = self.store.get(step["task_id"])
                    if task.status.value in {"queued", "running"} or self.store.worker_alive(task.task_id):
                        return False
        if item.get("extraction_plan_id"):
            plan = self._plan(item["extraction_plan_id"])
            if plan:
                for document in plan["documents"]:
                    for step in document["steps"].values():
                        if step.get("task_id"):
                            task = self.store.get(step["task_id"])
                            if task.status.value in {"queued", "running"} or self.store.worker_alive(task.task_id):
                                return False
        return True

    def tick(self):
        with self._lock:
            if not self._lease():
                return
            try:
                self._tick_locked()
            finally:
                self._release_lease()

    def _tick_locked(self):
        for state in self._runnable_states():  # Plans are advanced in submission order.
            if state["status"] not in {"active", "pausing", "cancelling"}:
                continue
            current = next((i for i in state["items"] if i["status"] not in ITEM_TERMINAL), None)
            try:
                self._recover_child_refs(state)
                current = next((i for i in state["items"] if i["status"] not in ITEM_TERMINAL), None)
                if state["status"] in {"pausing", "cancelling"}:
                    if not current or self._children_idle(current):
                        target = "paused" if state["status"] == "pausing" else "cancelled"
                        state["status"] = target
                        if target == "cancelled":
                            for item in state["items"]:
                                if item["status"] not in ITEM_TERMINAL:
                                    item.update(status="cancelled", message="批量计划取消，已完成成果保留")
                    self._save(state)
                    continue
                if current:
                    self._advance(state, current)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                message = str(getattr(exc, "detail", exc))
                if current and current.get("stage") == "delivery" and status_code in {401, 403}:
                    self._authorizations.pop(state["automation_id"], None)
                    state.update(status="waiting_credentials", pause_reason="操作员授权已失效，请重新连接后恢复")
                elif status_code == 503 or state["status"] in {"pausing", "cancelling"}:
                    if state["status"] not in {"pausing", "cancelling"}:
                        state["status"] = "paused"
                    state["pause_reason"] = "生产依赖暂不可用或停止状态无法确认：" + message
                    if current:
                        current["blockers"] = [{"code": "runtime_dependency_unavailable", "message": message}]
                elif current:
                    self._fail(state, current, "automation_dependency_blocked", message)
                else:
                    state.update(status="paused", pause_reason="生产依赖记录异常：" + message)
            if state["status"] == "active" and all(i["status"] in ITEM_TERMINAL for i in state["items"]):
                state["status"] = "completed" if all(i["status"] == "completed" for i in state["items"]) else "partial"
                self._authorizations.pop(state["automation_id"], None)
            self._save(state)
            break  # Oldest runnable parent owns ordered automatic advancement.

    def action(self, automation_id, action, request: AutomationAction, authorization):
        principal = self._principal(authorization)
        with self._lock:
            state = self.get(automation_id)
            if state["revision"] != request.expected_revision:
                raise ValueError("批量计划状态已变化，请刷新后重试")
            if action in {"archive", "restore"}:
                if state["status"] not in TERMINAL:
                    raise ValueError("仅已结束的批量计划可整理；待部署或执行中的计划请先取消")
                archived = bool(state.get("archived_at"))
                if action == "archive" and archived or action == "restore" and not archived:
                    raise ValueError("计划归档状态已变化，请刷新后重试")
                self._recover_child_refs(state)
                if not all(self._children_idle(item) for item in state["items"]):
                    raise ValueError("子任务或工作进程尚未停止，不能整理父计划")
                at = utc_now() if action == "archive" else None
                state.update(archived_at=at, lifecycle={"state": "archived" if at else "active",
                    "archived_at": at, "reason": request.reason, "operator_ref": principal["operator_ref"]})
                self._authorizations.pop(automation_id, None)
                self._save(state, action, principal["operator_ref"], request.reason)
                return self.detail(state)
            if state.get("archived_at"):
                raise ValueError("已归档计划只能恢复记录，不能执行运行操作")
            self._recover_child_refs(state)
            if action in {"start", "resume", "retry_failed"}:
                self._ready(state["request"]["target"])
                if state["status"] in {"cancelling", "cancelled", "completed"}:
                    raise ValueError("当前计划不能恢复，请创建新批次")
                if action == "start" and state["status"] != "planned":
                    raise ValueError("计划已经部署，不能重复启动")
                if action == "retry_failed" and state["status"] not in {"partial", "paused"}:
                    raise ValueError("请先结束当前推进或暂停计划，再重试失败报告")
                if action == "resume" and state["status"] not in {"paused", "waiting_credentials", "active"}:
                    raise ValueError("当前计划不能恢复，请使用相应的部署或重试操作")
                if action == "retry_failed":
                    retry = [i for i in state["items"] if i["status"] == "failed"]
                else:
                    retry = [i for i in state["items"] if i["item_id"] == state.get("retry_item_id")]
                for item in retry:
                    if not self._children_idle(item):
                        raise ValueError("子任务尚未停止，请等待执行槽释放")
                    if item.get("processing_id") and self.processing.get(item["processing_id"])["status"] in {"failed", "interrupted", "cancelled"}:
                        self.processing.action(item["processing_id"], "resume", ProcessingActionRequest())
                    if item.get("extraction_plan_id"):
                        plan = self._plan(item["extraction_plan_id"])
                        if plan and plan["status"] in {"paused", "needs_attention", "interrupted", "partial", "failed"}:
                            # A plan whose native session already finished adopts
                            # its results instead of recognising the pages again.
                            self.plans.action(plan["plan_id"], "resume")
                    item.update(status="processing" if not item.get("extraction_plan_id") else "extracting", blockers=[])
                for item in state["items"]:
                    if item["status"] not in ITEM_TERMINAL and item.get("extraction_plan_id"):
                        plan = self._plan(item["extraction_plan_id"])
                        if plan and plan["status"] == "paused":
                            self.plans.action(plan["plan_id"], "resume")
                state.update(status="active")
                state.pop("retry_item_id", None)
                state.pop("pause_reason", None)
                self._authorizations[automation_id] = held_authorization(authorization)
            elif action in {"pause", "cancel"}:
                if state["status"] in TERMINAL:
                    raise ValueError("该计划已经结束")
                for item in state["items"]:
                    if item["status"] in ITEM_TERMINAL:
                        continue
                    if item.get("extraction_plan_id"):
                        plan = self._plan(item["extraction_plan_id"])
                        if plan and plan["status"] not in {"completed", "partial", "cancelled"}:
                            self.plans.action(plan["plan_id"], action)
                    if action == "cancel" and item.get("processing_id"):
                        processed = self.processing.get(item["processing_id"])
                        if processed["status"] in {"queued", "running", "cancelling"}:
                            self.processing.action(processed["processing_id"], "cancel")
                state["status"] = "pausing" if action == "pause" else "cancelling"
                if action == "cancel":
                    self._authorizations.pop(automation_id, None)
            else:
                raise ValueError("不支持的自动化操作")
            self._save(state, action, principal["operator_ref"], request.reason)
            return self.detail(state)

    def reorder(self, automation_id, request: AutomationOrder, authorization):
        principal = self._principal(authorization)
        with self._lock:
            state = self.get(automation_id)
            if state["revision"] != request.expected_revision:
                raise ValueError("批量计划状态已变化，请刷新后重试")
            if state.get("archived_at") or state["status"] in TERMINAL | {"cancelling"}:
                raise ValueError("当前计划不能调整执行顺序")
            self._recover_child_refs(state)
            pending = [i for i in state["items"] if i["status"] == "pending"]
            if len(request.item_ids) != len(set(request.item_ids)) or set(request.item_ids) != {i["item_id"] for i in pending}:
                raise ValueError("只能完整重排尚未开始的报告，已启动项不可换位")
            lookup = {i["item_id"]: i for i in pending}
            ids = iter(request.item_ids)
            state["items"] = [lookup[next(ids)] if i["status"] == "pending" else i for i in state["items"]]
            for position, item in enumerate(state["items"], 1):
                item["position"] = position
            self._save(state, "reorder", principal["operator_ref"], request.reason)
            return self.detail(state)

    def detail(self, state):
        result = deepcopy(state)
        result["status_label"] = STATUS_LABELS[state["status"]]
        result["archived_at"] = state.get("archived_at")
        result["lifecycle"] = {**state.get("lifecycle", {"state": "active"}), "revision": state["revision"]}
        result["archive_scope"] = {"parent_record_only": True, "child_records_retained": True,
            "source_artifacts_retained": True, "permanent_deletion": False}
        result["counts"] = {status: sum(i["status"] == status for i in state["items"]) for status in {i["status"] for i in state["items"]}}
        result["progress"] = {"completed_reports": sum(i["status"] in {"completed", "partial"} for i in state["items"]), "terminal_reports": sum(i["status"] in ITEM_TERMINAL for i in state["items"]), "total_reports": len(state["items"])}
        actions = []
        if state["status"] == "planned": actions.append(("start", "部署并开始自动生产"))
        if state["status"] == "active": actions.append(("pause", "当前阶段收尾后暂停"))
        if state["status"] in {"paused", "waiting_credentials"}: actions.append(("resume", "恢复自动推进"))
        if state["status"] == "partial" and any(i["status"] == "failed" for i in state["items"]): actions.append(("retry_failed", "重试失败报告，复用已完成阶段"))
        if state["status"] not in TERMINAL | {"cancelling"}: actions.append(("cancel", "取消未完成生产"))
        if any(i["status"] == "pending" for i in state["items"]) and state["status"] not in TERMINAL | {"cancelling"}:
            actions.append(("order", "调整尚未开始报告顺序"))
        if state["status"] in TERMINAL:
            actions.append(("archive", "删除计划记录（可恢复）"))
        if state.get("archived_at"):
            actions = [("restore", "恢复计划记录")]
        result["available_actions"] = [{"action": action, "label": label, "endpoint": f"/api/production-automations/{state['automation_id']}/{action}", "method": "POST", "payload": {"expected_revision": state["revision"]}} for action, label in actions]
        result["reorderable_item_ids"] = [i["item_id"] for i in state["items"] if i["status"] == "pending"]
        result["publication_approval"] = "manual"
        return result

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="production-automation-coordinator", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # A concurrent state conflict is observed in the next durable tick;
                # it must not kill all unrelated future plans.
                pass
            self._stop.wait(2)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._authorizations.clear()
