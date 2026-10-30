"""Durable report identities and OCR → IR preparation above the single queue.

The coordinator only schedules children. It never owns a worker slot, rewrites
historical artifacts, guesses a measurement entity, or reuses an unversioned
artifact because its directory happens to sort last.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import threading
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from esg_contracts.search import PageSearchIntent

from esg_pipeline.contracts import utc_now
from esg_pipeline.context import dispatch_key


PROCESSING_CONTRACT = "report-processing-v1"
TERMINAL = {
    "ready",
    "ready_with_limits",
    "needs_attention",
    "failed",
    "interrupted",
    "cancelled",
}
STAGE_LABELS = {"ocr": "文档识别", "ir": "结构与证据构建", "quality": "证据质量检查"}


def digest(value) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class ReportMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=240)
    publisher_name: str | None = Field(default=None, max_length=240)
    publisher_entity_ref: str | None = Field(default=None, max_length=160)
    reporting_year: int | None = Field(default=None, ge=1900, le=2200)
    period_start: date | None = None
    period_end: date | None = None
    language: str | None = Field(default=None, max_length=40)
    report_kind: str | None = Field(default=None, max_length=80)
    # Optional links to an external report catalogue. They never replace the
    # local report_id, PDF asset_id or the registered measurement entity.
    measurement_entity_ref: str | None = Field(default=None, max_length=160)
    source_system: str | None = Field(default=None, max_length=80)
    source_report_id: str | None = Field(default=None, max_length=200)
    lei: str | None = Field(default=None, max_length=32)
    ticker: str | None = Field(default=None, max_length=80)
    isin: str | None = Field(default=None, max_length=32)
    source_package_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def valid_period(self):
        if (
            self.period_start
            and self.period_end
            and self.period_start > self.period_end
        ):
            raise ValueError("报告期间起始日期不能晚于结束日期")
        return self


class ProcessingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ocr_provider: Literal["local_first", "local_paddleocr", "paddle_api"] = (
        "local_paddleocr"
    )
    ocr_model: str = Field(default="PaddleOCR-VL-1.6", min_length=1, max_length=160)
    allow_cloud_ocr: bool = False
    # "clauses" OCRs only the pages the selected clause retrieval terms pick;
    page_selection: Literal["all", "clauses"] = "all"
    page_search_targets: list[PageSearchIntent] = Field(default_factory=list)
    execute_optional_reviews: bool = False
    execute_required_reviews: bool = True
    review_provider: Literal["local_qwen", "local_nuextract", "qiniu"] = "local_qwen"
    render_dpi: int = Field(default=144, ge=72, le=300)
    max_auto_review_rounds: int = Field(default=3, ge=1, le=3)
    allow_limited_evidence: bool = True

    @model_validator(mode="after")
    def explicit_cloud(self):
        if self.ocr_provider == "paddle_api" and not self.allow_cloud_ocr:
            raise ValueError("云 OCR 必须明确授权")
        return self


class ProcessingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=160)
    report_id: str | None = Field(default=None, max_length=160)
    mode: Literal["auto", "rebuild_ir", "reprocess"] = "auto"
    config: ProcessingConfig = Field(default_factory=ProcessingConfig)
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    submission_key: str | None = Field(default=None, min_length=1, max_length=160)
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)


class ProcessingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int | None = Field(default=None, ge=1)
    qiniu_api_key: str | None = Field(default=None, exclude=True)
    ocr_token: str | None = Field(default=None, exclude=True)


class EvidenceTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ir_run_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)


class ReportMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: ReportMetadata
    expected_revision: int = Field(ge=1)


class ReportVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)


def report_identity_code(metadata: dict) -> str | None:
    """Readable business alias, never the durable identity used by references."""
    entity = metadata.get("publisher_entity_ref")
    year = metadata.get("reporting_year")
    if not entity or not year:
        return None
    kind = metadata.get("report_kind")
    return f"{entity}/{year}" + (f"/{kind}" if kind else "")


class ReportProcessingCoordinator:
    def __init__(
        self,
        store,
        assets,
        dispatch,
        cancel,
        ocr_root: Path,
        ir_root: Path,
        *,
        engine_versions=None,
        integrity_check=None,
        runtime_config=None,
    ):
        self.store, self.assets, self.dispatch, self.cancel = (
            store,
            assets,
            dispatch,
            cancel,
        )
        self.roots = {"ocr": Path(ocr_root).resolve(), "ir": Path(ir_root).resolve()}
        self.engine_versions = engine_versions or {
            stage: digest(
                {"source": version, "runtime": (runtime_config or {}).get(stage, {})}
            )
            for stage, version in self._engine_versions().items()
        }
        self.integrity_check = integrity_check or self._check_integrity
        self._integrity_cache = {}
        self._lock, self._stop = threading.RLock(), threading.Event()
        self._thread = None
        self._secrets = {}
        with store._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS report_asset_versions (
                    report_id TEXT NOT NULL REFERENCES reports(report_id), asset_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL UNIQUE, version INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(report_id,version));
                CREATE TABLE IF NOT EXISTS report_metadata_history (
                    report_id TEXT NOT NULL REFERENCES reports(report_id), revision INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(report_id,revision));
                CREATE TABLE IF NOT EXISTS report_processing_runs (
                    processing_id TEXT PRIMARY KEY, submission_key TEXT UNIQUE, request_fingerprint TEXT NOT NULL,
                    asset_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, status TEXT NOT NULL, state_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_processing_source ON report_processing_runs(source_sha256,status);
                CREATE TABLE IF NOT EXISTS report_processing_artifacts (
                    processing_id TEXT NOT NULL REFERENCES report_processing_runs(processing_id), stage TEXT NOT NULL,
                    run_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, config_digest TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(processing_id,stage));
                CREATE TABLE IF NOT EXISTS report_processing_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    processing_id TEXT NOT NULL REFERENCES report_processing_runs(processing_id),
                    created_at TEXT NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL);
            """)

    @staticmethod
    def _engine_versions():
        from esg_runtime.layout import DOCUMENT_API_SOURCE, EXTRACT_ROOT

        base = DOCUMENT_API_SOURCE

        def fingerprint(paths):
            return digest(
                {
                    str(path.relative_to(EXTRACT_ROOT)): sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in sorted(paths)
                }
            )

        return {
            "ocr": fingerprint(
                [
                    *base.joinpath("ocr").glob("*.py"),
                    base / "workflows/ocr_workflow.py",
                    EXTRACT_ROOT / "contracts/src/esg_contracts/document.py",
                    base / "config.py",
                ]
            ),
            "ir": fingerprint(
                [
                    *base.joinpath("document").glob("*.py"),
                    *base.joinpath("models").glob("*.py"),
                    base / "workflows/ir_workflow.py",
                    base / "config.py",
                ]
            ),
        }

    def report(self, report_id):
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if row is None:
                raise KeyError(report_id)
            versions = [
                dict(v)
                for v in db.execute(
                    "SELECT * FROM report_asset_versions WHERE report_id=? ORDER BY version",
                    (report_id,),
                )
            ]
        metadata = json.loads(row["metadata_json"])
        return {
            "report_id": report_id,
            "report_code": report_identity_code(metadata),
            "revision": row["revision"],
            "metadata": metadata,
            "asset_versions": versions,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reports(self):
        with self.store._connect() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT report_id FROM reports ORDER BY updated_at DESC"
                )
            ]
        return [self.report(r) for r in ids]

    def current_evidence(self, state):
        """Validate a pinned result without rewriting its historical execution state."""
        readiness = {"status": "not_ready", "ready": False, "blockers": []}
        if not state.get("ir_run_id"):
            return readiness
        try:
            if state["status"] in {"discarded", "purged"}:
                raise ValueError("已丢弃记录不再作为可选提取来源")
            if state["engine_versions"] != self.engine_versions:
                raise ValueError(
                    "历史成果的构建版本与当前不同，需选择兼容处理或显式核对版本"
                )
            manifest, checksum = self._manifest("ir", state["ir_run_id"])
            self._verify_manifest(state, "ir", manifest)
            self.integrity_check("ir", state["ir_run_id"])
            self._verify_ir_ocr(state, manifest)
            with self.store._connect() as db:
                recorded = db.execute(
                    "SELECT manifest_sha256 FROM report_processing_artifacts WHERE processing_id=? AND stage='ir' AND run_id=?",
                    (state["processing_id"], state["ir_run_id"]),
                ).fetchone()
            if not recorded or recorded[0] != checksum:
                raise ValueError("证据清单与固定版本不一致")
            full = bool(manifest.get("can_build_evidence"))
            limited = (
                bool(manifest.get("can_build_limited_evidence"))
                and state["request"]["config"]["allow_limited_evidence"]
            )
            if not full and not limited:
                raise ValueError("固定的结构成果不满足本次证据使用条件")
            readiness.update(status="full" if full else "limited", ready=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            readiness.update(
                status="blocked",
                blockers=[{"code": "evidence_readiness_blocked", "message": str(exc)}],
            )
        return readiness

    def asset_summary(self, asset):
        with self.store._connect() as db:
            report = db.execute(
                "SELECT report_id FROM report_asset_versions WHERE source_sha256=?",
                (asset["sha256"],),
            ).fetchone()
            rows = db.execute(
                "SELECT state_json FROM report_processing_runs WHERE source_sha256=? ORDER BY rowid DESC",
                (asset["sha256"],),
            ).fetchall()
        states = [json.loads(row[0]) for row in rows]
        states = [
            s
            for s in states
            if not s.get("archived_at") and s["status"] not in {"discarded", "purged"}
        ]
        summary = {
            "report_id": report[0] if report else None,
            "report_metadata": self.report(report[0])["metadata"] if report else {},
            "processing_status": "ocr_only" if asset.get("ocr_runs") else "unprocessed",
            "evidence_status": "not_ready",
            "processing_runs": [
                {
                    "processing_id": s["processing_id"],
                    "status": s["status"],
                    "ir_run_id": s.get("ir_run_id"),
                }
                for s in states
            ],
        }
        if not states:
            return summary
        state = states[0]
        summary.update(
            processing_id=state["processing_id"],
            processing_status=state["status"],
            recorded_processing_status=state["status"],
            evidence_status=state["evidence_status"],
            ir_run_id=state.get("ir_run_id"),
            processing_error=state.get("error"),
        )
        summary["current_evidence"] = self.current_evidence(state)
        if (
            state["status"] in {"ready", "ready_with_limits"}
            and not summary["current_evidence"]["ready"]
        ):
            summary.update(
                processing_status="needs_attention",
                evidence_status="blocked",
                processing_error="；".join(
                    item["message"] for item in summary["current_evidence"]["blockers"]
                ),
            )
        return summary

    def register_asset(self, asset, report_id=None, expected_revision=None):
        """Only explicit attachment identifies two different PDFs as report revisions."""
        with self._lock, self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT report_id FROM report_asset_versions WHERE source_sha256=?",
                (asset["sha256"],),
            ).fetchone()
            if existing:
                if report_id and existing[0] != report_id:
                    raise ValueError("该 PDF 已属于另一个报告；不能静默改写报告身份")
                return self.report(existing[0])
            now = utc_now()
            if report_id:
                row = db.execute(
                    "SELECT revision FROM reports WHERE report_id=?", (report_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(report_id)
                if row[0] != expected_revision:
                    raise ValueError("报告版本已变化，请刷新后再关联文件")
                version = db.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM report_asset_versions WHERE report_id=?",
                    (report_id,),
                ).fetchone()[0]
                db.execute(
                    "UPDATE reports SET revision=revision+1,updated_at=? WHERE report_id=?",
                    (now, report_id),
                )
            else:
                report_id, version = f"report-{uuid4().hex}", 1
                metadata = json.dumps(
                    ReportMetadata().model_dump(mode="json"), ensure_ascii=False
                )
                db.execute(
                    "INSERT INTO reports VALUES (?,?,?,?,?)",
                    (report_id, 1, metadata, now, now),
                )
                db.execute(
                    "INSERT INTO report_metadata_history VALUES (?,?,?,?)",
                    (report_id, 1, metadata, now),
                )
            db.execute(
                "INSERT INTO report_asset_versions VALUES (?,?,?,?,?)",
                (report_id, asset["asset_id"], asset["sha256"], version, now),
            )
        return self.report(report_id)

    def update_metadata(self, report_id, request: ReportMetadataPatch):
        metadata = json.dumps(
            request.metadata.model_dump(mode="json"), ensure_ascii=False
        )
        now = utc_now()
        with self._lock, self.store._connect() as db:
            cursor = db.execute(
                "UPDATE reports SET revision=revision+1,metadata_json=?,updated_at=? WHERE report_id=? AND revision=?",
                (metadata, now, report_id, request.expected_revision),
            )
            if not cursor.rowcount:
                self.report(report_id)
                raise ValueError("报告信息已变化，请刷新后重试")
            db.execute(
                "INSERT INTO report_metadata_history VALUES (?,?,?,?)",
                (report_id, request.expected_revision + 1, metadata, now),
            )
        return self.report(report_id)

    def get(self, processing_id):
        with self.store._connect() as db:
            row = db.execute(
                "SELECT state_json FROM report_processing_runs WHERE processing_id=?",
                (processing_id,),
            ).fetchone()
        if not row:
            raise KeyError(processing_id)
        return json.loads(row[0])

    def list(self, include_archived=False):
        with self.store._connect() as db:
            states = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT state_json FROM report_processing_runs ORDER BY rowid DESC"
                )
            ]
        return [s for s in states if include_archived or not s.get("archived_at")]

    def events(self, processing_id, after=0):
        self.get(processing_id)
        with self.store._connect() as db:
            return [
                {**dict(r), "detail": json.loads(r["state_json"])}
                for r in db.execute(
                    "SELECT event_id,created_at,stage,state_json FROM report_processing_events WHERE processing_id=? AND event_id>? ORDER BY event_id LIMIT 500",
                    (processing_id, after),
                )
            ]

    def _save(self, state, *, event_stage="state", force=False):
        old = None
        try:
            old = self.get(state["processing_id"])
        except KeyError:
            pass
        unchanged = old and {
            k: v for k, v in old.items() if k not in {"revision", "updated_at"}
        } == {k: v for k, v in state.items() if k not in {"revision", "updated_at"}}
        if unchanged and not force:
            return
        state["revision"] = (old or {}).get("revision", 0) + 1
        state["updated_at"] = utc_now()
        text = json.dumps(state, ensure_ascii=False)
        with self.store._connect() as db:
            db.execute(
                "UPDATE report_processing_runs SET status=?,state_json=? WHERE processing_id=?",
                (state["status"], text, state["processing_id"]),
            )
            db.execute(
                "INSERT INTO report_processing_events(processing_id,created_at,stage,state_json) VALUES (?,?,?,?)",
                (state["processing_id"], state["updated_at"], event_stage, text),
            )

    def _config_digest(self, state, stage):
        config = state["request"]["config"]
        keys = (
            ("ocr_provider", "ocr_model", "allow_cloud_ocr")
            if stage == "ocr"
            else (
                "execute_required_reviews",
                "execute_optional_reviews",
                "review_provider",
                "render_dpi",
                "max_auto_review_rounds",
            )
        )
        values = {k: config.get(k, False) for k in keys}
        # Whole-document digests stay unchanged; only a targeted selection is keyed in.
        if stage == "ocr" and config.get("page_selection", "all") != "all":
            values["page_selection"] = config["page_selection"]
            values["page_search_targets"] = config.get("page_search_targets") or []
        return digest(
            {
                "contract": PROCESSING_CONTRACT,
                "engine": state["engine_versions"][stage],
                "config": values,
                **(
                    {"ocr_config_digest": self._config_digest(state, "ocr")}
                    if stage == "ir"
                    else {}
                ),
            }
        )

    def resolve_document(self, asset_id, config: ProcessingConfig, *, reuse=True):
        """Shared resolver for standalone processing and automatic extraction preparation."""
        asset = self.assets.get(asset_id)
        state = {
            "source_sha256": asset["sha256"],
            "engine_versions": self.engine_versions,
            "request": {"config": config.model_dump(mode="json")},
            "reuse_diagnostics": [],
        }
        document = {
            "asset_id": asset_id,
            "file_name": asset["file_name"],
            "source_sha256": asset["sha256"],
            "processing_config": state["request"]["config"],
            "processing_engine_versions": self.engine_versions,
        }
        if reuse:
            existing_ir = self._find_reusable(state, "ir")
            if existing_ir:
                manifest, _ = self._manifest("ir", existing_ir["run_id"])
                document.update(
                    ir_run_id=existing_ir["run_id"],
                    ir_reused=True,
                    ocr_run_id=manifest.get("ocr_run_id")
                    or (manifest.get("source") or {}).get("ocr_run_id"),
                    ocr_reused=True,
                )
            else:
                existing_ocr = self._find_reusable(state, "ocr")
                if existing_ocr:
                    document.update(ocr_run_id=existing_ocr["run_id"], ocr_reused=True)
        document["reuse_diagnostics"] = state["reuse_diagnostics"]
        return document

    def record_plan_artifacts(self, plan, document):
        """Enroll only new plans carrying exact preparation provenance, after IR completion.

        Historical plans without a producer snapshot stay readable but are not
        automatically promoted to compatible artifacts.
        """
        if (
            not document.get("processing_config")
            or document.get("processing_engine_versions") != self.engine_versions
        ):
            return None
        if (
            not document.get("asset_id")
            or not document.get("ocr_run_id")
            or not document.get("ir_run_id")
        ):
            return None
        with self._lock:
            key = f"plan:{plan['plan_id']}:{document['ir_run_id']}"
            with self.store._connect() as db:
                existing = db.execute(
                    "SELECT state_json FROM report_processing_runs WHERE submission_key=?",
                    (key,),
                ).fetchone()
            if existing:
                return json.loads(existing[0])
            config = ProcessingConfig.model_validate(document["processing_config"])
            # The artifacts describe their own page coverage; enrolling them with
            # a caller-side default would reject the OCR run they were built from.
            ocr_manifest, _ = self._manifest("ocr", document["ocr_run_id"])
            selector = (ocr_manifest.get("page_selection") or {}).get(
                "selector"
            ) or "all"
            if selector != config.page_selection and config.page_selection == "all":
                config = config.model_copy(update={"page_selection": selector})
            asset = self.assets.get(document["asset_id"])
            if asset["sha256"] != document["source_sha256"]:
                raise ValueError("计划的 PDF 已变化，不能登记处理成果")
            report = self.register_asset(asset)
            now = utc_now()
            state = {
                "schema_version": PROCESSING_CONTRACT,
                "processing_id": f"processing-{uuid4().hex}",
                "report_id": report["report_id"],
                "asset_id": asset["asset_id"],
                "source_sha256": asset["sha256"],
                "file_name": asset["file_name"],
                "request": {
                    "asset_id": asset["asset_id"],
                    "mode": "auto",
                    "config": config.model_dump(mode="json"),
                },
                "engine_versions": self.engine_versions,
                "status": "queued",
                "evidence_status": "not_ready",
                "revision": 0,
                "created_at": now,
                "updated_at": now,
                "steps": {},
                "reuse_diagnostics": [],
                "origin_plan_id": plan["plan_id"],
                "ocr_run_id": document["ocr_run_id"],
                "ir_run_id": document["ir_run_id"],
            }
            # Validate before inserting the enrollment journal.
            for stage in ("ocr", "ir"):
                manifest, _ = self._manifest(stage, state[f"{stage}_run_id"])
                self._verify_manifest(state, stage, manifest)
                self.integrity_check(stage, state[f"{stage}_run_id"])
                state["steps"][stage] = {
                    "status": "completed",
                    "run_id": state[f"{stage}_run_id"],
                    "origin_plan_id": plan["plan_id"],
                }
            with self.store._connect() as db:
                db.execute(
                    "INSERT INTO report_processing_runs VALUES (?,?,?,?,?,?,?)",
                    (
                        state["processing_id"],
                        key,
                        digest(state["request"]),
                        asset["asset_id"],
                        asset["sha256"],
                        "queued",
                        json.dumps(state, ensure_ascii=False),
                    ),
                )
            self._record_artifact(state, "ocr")
            self._finish_quality(state)
            self._save(state, event_stage="plan_artifacts_enrolled")
            return state

    def create(self, request: ProcessingRequest):
        asset = self.assets.get(request.asset_id)
        if request.source_sha256 and request.source_sha256 != asset["sha256"]:
            raise ValueError("PDF 内容已变化，请刷新报告后重新提交")
        if request.config.ocr_provider == "paddle_api" and not request.ocr_token:
            raise ValueError("云 OCR 需要本次运行凭据")
        if (
            (
                request.config.execute_required_reviews
                or request.config.execute_optional_reviews
            )
            and request.config.review_provider == "qiniu"
            and not request.qiniu_api_key
        ):
            raise ValueError("云证据复核需要本次运行凭据")
        with self._lock:
            if request.mode == "rebuild_ir":
                candidate = {
                    "source_sha256": asset["sha256"],
                    "engine_versions": self.engine_versions,
                    "request": {"config": request.config.model_dump(mode="json")},
                    "reuse_diagnostics": [],
                }
                if not self._find_reusable(candidate, "ocr"):
                    raise ValueError(
                        "没有配置和构建版本兼容的识别成果，不能只重建结构；请选择自动处理或重新识别并构建"
                    )
            report = self.register_asset(
                asset,
                request.report_id,
                self.report(request.report_id)["revision"]
                if request.report_id
                else None,
            )
            payload = request.model_dump(mode="json")
            fingerprint = digest(
                {
                    "request": {
                        k: v for k, v in payload.items() if k != "submission_key"
                    },
                    "sha256": asset["sha256"],
                    "engines": self.engine_versions,
                }
            )
            with self.store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = (
                    db.execute(
                        "SELECT state_json,request_fingerprint FROM report_processing_runs WHERE submission_key=?",
                        (request.submission_key,),
                    ).fetchone()
                    if request.submission_key
                    else None
                )
                if existing:
                    if existing[1] != fingerprint:
                        raise ValueError("相同提交键不能用于不同的报告处理请求")
                    return json.loads(existing[0])
                active = db.execute(
                    "SELECT state_json FROM report_processing_runs WHERE request_fingerprint=? AND status IN ('queued','running','cancelling')",
                    (fingerprint,),
                ).fetchone()
                if active:
                    return json.loads(active[0])
                processing_id, now = f"processing-{uuid4().hex}", utc_now()
                state = {
                    "schema_version": PROCESSING_CONTRACT,
                    "processing_id": processing_id,
                    "report_id": report["report_id"],
                    "asset_id": asset["asset_id"],
                    "source_sha256": asset["sha256"],
                    "file_name": asset["file_name"],
                    "request": payload,
                    "engine_versions": self.engine_versions,
                    "status": "queued",
                    "evidence_status": "not_ready",
                    "steps": {},
                    "revision": 0,
                    "created_at": now,
                    "updated_at": now,
                    "reuse_diagnostics": [],
                }
                db.execute(
                    "INSERT INTO report_processing_runs VALUES (?,?,?,?,?,?,?)",
                    (
                        processing_id,
                        request.submission_key,
                        fingerprint,
                        asset["asset_id"],
                        asset["sha256"],
                        "queued",
                        json.dumps(state, ensure_ascii=False),
                    ),
                )
            self._secrets[processing_id] = {
                "qiniu_api_key": request.qiniu_api_key,
                "ocr_token": request.ocr_token,
            }
            if request.mode == "auto":
                reused = self._find_reusable(state, "ir")
                if reused:
                    state["ir_run_id"] = reused["run_id"]
                    manifest, _ = self._manifest("ir", reused["run_id"])
                    state["ocr_run_id"] = manifest.get("ocr_run_id") or (
                        manifest.get("source") or {}
                    ).get("ocr_run_id")
                    state["steps"]["ir"] = {
                        "status": "reused",
                        "run_id": reused["run_id"],
                        "reused_from": reused["processing_id"],
                        "config_digest": reused["config_digest"],
                    }
                    state["steps"]["ocr"] = {
                        "status": "reused",
                        "run_id": state["ocr_run_id"],
                        "reused_from": reused["processing_id"],
                    }
            if request.mode != "reprocess" and not state.get("ocr_run_id"):
                reused = self._find_reusable(state, "ocr")
                if reused:
                    state["ocr_run_id"] = reused["run_id"]
                    state["steps"]["ocr"] = {
                        "status": "reused",
                        "run_id": reused["run_id"],
                        "reused_from": reused["processing_id"],
                        "config_digest": reused["config_digest"],
                    }
            self._save(state, event_stage="submitted", force=True)
            if state.get("ir_run_id"):
                self._finish_quality(state)
                self._save(state, event_stage="quality")
            return state

    def _manifest(self, stage, run_id):
        path = (self.roots[stage] / run_id / "manifest.json").resolve()
        if not path.is_relative_to(self.roots[stage]) or not path.is_file():
            raise ValueError(f"{STAGE_LABELS[stage]}成果不存在或路径无效")
        data = path.read_bytes()
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            raise ValueError("成果清单身份与任务不一致")
        return value, sha256(data).hexdigest()

    def _check_integrity(self, stage, run_id):
        from esg_document.document.reader import DocumentIrPackageReader
        from esg_document.storage.package_validator import validate_package

        root = self.roots[stage] / run_id
        signature = tuple(
            (str(path.relative_to(root)), stat.st_size, stat.st_mtime_ns, stat.st_ino)
            for path in sorted(root.rglob("*"))
            if path.is_file()
            for stat in [path.stat()]
        )
        key = (stage, run_id)
        if self._integrity_cache.get(key) == signature:
            return
        if stage == "ir":
            DocumentIrPackageReader(root).validate_integrity().require_valid()
        else:
            validate_package(
                root,
                expected_type="ocr-run",
                expected_schema="ocr-package-v1",
                required_entrypoints={
                    "source_request",
                    "provider_submit_response",
                    "provider_poll_events",
                    "provider_result",
                    "page_index",
                    "artifacts",
                    "integrity",
                },
            ).require_valid()
        if len(self._integrity_cache) >= 256:
            self._integrity_cache.clear()
        self._integrity_cache[key] = signature

    def _verify_manifest(self, state, stage, manifest):
        source = manifest.get("source") or {}
        sha = source.get("sha256") if stage == "ocr" else source.get("pdf_sha256")
        if sha != state["source_sha256"]:
            raise ValueError("处理成果与所选 PDF 内容身份不一致")
        if stage == "ocr" and int(manifest.get("page_count") or 0) < 1:
            raise ValueError("识别成果没有有效页面")
        if stage == "ocr":
            config = state["request"]["config"]
            route = manifest.get("provider_route") or {}
            if (
                route.get("requested"),
                route.get("requested_model"),
                route.get("allow_api_fallback"),
            ) != (
                config["ocr_provider"],
                config["ocr_model"],
                config["allow_cloud_ocr"],
            ):
                raise ValueError("识别成果的实际配置与固定处理请求不一致")
            selection = manifest.get("page_selection") or {}
            selector = (
                (selection.get("selector") or {}).get("name") if selection else "all"
            )
            if selector != config.get("page_selection", "all"):
                raise ValueError(
                    "识别成果的页面范围与固定处理请求不一致；部分页识别不能用于全文处理"
                )
        if stage == "ir":
            if manifest.get("schema_version") != "document-ir-v0.12":
                raise ValueError("结构成果合同不兼容，需要重新构建")
            ocr_id = manifest.get("ocr_run_id") or source.get("ocr_run_id")
            if state.get("ocr_run_id") and ocr_id != state["ocr_run_id"]:
                raise ValueError("结构成果与固定的识别版本不一致")

    def _find_reusable(self, state, stage):
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT a.* FROM report_processing_artifacts a
                JOIN report_processing_runs r USING(processing_id)
                WHERE a.stage=? AND a.source_sha256=? AND a.config_digest=?
                AND r.status NOT IN ('discarded','purged') ORDER BY a.created_at DESC,a.processing_id DESC""",
                (stage, state["source_sha256"], self._config_digest(state, stage)),
            ).fetchall()
        for row in rows:
            try:
                manifest, checksum = self._manifest(stage, row["run_id"])
                self._verify_manifest(state, stage, manifest)
                self.integrity_check(stage, row["run_id"])
                if checksum != row["manifest_sha256"]:
                    raise ValueError("成果清单发生变化，禁止静默复用")
                if stage == "ir":
                    self._verify_ir_ocr(state, manifest)
                if stage == "ir" and not (
                    manifest.get("can_build_evidence")
                    or (
                        state["request"]["config"]["allow_limited_evidence"]
                        and manifest.get("can_build_limited_evidence")
                    )
                ):
                    raise ValueError("已有结构证据不满足本次使用要求")
                return dict(row)
            except (OSError, ValueError, TypeError) as exc:
                state["reuse_diagnostics"].append(
                    {"stage": stage, "run_id": row["run_id"], "reason": str(exc)}
                )
        state["reuse_diagnostics"].append(
            {
                "stage": stage,
                "reason": "没有匹配 PDF、配置和构建版本的已登记成果；旧成果保留但不自动猜测复用",
            }
        )
        return None

    def _verify_ir_ocr(self, state, manifest):
        ocr_id = manifest.get("ocr_run_id") or (manifest.get("source") or {}).get(
            "ocr_run_id"
        )
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT manifest_sha256 FROM report_processing_artifacts WHERE stage='ocr' AND run_id=? AND source_sha256=? AND config_digest=?",
                (ocr_id, state["source_sha256"], self._config_digest(state, "ocr")),
            ).fetchall()
        if not rows:
            raise ValueError("结构成果缺少兼容的识别来源登记")
        ocr_manifest, checksum = self._manifest("ocr", ocr_id)
        self._verify_manifest(state, "ocr", ocr_manifest)
        self.integrity_check("ocr", ocr_id)
        if not any(row[0] == checksum for row in rows):
            raise ValueError("结构成果关联的识别清单发生变化")

    def _record_artifact(self, state, stage):
        run_id = state[f"{stage}_run_id"]
        manifest, checksum = self._manifest(stage, run_id)
        self._verify_manifest(state, stage, manifest)
        self.integrity_check(stage, run_id)
        if stage == "ir":
            self._verify_ir_ocr(state, manifest)
        config_digest = self._config_digest(state, stage)
        if stage == "ir" and state.get("ir_provenance_kind") == "explicit_repair":
            # An explicit repaired revision is usable as a pinned input. Its
            # unknown producer configuration must not masquerade as the old
            # automatic build configuration for subsequent reuse.
            config_digest = digest(
                {"config": config_digest, "explicit_evidence_revision": run_id}
            )
        with self.store._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO report_processing_artifacts VALUES (?,?,?,?,?,?,?)",
                (
                    state["processing_id"],
                    stage,
                    run_id,
                    state["source_sha256"],
                    config_digest,
                    checksum,
                    utc_now(),
                ),
            )
        if stage == "ir":
            self.store.pin_ir(state["processing_id"], run_id)
        return manifest

    def _step(self, state, stage):
        step = state["steps"].setdefault(stage, {"status": "pending", "attempt": 0})
        if step.get("queue_record_deleted") and step.get("requires_new_task"):
            raise ValueError("此阶段执行记录已删除，请从报告资产新建任务。")
        if (
            step.get("queue_record_deleted")
            and step.get("status") in {"completed", "partial"}
            and state.get(f"{stage}_run_id")
        ):
            self._record_artifact(state, stage)
            return True
        key = f"{state['processing_id']}:{stage}:{step.get('attempt', 0)}"
        try:
            task = (
                self.store.get(step["task_id"])
                if step.get("task_id")
                else self.store.find_plan_step(key)
            )
        except KeyError:
            raise ValueError("任务依赖记录缺失，不能自动重复调度；请检查历史清理")
        if task is None:
            if state["engine_versions"] != self.engine_versions:
                raise ValueError(
                    "处理构建版本已变化；原成果保留，请创建新的兼容处理任务"
                )
            current = self.assets.get(state["asset_id"])
            if current["sha256"] != state["source_sha256"]:
                raise ValueError("源 PDF 内容已变化，不能继续原任务")
            token = dispatch_key.set(key)
            try:
                task = self.dispatch(
                    stage,
                    state["request"]["config"],
                    state,
                    self._secrets.get(state["processing_id"], {}),
                )
            finally:
                dispatch_key.reset(token)
        step.update(
            task_id=task.task_id,
            run_id=task.native_job_id,
            status=task.status.value,
            started_at=task.started_at,
            finished_at=task.finished_at,
            error=task.error,
            progress_current=task.progress_current,
            progress_total=task.progress_total,
            message=task.message,
            child_stage=task.stage,
        )
        step["progress"] = (
            task.progress.model_dump(mode="json") if task.progress else None
        )
        step["timing"] = task.timing.model_dump(mode="json")
        step["error_detail"] = (
            task.error_detail.model_dump(mode="json") if task.error_detail else None
        )
        if task.status.value in {"completed", "partial"}:
            state[f"{stage}_run_id"] = task.native_job_id
            self._record_artifact(state, stage)
            return True
        if task.status.value in {"failed", "cancelled", "interrupted"}:
            state.update(
                status=task.status.value,
                error=task.error or f"{STAGE_LABELS[stage]}未完成；已有成果保留",
            )
        else:
            state["status"] = task.status.value
        return False

    def _finish_quality(self, state):
        manifest = self._record_artifact(state, "ir")
        full = bool(manifest.get("can_build_evidence"))
        limited = bool(manifest.get("can_build_limited_evidence"))
        allowed = state["request"]["config"]["allow_limited_evidence"]
        state["evidence_policy"] = manifest.get("evidence_policy") or {}
        state["readiness"] = manifest.get("readiness") or manifest.get(
            "readiness_status"
        )
        state["evidence_status"] = (
            "full" if full else "limited" if limited and allowed else "blocked"
        )
        state["status"] = (
            "ready"
            if full
            else "ready_with_limits"
            if limited and allowed
            else "needs_attention"
        )
        state["steps"]["quality"] = {
            "status": "completed" if full or limited and allowed else "blocked",
            "checked_at": utc_now(),
        }
        if state["status"] == "needs_attention":
            state["error"] = (
                "文档结构已构建，但证据条件未满足；查看问题并显式选择修复版本后继续"
            )
        else:
            state.pop("error", None)

    def tick(self):
        with self._lock:
            for state in self.list():
                if state["status"] == "cancelling":
                    self._observe_cancellation(state)
                    self._save(state, event_stage="cancellation")
                    continue
                if state["status"] not in {"queued", "running"}:
                    continue
                try:
                    if not state.get("ocr_run_id") and not self._step(state, "ocr"):
                        self._save(state, event_stage="ocr")
                        continue
                    if not state.get("ir_run_id") and not self._step(state, "ir"):
                        self._save(state, event_stage="ir")
                        continue
                    self._finish_quality(state)
                except Exception as exc:
                    state.update(
                        status="failed", error=str(exc), evidence_status="not_ready"
                    )
                self._save(
                    state,
                    event_stage="quality" if state["status"] in TERMINAL else "state",
                )

    def _observe_cancellation(self, state):
        """Observe existing children only: a cancelling parent never dispatches."""
        active = False
        for stage, step in state["steps"].items():
            if not step.get("task_id"):
                continue
            try:
                child = self.store.get(step["task_id"])
            except KeyError:
                state.update(
                    status="interrupted",
                    error="取消期间的任务依赖记录缺失，请核对运行进程；已有来源保留",
                )
                return
            step.update(
                status=child.status.value,
                message=child.message,
                error=child.error,
                progress=child.progress.model_dump(mode="json")
                if child.progress
                else None,
                timing=child.timing.model_dump(mode="json"),
            )
            active = active or child.status.value in {"queued", "running"}
            if (
                child.status.value in {"completed", "partial"}
                and stage in {"ocr", "ir"}
                and not state.get(f"{stage}_run_id")
            ):
                state[f"{stage}_run_id"] = child.native_job_id
                try:
                    self._record_artifact(state, stage)
                except (OSError, ValueError, TypeError) as exc:
                    state["error"] = f"执行已停止，阶段成果仍需核对：{exc}"
        if not active:
            state["status"] = "cancelled"

    def action(
        self, processing_id, action, request: ProcessingActionRequest | None = None
    ):
        request = request or ProcessingActionRequest()
        with self._lock:
            state = self.get(processing_id)
            if (
                request.expected_revision is not None
                and request.expected_revision != state["revision"]
            ):
                raise ValueError("任务状态已变化，请刷新后重试")
            if state.get("archived_at") or state["status"] in {"discarded", "purged"}:
                raise ValueError("已归档或丢弃任务不能执行运行操作")
            self._secrets.setdefault(processing_id, {}).update(
                {
                    k: v
                    for k, v in {
                        "qiniu_api_key": request.qiniu_api_key,
                        "ocr_token": request.ocr_token,
                    }.items()
                    if v
                }
            )
            if action == "cancel":
                if state["status"] in {"ready", "ready_with_limits"}:
                    raise ValueError("报告已处理完成，无需取消")
                for step in state["steps"].values():
                    if step.get("task_id"):
                        self.cancel(step["task_id"])
                state["status"] = (
                    "cancelling"
                    if any(
                        self.store.get(step["task_id"]).status.value
                        in {"queued", "running"}
                        for step in state["steps"].values()
                        if step.get("task_id")
                    )
                    else "cancelled"
                )
            elif action == "resume":
                if any(
                    step.get("requires_new_task") for step in state["steps"].values()
                ):
                    raise ValueError("执行记录已删除，请从报告资产新建任务。")
                if state["status"] in {
                    "queued",
                    "running",
                    "ready",
                    "ready_with_limits",
                }:
                    return state
                asset = self.assets.get(state["asset_id"])
                if asset["sha256"] != state["source_sha256"]:
                    raise ValueError("源 PDF 已变化，请创建新的报告处理任务")
                for stage, step in state["steps"].items():
                    if not step.get("task_id"):
                        continue
                    child = self.store.get(step["task_id"])
                    if child.status.value in {"queued", "running"}:
                        raise ValueError("子任务仍在停止中，请等待释放执行槽后恢复")
                    if child.status.value in {"failed", "cancelled", "interrupted"}:
                        step.setdefault("history", []).append(
                            {
                                "task_id": step.pop("task_id"),
                                "run_id": step.get("run_id"),
                                "status": child.status.value,
                            }
                        )
                        step.update(
                            attempt=step.get("attempt", 0) + 1, status="pending"
                        )
                        state.pop(f"{stage}_run_id", None)
                state.update(status="queued", evidence_status="not_ready")
                state.pop("error", None)
            else:
                raise ValueError("不支持的报告处理操作")
            self._save(state, event_stage=action)
            return state

    def transition_evidence(self, processing_id, request: EvidenceTransitionRequest):
        with self._lock:
            state = self.get(processing_id)
            if state["revision"] != request.expected_revision:
                raise ValueError("任务状态已变化，请刷新后重试")
            if state["status"] in {
                "queued",
                "running",
                "cancelling",
                "discarded",
                "purged",
            } or state.get("archived_at"):
                raise ValueError("当前任务不允许切换证据版本")
            manifest, _ = self._manifest("ir", request.ir_run_id)
            self._verify_manifest(state, "ir", manifest)
            previous = state.get("ir_run_id")
            state.setdefault("evidence_transitions", []).append(
                {
                    "from": previous,
                    "to": request.ir_run_id,
                    "at": utc_now(),
                    "reason": "用户显式选择证据修复版本",
                }
            )
            state["ir_run_id"] = request.ir_run_id
            state["ir_provenance_kind"] = "explicit_repair"
            state["steps"]["ir"] = {
                "status": "completed",
                "run_id": request.ir_run_id,
                "explicit_transition": True,
            }
            self._finish_quality(state)
            self._save(state, event_stage="evidence_transition")
            return state

    def evidence_versions(self, processing_id):
        state = self.get(processing_id)
        result = []
        for path in self.roots["ir"].glob("*/manifest.json"):
            try:
                manifest, _ = self._manifest("ir", path.parent.name)
                self._verify_manifest(state, "ir", manifest)
                usable = bool(
                    manifest.get("can_build_evidence")
                    or (
                        state["request"]["config"]["allow_limited_evidence"]
                        and manifest.get("can_build_limited_evidence")
                    )
                )
                result.append(
                    {
                        "ir_run_id": path.parent.name,
                        "revision": manifest.get("ir_revision"),
                        "parent_ir_run_id": manifest.get("parent_ir_run_id"),
                        "readiness": manifest.get("readiness"),
                        "evidence_status": "full"
                        if manifest.get("can_build_evidence")
                        else "limited"
                        if usable
                        else "blocked",
                        "usable": usable,
                        "current": path.parent.name == state.get("ir_run_id"),
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        return sorted(
            result,
            key=lambda item: (item.get("revision") or 0, item["ir_run_id"]),
            reverse=True,
        )

    def detail(self, state):
        result = dict(state)
        result["report"] = self.report(state["report_id"])
        result["current_evidence"] = self.current_evidence(state)
        result["execution_status"] = (
            "finished"
            if state["status"] in {"ready", "ready_with_limits", "needs_attention"}
            else state["status"]
        )
        result["stages"] = [
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                **state["steps"].get(stage, {"status": "pending"}),
                "progress_unit": "pages"
                if stage == "ocr"
                else "objects"
                if stage == "ir"
                else "checks",
            }
            for stage in STAGE_LABELS
        ]
        actions = []
        if not state.get("archived_at") and state["status"] in {"queued", "running"}:
            actions.append(
                {
                    "action": "cancel",
                    "label": "取消处理",
                    "calls_model": False,
                    "incurs_cost": False,
                }
            )
        if not state.get("archived_at") and state["status"] in {
            "failed",
            "interrupted",
            "cancelled",
        }:
            actions.append(
                {
                    "action": "resume",
                    "label": "恢复未完成阶段",
                    "calls_model": True,
                    "incurs_cost": state["request"]["config"]["allow_cloud_ocr"]
                    or state["request"]["config"]["review_provider"] == "qiniu",
                }
            )
        for action in actions:
            action.update(
                endpoint=f"/api/report-processing/runs/{state['processing_id']}/{action['action']}",
                method="POST",
                payload={"expected_revision": state["revision"]},
            )
        if state.get("ir_run_id") and state["status"] not in {"discarded", "purged"}:
            actions.append(
                {
                    "action": "inspect_evidence",
                    "label": "查看文档与证据",
                    "ir_run_id": state["ir_run_id"],
                    "calls_model": False,
                    "incurs_cost": False,
                }
            )
            if state["status"] not in {
                "queued",
                "running",
                "cancelling",
            } and not state.get("archived_at"):
                actions.append(
                    {
                        "action": "select_evidence_version",
                        "label": "选择修复后的证据版本",
                        "calls_model": False,
                        "incurs_cost": False,
                        "endpoint": f"/api/report-processing/runs/{state['processing_id']}/evidence-versions",
                        "method": "GET",
                    }
                )
                if (
                    state["status"] in {"needs_attention", "ready_with_limits"}
                    or not result["current_evidence"]["ready"]
                ):
                    actions.append(
                        {
                            "action": "review_evidence",
                            "label": "复核文档证据问题",
                            "ir_run_id": state["ir_run_id"],
                            "calls_model": False,
                            "incurs_cost": False,
                        }
                    )
        if (
            state["status"] in {"ready", "ready_with_limits"}
            and result["current_evidence"]["ready"]
            and not state.get("archived_at")
        ):
            actions.append(
                {
                    "action": "start_extraction",
                    "label": "选择条款开始提取",
                    "asset_id": state["asset_id"],
                    "ir_run_id": state["ir_run_id"],
                    "calls_model": False,
                    "incurs_cost": False,
                }
            )
        lifecycle_actions = getattr(self, "lifecycle_actions", None)
        if lifecycle_actions:
            result["lifecycle"] = lifecycle_actions(state)
            actions.extend(result["lifecycle"]["available_actions"])
        result["available_actions"] = actions
        return result

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="report-processing-coordinator", daemon=True
        )
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(2)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
