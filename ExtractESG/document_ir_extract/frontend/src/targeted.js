import {publishTask, readTask} from "./task-store.js";
import {reportFactsTable, evidenceTable, stageSummaryView, gapSummary} from "./results.js";
import {disclosureProgress, agentProgress, providerLabel} from "./disclosure.js?v=report-fact-agent-20261001";
import {reportIdentity} from "./selection.js?v=input-selectors-v3-20260829";
import {docRequest, targetedRequest, serviceBase} from "./api.js";
import {subscribe, unsubscribe} from "./subscriptions.js";
import {statusLabel, taskRevision} from "./status.js";
import {progressView, stageLabel, historicalActiveCall, taskErrorView} from "./progress.js";
import {getSecret} from "./roles.js";
function metricSortDescription(metric) {
  return `后端统一组织：${metric.logical_measurement_count ?? metric.facts.length} 个逻辑测量；保留全部原始展示及证据`;
}
const $ = (selector) => document.querySelector(selector);

const targetedState = {
  backend: localStorage.getItem("esg-targeted-backend-url") || "http://127.0.0.1:18180",
  config: null,
  irRuns: [],
  standards: [],
  standardCatalog: null,
  standard: null,
  standardDetails: new Map(),

  jobs: [],
  resultCatalog: null,
  activeJob: null,
  inspection: null,
  events: [],
  artifacts: [],
  semanticIndexes: [],
  selectedMetricId: null,
  selectedFactId: null,
  selectedIrRunId: localStorage.getItem("esg-targeted-ir-run-id") || "",
};

const txRequest = (path, options = {}, timeoutMs) => targetedRequest(path, options, timeoutMs);
const docBase = () => serviceBase("document");
let resultPerspective = "facts";
let resultViewActive = false;
let activeAuditTab = "inspection";
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function elapsedSeconds(job) {
  const start = Date.parse(job?.created_at || "");
  const end = ["queued", "running"].includes(job?.status) ? Date.now() : Date.parse(job?.updated_at || "");
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.round((end - start) / 1000)) : null;
}

function metricName(metric) {
  return metric.labels?.zh || metric.labels?.en || metric.source_datapoint_id || metric.metric_id;
}

function jobDocumentIdentity(job, inspection = null) {
  const runId = inspection?.document?.ir_run_id || job?.request?.ir_run_id || "";
  const catalogRun = targetedState.irRuns.find((run) => run.run_id === runId) || {};
  const summaryDocument = job?.summary?.document || {};
  const document = inspection?.document || {};
  const documentLabel = document.document_label
    || summaryDocument.document_label
    || catalogRun.document_label
    || document.document_id
    || summaryDocument.document_id
    || catalogRun.document_id
    || runId
    || "未知报告";
  const identity = reportIdentity(documentLabel, runId);
  return {
    displayName: identity.displayName || documentLabel,
    year: identity.year,
    documentId: document.document_id || summaryDocument.document_id || catalogRun.document_id || "未记录",
    runId: runId || "未记录",
    revision: document.ir_revision ?? summaryDocument.ir_revision ?? catalogRun.ir_revision ?? null,
    schemaVersion: document.ir_schema_version || summaryDocument.ir_schema_version || catalogRun.schema_version || "未记录",
  };
}

function factSummary(metric, limit = 140) {
  if (!metric.facts?.length && metric.processing_status === "not_executed") return "本项未执行；不能判断报告是否披露。";
  if (!metric.facts?.length && ["processing", "unfinished"].includes(metric.processing_status)) return `本项已进入处理，尚未形成检查点；执行${statusLabel(metric.execution_status)}。`;
  if (!metric.facts?.length) return metric.uncertainty_reason || (metric.search_resolution_status && metric.search_resolution_status !== "not_applicable" ? statusLabel(metric.search_resolution_status) : metric.status === "not_found" ? "未找到（历史结果未记录检索充分性）" : "尚未产生事实记录");
  const text = metric.facts.map((fact) => fact.display_value || fact.statement_summary || fact.statement_raw || "").filter(Boolean).join("；");
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function periodBoundarySummary(metric) {
  const values = (metric.facts || []).map((fact) => [
    fact.reporting_period_raw || fact.reporting_year || [fact.period_start, fact.period_end].filter(Boolean).join(" 至 "),
    fact.reporting_boundary,
  ].filter(Boolean).join(" · ")).filter(Boolean);
  return [...new Set(values)].join("；") || "未单独识别";
}

function evidenceLocationSummary(metric) {
  const evidence = (metric.facts || []).flatMap((fact) => fact.evidence || []);
  if (!evidence.length) return "无事实证据记录";
  return [...new Set(evidence.map((item) => `PDF 第 ${item.pdf_page_number} 页${item.printed_page_label ? `（报告页 ${item.printed_page_label}）` : ""}`))].join("；");
}

async function loadTargetedBootstrap() {
  const names = ['health', 'config', 'irRuns', 'standards', 'standardCatalog', 'resultCatalog', 'compatibility'];
  const outcomes = await Promise.allSettled([
    txRequest("/api/health"), txRequest("/api/config"), txRequest("/api/ir-runs"),
    txRequest("/api/standards"), txRequest("/api/standards/diagnostics"), txRequest("/api/result-catalog"),
    docRequest('/api/pipeline/compatibility'),
  ]);
  return Object.fromEntries(names.map((name, index) => [name, outcomes[index]]));
}

async function connectTargeted({allowDiscovery = false} = {}) {
  const requestedBackend = ($("#tx-backend-url")?.value || targetedState.backend).replace(/\/$/, "");
  targetedState.backend = requestedBackend;
  const pill = $("#tx-connection");
  pill.textContent = "连接中";
  let bootstrap;
  try {
    let discovered = false;
    try {
      bootstrap = await loadTargetedBootstrap();
      if (bootstrap.health.status === 'rejected') throw bootstrap.health.reason;
    } catch (initialError) {
      if (!allowDiscovery) throw initialError;
      const status = await docRequest("/api/pipeline/status");
      const candidate = status.services?.targeted_backend?.url?.replace(/\/$/, "");
      if (!status.services?.targeted_backend?.available || !candidate || candidate === requestedBackend) {
        throw initialError;
      }
      targetedState.backend = candidate;
      $("#tx-backend-url").value = candidate;
      bootstrap = await loadTargetedBootstrap();
      if (bootstrap.health.status === 'rejected') throw bootstrap.health.reason;
      discovered = true;
    }
    const health = bootstrap.health.value;
    localStorage.setItem("esg-targeted-backend-url", targetedState.backend);
    $("#tx-backend-url").value = targetedState.backend;
    for (const key of ['config', 'irRuns', 'standards', 'standardCatalog', 'resultCatalog']) {
      if (bootstrap[key].status === 'fulfilled') targetedState[key] = bootstrap[key].value;
    }
    const compatibility = bootstrap.compatibility.status === 'fulfilled' ? bootstrap.compatibility.value : null;
    const buildMatches = health.compatibility?.build_fingerprint &&
      health.compatibility.build_fingerprint === compatibility?.targeted?.build_fingerprint;
    const ready = compatibility?.ready && health.compatibility?.readiness?.ready && buildMatches;
    pill.textContent = ready ? (discovered ? "后端已就绪 · 地址已自动恢复" : "后端已就绪") : "服务已连接 · 版本或合同未就绪";
    pill.className = `status-pill ${ready ? 'success' : 'warning'}`;
    renderTargetHealth(health);
    const names = {config: '运行配置', irRuns: '已有证据', standards: '标准目录', standardCatalog: '包校验诊断', resultCatalog: '成果目录', compatibility: '版本握手'};
    const errors = Object.entries(names).filter(([key]) => bootstrap[key].status === 'rejected')
      .map(([key, label]) => `${label}读取失败：${bootstrap[key].reason.message}`);
    if (!ready) errors.unshift(`兼容诊断：${compatibility?.blockers?.join('；') || '服务缺少当前构建与合同握手，不能创建新任务'}`);
    if (errors.length) $("#tx-health-grid").insertAdjacentHTML('beforeend', `<div class="empty-state warning">${errors.map(esc).join('。')}。已成功读取的目录与历史成果保留。</div>`);
    if (document.body.dataset.activeView === "settings") await refreshSemanticIndexes();
    await refreshTargetJobs();
  } catch (error) {
    if (bootstrap) for (const key of ['config', 'irRuns', 'standards', 'standardCatalog', 'resultCatalog']) {
      if (bootstrap[key].status === 'fulfilled') targetedState[key] = bootstrap[key].value;
    }
    pill.textContent = "后端未连接";
    pill.className = "status-pill error";
    $("#tx-health-grid").innerHTML = `<div class="empty-state error">请确认定向抽取后端 18180 已启动：${esc(error.message)}</div>`;
    await refreshTargetJobs();  // A health failure must not erase readable history.
  }
}

async function refreshSemanticIndexes() {
  const root = $("#semantic-index-list");
  if (!root) return;
  try {
    targetedState.semanticIndexes = await txRequest("/api/semantic-indexes");
    renderSemanticIndexes();
  } catch (error) {
    root.innerHTML = `<div class="empty-state error">语义索引读取失败：${esc(error.message)}</div>`;
  }
}

function renderSemanticIndexes() {
  const items = targetedState.semanticIndexes || [];
  const totalBytes = items.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0);
  $("#semantic-index-summary").innerHTML = [
    [items.length, "独立索引"],
    [formatBytes(totalBytes), "缓存体积"],
    [items.filter((item) => item.cache_ready).length, "可直接复用"],
  ].map(([value, label]) => `<div class="storage-metric"><strong>${esc(value)}</strong><span>${label}</span></div>`).join("");
  $("#semantic-index-list").innerHTML = items.map((item) => {
    const label = item.document_label || item.document_id || item.inventory_id;
    const model = String(item.model_path || "未记录").split("/").pop();
    const indexed = Number(item.semantic_indexed_span_count || 0);
    const eligible = Number(item.retrieval_eligible_span_count || 0);
    const updated = item.updated_at ? new Date(item.updated_at).toLocaleString("zh-CN", {hour12: false}) : "未记录";
    return `<article class="report-asset-row">
      <div class="report-asset-main"><strong>${esc(label)}</strong><span>${esc(item.ir_run_id || item.inventory_id)} · revision ${esc(item.ir_revision ?? "-")}</span><small>${esc(model)} · ${esc(item.index_policy || "legacy")} · 语义代表 ${indexed.toLocaleString()} / 可检索 ${eligible.toLocaleString()}</small></div>
      <div class="report-asset-state"><span class="status-pill ${item.cache_ready ? "success" : "warning"}">${item.cache_ready ? "可复用" : "不完整"}</span><small>${esc(formatBytes(item.size_bytes || 0))} · ${esc(updated)}</small></div>
      <div class="button-row"><button class="danger semantic-index-delete" data-inventory-id="${esc(item.inventory_id)}" type="button">删除索引</button></div>
    </article>`;
  }).join("") || '<div class="empty-state">还没有本地语义索引。首次对某份 IR 执行语义检索后会在这里建立。</div>';
  document.querySelectorAll(".semantic-index-delete").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm(`确认删除语义索引 ${button.dataset.inventoryId} 吗？下次抽取会重新构建。`)) return;
    try {
      await txRequest(`/api/semantic-indexes/${encodeURIComponent(button.dataset.inventoryId)}`, {method: "DELETE"});
      if (document.body.dataset.activeView === "settings") await refreshSemanticIndexes();
    } catch (error) {
      window.alert(`索引删除失败：${error.message}`);
    }
  }));
}

function renderTargetHealth(health) {
  const cards = [
    [health.catalog?.evidence_ready_ir_count || 0, "可抽取 IR"],
    [health.catalog?.standard_package_count || 0, "标准包"],
    [health.catalog?.indexed_result_job_count || 0, "结果任务"],
    [health.models?.embedding?.available ? "就绪" : "缺失", "Qwen3 Embedding"],
    [health.models?.nuextract?.available ? "就绪" : "缺失", "NuExtract3"],
    [health.models?.local_qwen?.available ? "权重可用" : "缺失", "Qwen3.5 视觉语义"],
    [health.models?.qiniu_vlm?.configured ? "已配置" : "未配置", "七牛云 VLM"],
  ];
  $("#tx-health-grid").innerHTML = cards.map(([value, label]) => `<article><strong>${esc(value)}</strong><span>${label}</span></article>`).join("");
}

async function refreshTargetJobs(signal) {
  signal = signal instanceof AbortSignal ? signal : undefined;
  try {
    const [jobs, resultCatalog] = await Promise.all([
      txRequest("/api/jobs?limit=500", {signal}),
      txRequest("/api/result-catalog", {signal}).catch((error) => ({error: error.message})),
    ]);
    targetedState.jobs = jobs;
    targetedState.resultCatalog = resultCatalog;
    renderTargetJobs();
    if (targetedState.activeJob) {
      const updated = targetedState.jobs.find((item) => item.job_id === targetedState.activeJob.job_id);
      if (updated && taskRevision(updated) !== taskRevision(readTask(`job:${updated.job_id}`))) await selectTargetJob(updated.job_id, false, signal);
    }
  } catch (error) {
    if (signal?.aborted) return;
    $("#tx-job-list").innerHTML = `<div class="empty-state error">定向抽取任务读取失败：${esc(error.message)}</div>`;
  }
}

function renderTargetJobs() {
  const catalog = targetedState.resultCatalog || {};
  const warnings = [];
  if (catalog.error) {
    warnings.push(`<div class="empty-state result-catalog-empty warning"><strong>结果目录诊断不可用</strong><span>${esc(catalog.error)}</span></div>`);
  } else if (Number(catalog.orphan_artifact_count || 0)) {
    warnings.push(`<div class="empty-state result-catalog-empty warning"><strong>发现 ${Number(catalog.orphan_artifact_count)} 个未进入任务索引的结果目录</strong><span>后端只会自动恢复包含有效请求和终态标记的 Result Bundle；其余目录需要检查完整性。</span><code>${esc(catalog.output_root || "")}</code></div>`);
  }
  if (Number(catalog.missing_artifact_count || 0)) {
    warnings.push(`<div class="empty-state result-catalog-empty warning"><strong>${Number(catalog.missing_artifact_count)} 条任务记录缺少结果目录</strong><span>任务索引仍在，但对应中间文件或成果已被移动或清除。</span><code>${esc(catalog.output_root || "")}</code></div>`);
  }
  const cards = targetedState.jobs.map((job) => {
    const provider = providerLabel(job.request.semantic_provider, job.request.semantic_model);
    const report = jobDocumentIdentity(job);
    return `<button class="job-card ${targetedState.activeJob?.job_id === job.job_id ? "active" : ""}" data-tx-job="${esc(job.job_id)}" type="button">
      <div class="job-card-head"><div class="job-card-report"><span class="year-badge">${esc(report.year)}</span><strong title="${esc(report.displayName)}">${esc(report.displayName)}</strong></div><span class="status-pill ${esc(job.status)}">${statusLabel(job.status)}</span></div>
      <p>${esc(targetedState.standards.find((item) => item.package_id === job.request.package_id)?.disclosure_requirement || job.request.package_id.split(".").at(-1).toUpperCase())} · ${job.request.metric_ids.length || "全部"} 指标</p>
      <small>${esc(provider)}</small>
      <span class="muted">文档证据版本 ${report.revision === null ? "未记录" : `r${report.revision}`}</span>
      ${progressView(job)}
    </button>`;
  }).join("");
  if (!cards && !warnings.length) {
    const indexed = Number(catalog.indexed_job_count || 0);
    const bundles = Number(catalog.artifact_job_count || 0);
    warnings.push(`<div class="empty-state result-catalog-empty"><strong>当前没有可展示的定向抽取结果</strong><span>后端任务索引 ${indexed} 条，结果目录 ${bundles} 个；不是前端筛选造成的隐藏。新任务产生记录后会自动出现在这里。</span>${catalog.output_root ? `<code>${esc(catalog.output_root)}</code>` : ""}</div>`);
  }
  $("#tx-job-list").innerHTML = `${warnings.join("")}${cards}`;
  document.querySelectorAll("[data-tx-job]").forEach((button) => button.addEventListener("click", () => selectTargetJob(button.dataset.txJob)));
}

async function selectTargetJob(jobId, rerender = true, signal) {
  const [job, inspection] = await Promise.all([
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}`, {signal}),
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}/inspection`, {signal}).catch((error) => ({availability: "unavailable", message: error.message, metrics: [], overview: {}})),
  ]);
  targetedState.activeJob = job;
  publishTask(`job:${jobId}`, job);
  targetedState.inspection = inspection;
  targetedState.events = [];
  targetedState.artifacts = [];
  await loadTargetAudit(activeAuditTab, signal);
  if (rerender) renderTargetJobs();
  renderTargetJobDetail();
}

function renderTargetJobDetail() {
  const job = targetedState.activeJob;
  const summary = job.summary || {};
  const execution = targetedState.inspection?.model_execution || summary.model_execution || {};
  const evidencePolicy = targetedState.inspection?.document?.evidence_policy || summary.document?.evidence_policy || {};
  const accountBlocked = summary.failure_category === "provider_account_blocked"
    || targetedState.inspection?.agent?.current_call?.failure_category === "provider_account_blocked"
    || (targetedState.inspection?.metrics || []).some((metric) =>
    (metric.decision?.attempts || []).some((attempt) =>
      attempt.provider_error_code === "account_billing_suspended"
      || attempt.failure_category === "provider_account_blocked"
      || String(attempt.error || "").includes("account_billing_suspended")
    )
  );
  const repairableContractFailure = (targetedState.inspection?.metrics || []).some(
    (metric) => metric.contract_validation_status === "failed"
  ) || /ResultContractError|duplicates dimension_value_id/.test(String(job.error || ""));
  const report = jobDocumentIdentity(job, targetedState.inspection);
  const modelCalls = targetedState.events.filter((event) => event.stage === "model_call" && (event.detail?.phase === "started" || String(event.message).endsWith("started"))).length;
  $("#tx-job-title").textContent = report.displayName;
  const documentRoot = $("#tx-result-document");
  documentRoot.className = "result-document-banner";
  documentRoot.innerHTML = `<div class="result-document-primary"><span class="year-badge">${esc(report.year)}</span><div><small>当前成果对应报告</small><h3>${esc(report.displayName)}</h3><p>所有指标、事实和证据均来自该报告固定的 Document IR Revision。</p></div></div><details class="result-document-identifiers technical-details"><summary>文档技术身份</summary><span><small>Document ID</small><code>${esc(report.documentId)}</code></span><span><small>Document IR</small><code>${esc(report.runId)}${report.revision === null ? "" : ` · r${report.revision}`}</code></span></details>`;
  $("#tx-job-summary").className = "job-summary";
  const legacyElapsed = elapsedSeconds(job);
  const elapsedCell = Number.isFinite(job.timing?.wall_elapsed_seconds)
    ? ["执行墙钟", `${Math.round(job.timing.wall_elapsed_seconds)} 秒`]
    : ["任务记录跨度", legacyElapsed === null ? "未记录" : `${legacyElapsed} 秒`, "按创建与最后更新时间计算，含排队；执行与未观测时间分解未记录"];
  const cells = [
    ["状态", statusLabel(job.status)], ["阶段", stageLabel(job.stage)], ["条款处理", job.progress?.total ? `${job.progress.current} / ${job.progress.total} 已处理条款` : "当前没有可计算的工作总量"],
    elapsedCell, ["模型调用", summary.model_call_count ?? (targetedState.events.length ? modelCalls : "未记录")],
    ["Guard 通过", summary.guard_accepted_count ?? "处理中"], ["IR Revision", report.revision === null ? "未记录" : `r${report.revision}`],
    ["任务 ID", job.job_id], ["IR Schema", report.schemaVersion],
    ["标准包", `${job.request.package_id}@${job.request.package_version}`],
    ["语义检索", job.request.semantic_search ? "Qwen3 + BM25" : "仅 BM25"],
    ["检索候选预算", `Top ${job.request.retrieval_object_top_n || 3}`],
    ["提取路径", job.request.extraction_mode === "per_metric" ? "逐指标兼容" : "源表语义 Agent / 成熟路径"],
    ...(job.request.extraction_mode !== "per_metric" ? [["每主题初始共享预算", job.request.shared_object_budget ?? 3], ["每主题 Agent 步骤预算", job.request.agent_max_steps ?? 8]] : []),
    ["请求模型", job.request.semantic_fill ? providerLabel(job.request.semantic_provider, job.request.semantic_model) : "关闭"],
    ["实际执行", (execution.actual_models || []).join("；") || (accountBlocked ? "七牛账户拒绝请求；未获得模型输出" : ["completed", "partial", "failed", "cancelled", "interrupted"].includes(job.status) ? "任务已结束，未记录有效模型响应" : "处理中")],
    ...(evidencePolicy.mode === "limited" ? [["证据范围", `受限：可用 ${evidencePolicy.available_page_count || 0} 页，排除 ${(evidencePolicy.excluded_page_indices || []).length} 页；排除页不代表未披露`]] : []),
  ];
  $("#tx-job-summary").innerHTML = `${cells.slice(0, 5).map(([label, value, hint]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong>${hint ? `<small>${esc(hint)}</small>` : ""}</div>`).join("")}<details class="technical-details"><summary>运行配置与审计</summary>${cells.slice(5).map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</details>${taskErrorView(job)}`;
  const agent = targetedState.inspection?.agent || summary.agent;
  const historicalCall = historicalActiveCall(job, agent?.current_call);
  const auditAgent = historicalCall ? {...agent, state: job.status, current_call: null} : agent;
  const historicalAudit = historicalCall ? `<p>历史调用记录，任务已停止。原始调用阶段只作为技术记录保留。</p><details class="technical-details"><summary>原始调用记录</summary><pre>${esc(JSON.stringify(agent, null, 2))}</pre></details>` : "";
  $("#tx-agent-progress").innerHTML = `<details class="technical-details"><summary>模型动作与调用审计</summary>${historicalAudit}${agentProgress(auditAgent, job.request)}</details>`;
  $("#tx-resume-job").textContent = repairableContractFailure
    ? "修复后重建结果（不调用模型）"
    : "加入断点续跑";
  $("#tx-resume-job").classList.toggle("hidden", !["interrupted", "failed", "cancelled", "partial"].includes(job.status));
  $("#tx-cancel-job").classList.toggle("hidden", !["queued", "running"].includes(job.status));
  const lifecycleActions = job.lifecycle?.available_actions || [];
  const primaryLifecycleAction = lifecycleActions.find((action) => ["archive", "open_parent"].includes(action.action));
  $("#tx-delete-job").textContent = primaryLifecycleAction?.label || "归档记录";
  $("#tx-delete-job").classList.toggle("hidden", !primaryLifecycleAction);
  renderTargetInspection();
  renderTargetOutcomes();
  renderTargetEvents();
  renderTargetArtifacts();
}

function renderTargetInspection() {
  const inspection = targetedState.inspection;
  if (!inspection || (!["ready", "partial"].includes(inspection.availability) && !inspection.metrics?.length && !inspection.report_facts?.length)) {
    $("#tx-inspection-summary").innerHTML = `<div class="empty-state">${esc(inspection?.message || "任务完成后生成成果检查。")}</div>`;
    $("#tx-model-execution-banner").innerHTML = "";
    $("#tx-integrity-banner").innerHTML = "";
    $("#tx-result-downloads").innerHTML = "";
    $("#tx-result-table").innerHTML = "";
    $("#tx-result-detail").innerHTML = "";
    return;
  }
  const overview = inspection.overview || {};
  const accountBlockedCount = (inspection.metrics || []).filter(metricProviderAccountBlocked).length;
  const cards = [
    [overview.metric_count, "指标任务", "本次选择"],
    [overview.found_count, "已找到", `部分 ${overview.partial_count || 0}`],
    [(overview.quantitative_fact_count || 0) + (overview.qualitative_fact_count || 0), "结构化事实", `量化 ${overview.quantitative_fact_count || 0} · 定性 ${overview.qualitative_fact_count || 0}`],
    [overview.evidence_count, "证据引用", "均保留 IR 定位"],
    [Math.max(0, (overview.human_review_count || 0) - accountBlockedCount), "需人工判断", "仅异常或歧义"],
    ...(accountBlockedCount ? [[accountBlockedCount, "服务商阻塞", "账户恢复后重跑"]] : []),
  ];
  $("#tx-inspection-summary").innerHTML = `${inspection.availability !== "ready" ? `<p class="hint">${esc(inspection.message)}</p>` : ""}${stageSummaryView(inspection.stage_summary, overview)}${cards.map(([value, label, note]) => `<article><strong>${value || 0}</strong><span>${label}</span><small>${note}</small></article>`).join("")}${gapSummary(inspection.gaps)}`;
  const execution = inspection.model_execution || {};
  const requested = providerLabel(execution.requested_provider || targetedState.activeJob.request.semantic_provider, execution.requested_model);
  const actual = (execution.actual_models || []).join("；") || (
    accountBlockedCount ? "请求被七牛账户计费状态拒绝；无有效模型响应" : "本任务没有有效模型响应或旧成果未记录"
  );
  const matchClass = execution.provider_match === false ? "failed" : execution.provider_match === true ? "passed" : "pending";
  $("#tx-model-execution-banner").className = `model-execution-banner ${matchClass}`;
  $("#tx-model-execution-banner").innerHTML = `<div><small>请求通道</small><strong>${esc(requested)}</strong></div><span>→</span><div><small>实际执行</small><strong>${esc(actual)}</strong></div>${execution.provider_match === false ? '<b>通道不一致：该成果不应视为按所选模型完成</b>' : ""}`;
  const issues = inspection.integrity_issues || [];
  $("#tx-integrity-banner").className = `integrity-banner ${esc(inspection.integrity_status)}`;
  $("#tx-integrity-banner").innerHTML = issues.length
    ? `<strong>成果合同 ${statusLabel(inspection.integrity_status)}</strong><span>${issues.map((item) => esc(item.message)).join("；")}</span>`
    : (overview.quantitative_fact_count || overview.qualitative_fact_count)
      ? `<strong>${inspection.availability === "ready" ? "成果" : "阶段 Core"}合同校验通过</strong><span>已核对 ${(overview.quantitative_fact_count || 0) + (overview.qualitative_fact_count || 0)} 条 Core 事实及其关联；来源候选仍独立确认。</span>`
      : "<strong>无 Core 事实记录可验证</strong><span>任务结构检查不表示来源候选已通过业务核验。</span>";
  const downloadLabels = {workbook: "下载机器表", fact_csv: "下载事实 CSV", task_csv: "下载任务 CSV", result_package: "下载成果 JSON", source_facts: "下载共享源事实", metric_source_mappings: "下载指标映射", disclosure_views: "下载来源展示引用", stage_snapshot: "下载阶段快照"};
  $("#tx-result-downloads").innerHTML = Object.entries(inspection.downloads || {}).map(([key, path]) => `<a class="button-link" href="${targetedState.backend}/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/download?path=${encodeURIComponent(path)}">${esc(downloadLabels[key] || key)}</a>`).join("");
  $("#tx-result-downloads").insertAdjacentHTML("beforeend", disclosureProgress(inspection.disclosure_plan, path => `${targetedState.backend}/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/download?path=${encodeURIComponent(path)}`));
  renderTargetMetricTable();
}

function displayMachineValue(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function elementLabel(item) {
  return item.label?.zh || item.label?.en || item.element_code || item.element_id;
}

function factEvidenceLocation(fact) {
  const evidence = fact?.evidence || [];
  if (!evidence.length) return "";
  return [...new Set(evidence.map((item) => `PDF 第 ${item.pdf_page_number} 页${item.printed_page_label ? `（报告页 ${item.printed_page_label}）` : ""}`))].join("；");
}

function sourceKindLabel(kind) {
  return {ir_table: "IR 表格", visual_table: "视觉表格", text: "正文文本", unknown: "未识别"}[kind] || kind || "未识别";
}

function provenanceModeLabel(mode) {
  return {candidate: "C 字面量", span: "S 原文", visual: "V 视觉直读"}[mode] || "";
}

function factProvenanceLabel(fact) {
  const modes = fact?.provenance_modes || [];
  return modes.map(provenanceModeLabel).filter(Boolean).join(" / ");
}

const elementRoleOrder = ["subject", "category", "dimension", "value", "unit", "period", "scope", "method", "context"];

function elementSemanticRole(column) {
  const declared = String(column?.semantic_role || "").toLowerCase();
  if (declared) return declared;
  const target = String(column?.binding_target || "").toLowerCase();
  if (target === "value_raw") return "value";
  if (target === "unit_raw" || target === "unit_id") return "unit";
  if (["reporting_period_raw", "reporting_year", "period_start", "period_end"].includes(target)) return "period";
  if (target === "reporting_boundary") return "scope";
  return "context";
}

function orderedElementColumns(metric) {
  const declared = metric.element_columns || [];
  const seen = new Set(declared.map((column) => column.element_id || column.element_code));
  const discovered = (metric.facts || []).flatMap((fact) => fact.element_cells || []).filter((cell) => {
    const key = cell.element_id || cell.element_code;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return [...declared, ...discovered]; // Canonical order is owned by the backend.
}

function elementDisplayLabel(column) {
  return elementLabel(column);
}

function factElementCell(fact, column) {
  if (!fact) return null;
  return (fact.element_cells || []).find((cell) =>
    (column.element_id && cell.element_id === column.element_id)
    || (column.element_code && cell.element_code === column.element_code)
  ) || null;
}

function factElementDisplayValue(fact, column) {
  const cell = factElementCell(fact, column);
  let value = cell?.value;
  const role = elementSemanticRole(column);
  const target = String(column.binding_target || "").toLowerCase();
  if (value === null || value === undefined || value === "") {
    if (role === "value" || target === "value_raw") value = fact?.value_raw ?? fact?.display_value;
    else if (role === "unit" || target === "unit_raw") value = fact?.unit_raw;
    else if (role === "period") value = fact?.reporting_period_raw ?? fact?.reporting_year;
    else if (role === "scope" && target === "reporting_boundary") value = fact?.reporting_boundary;
  }
  if (Array.isArray(value)) return value.map(displayMachineValue).filter(Boolean).join(" / ");
  return displayMachineValue(value);
}

function metricEvidenceCount(metric) {
  const keys = new Set();
  (metric.facts || []).forEach((fact) => (fact.evidence || []).forEach((item, index) => {
    keys.add(item.evidence_id || `${fact.fact_id || "fact"}:${item.ir_object_id || item.excerpt || index}`);
  }));
  return keys.size;
}

function metricQualityClass(metric) {
  if (metric.contract_validation_status === "failed") return "quality-error";
  if (metric.guard_accepted === false) return "quality-error";
  if ((metric.facts || []).some((fact) => (fact.quality_issues || []).length)) return "quality-warning";
  return "quality-ok";
}

function factQualitySummary(metric, fact) {
  const issues = fact?.quality_issues || [];
  if (issues.length) return issues.map((item) => item.message).join("；");
  if (metric.display_readiness_status === "needs_semantic_completion") return "语义身份待补全";
  return metric.guard_accepted === true ? "来源绑定通过" : metric.guard_accepted === false ? "来源绑定未通过" : "来源绑定未执行";
}

function metricVerificationSummary(metric) {
  const stages = [
    `来源 ${statusLabel(metric.source_validation_status)}`,
    `合同 ${statusLabel(metric.contract_validation_status || "not_run")}`,
    `语义 ${statusLabel(metric.semantic_decision_status)}`,
    `展示 ${statusLabel(metric.display_readiness_status)}`,
  ];
  if (metric.search_resolution_status && metric.search_resolution_status !== "not_applicable") stages.push(`检索 ${statusLabel(metric.search_resolution_status)}`);
  if (metric.dependency_status && metric.dependency_status !== "not_applicable") stages.push(`派生依赖 ${metric.dependency_status === "blocked" ? "阻塞" : "就绪"}`);
  return stages.join(" · ");
}

function renderMetricFactTable(metric, facts) {
  const columns = orderedElementColumns(metric);
  const trailingColumnCount = 3;
  const columnCount = Math.max(1, columns.length + trailingColumnCount);
  const headers = columns.map((column) => `<th title="${esc(column.element_id || column.element_code)}">${esc(elementDisplayLabel(column))}</th>`).join("");
  let previousMeasurement = null;
  const rows = facts.map((fact) => {
    const organization = fact.organization || {};
    const newGroup = organization.logical_measurement_id !== previousMeasurement;
    previousMeasurement = organization.logical_measurement_id;
    const groupHeading = newGroup && organization.presentation_count > 1 ? `<tr><th colspan="${columnCount}">同一逻辑测量 · ${organization.presentation_count} 个原始展示 / 披露（不相加）</th></tr>` : "";
    const selected = fact.fact_id === targetedState.selectedFactId;
    const qualityIssues = fact.quality_issues || [];
    const qualityClass = qualityIssues.some((item) => item.severity === "error") || metric.guard_accepted === false
      ? "quality-error"
      : qualityIssues.length ? "quality-warning" : "quality-ok";
    const elementCells = columns.map((column) => {
      const value = factElementDisplayValue(fact, column);
      const cell = factElementCell(fact, column);
      const provenance = provenanceModeLabel(cell?.provenance_mode);
      return `<td class="metric-element-value ${value ? "" : "empty"}" title="${esc([column.element_code, provenance].filter(Boolean).join(" · "))}">${value ? esc(value) : '<span class="empty-mark">—</span>'}</td>`;
    }).join("");
    const location = factEvidenceLocation(fact);
    return `${groupHeading}<tr class="metric-fact-row ${selected ? "selected" : ""} ${qualityClass}" tabindex="0" data-tx-fact-row="${esc(fact.fact_id)}">
      ${elementCells}
      <td class="metric-fact-evidence">${location ? `<strong>${esc(location)}</strong>` : '<span class="empty-mark">—</span>'}<small>${esc(fact.evidence?.[0]?.excerpt || "")}</small></td>
      <td><span class="source-kind ${esc(fact.source_kind || "unknown")}">${esc(sourceKindLabel(fact.source_kind))}</span><small class="provenance-modes">${esc(factProvenanceLabel(fact) || "无字段来源")}</small></td>
      <td class="metric-fact-quality"><strong>${esc(factQualitySummary(metric, fact))}</strong><small>${esc(statusLabel(fact.review_status || metric.review_status))}</small></td>
    </tr>`;
  }).join("");
  const body = rows || `<tr class="metric-fact-empty"><td colspan="${columnCount}">该指标未提取到事实；状态为“${esc(statusLabel(metric.status))}”。</td></tr>`;
  return `<div class="metric-fact-table-wrap"><table class="metric-fact-table"><thead><tr>${headers}<th>证据页</th><th>结构来源</th><th>质量</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function metricProviderAccountBlocked(metric) {
  return (metric.decision?.attempts || []).some((item) =>
    item.provider_error_code === "account_billing_suspended"
    || item.failure_category === "provider_account_blocked"
    || String(item.error || "").includes("account_billing_suspended")
  );
}

function renderTargetMetricTable() {
  const heading = $(".result-level-heading h3");
  const note = $(".result-level-heading > span");
  heading.textContent = resultPerspective === "facts" ? "报告事实" : resultPerspective === "evidence" ? "证据引用" : "标准覆盖";
  note.textContent = resultPerspective === "coverage" ? "每项标准要求分别显示覆盖状态；点击查看具体事实。" : "按后端原始顺序展示，未知身份保留为空。";
  if (resultPerspective !== "coverage") {
    const inspection = targetedState.inspection || {};
    const legacyFacts = (inspection.metrics || []).flatMap((metric) => metric.facts || []);
    const neutralFacts = inspection.report_facts?.length ? inspection.report_facts : inspection.source_facts?.length ? inspection.source_facts : null;
    const facts = neutralFacts || legacyFacts;
    const query = $("#tx-result-search").value.trim();
    const evidence = inspection.evidence_references?.length ? inspection.evidence_references : facts.flatMap((fact) => fact.evidence || fact.evidence_references || fact.physical_sources || []);
    $("#tx-result-table").innerHTML = resultPerspective === "facts" ? reportFactsTable(facts, {query, legacy: !neutralFacts}) : evidenceTable(evidence, query);
    $("#tx-result-detail").innerHTML = "";
    return;
  }
  const query = $("#tx-result-search").value.trim().toLowerCase();
  const filter = $("#tx-result-filter").value;
  const reviewFilter = $("#tx-review-filter").value;
  const metrics = (targetedState.inspection?.metrics || []).filter((metric) => {
    if (filter !== "all" && metric.status !== filter && metric.found_status !== filter) return false;
    if (reviewFilter !== "all" && metric.review_status !== reviewFilter) return false;
    const elementValues = (metric.facts || []).flatMap((fact) => (fact.element_cells || []).flatMap((cell) => [elementLabel(cell), displayMachineValue(cell.value)]));
    const haystack = [metric.source_datapoint_id, metric.metric_id, metricName(metric), metric.description, factSummary(metric, 5000), evidenceLocationSummary(metric), ...elementValues, ...(metric.facts || []).flatMap((fact) => (fact.evidence || []).map((item) => item.excerpt))].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  if (!metrics.some((item) => item.metric_id === targetedState.selectedMetricId)) {
    targetedState.selectedMetricId = metrics[0]?.metric_id || null;
    targetedState.selectedFactId = metrics[0]?.facts?.[0]?.fact_id || null;
  }
  const selectedMetric = metrics.find((item) => item.metric_id === targetedState.selectedMetricId);
  if (selectedMetric && !selectedMetric.facts?.some((fact) => fact.fact_id === targetedState.selectedFactId)) {
    targetedState.selectedFactId = selectedMetric.facts?.[0]?.fact_id || null;
  }
  const rows = metrics.map((metric) => {
    const selected = metric.metric_id === targetedState.selectedMetricId;
    const factCount = metric.facts?.length || 0;
    const evidenceCount = metricEvidenceCount(metric);
    const blocked = metricProviderAccountBlocked(metric);
    const guard = metric.contract_validation_status === "failed"
      ? "结果合同失败"
      : blocked ? "未执行" : metric.guard_accepted === true ? factCount ? "通过" : "任务结构通过（0 事实）" : metric.guard_accepted === false ? "未通过" : "未执行";
    return `<tr class="metric-task-row ${selected ? "selected" : ""} ${metricQualityClass(metric)}" tabindex="0" data-tx-metric="${esc(metric.metric_id)}">
      <td class="metric-task-id"><strong>${esc(metric.source_datapoint_id)}</strong><code>${esc(metric.metric_id)}</code></td>
      <td><strong>${esc(metricName(metric))}</strong><small>${esc(metric.description || "")}</small></td>
      <td><span class="status-pill ${esc(metric.status)}">${esc(blocked ? "服务商账户阻塞" : statusLabel(metric.status))}</span></td>
      <td class="metric-count"><strong>${factCount}</strong><small>行事实</small></td>
      <td class="metric-count"><strong>${evidenceCount}</strong><small>条引用</small></td>
      <td><strong>${guard}</strong><small>${esc(metricVerificationSummary(metric))}</small></td>
      <td>${esc(blocked ? "无模型输出" : statusLabel(metric.review_status))}</td>
      <td><span class="metric-open-hint">查看 ${factCount} 行 →</span></td>
    </tr>`;
  }).join("");
  $("#tx-result-table").innerHTML = rows ? `<table class="metric-task-table"><thead><tr><th>指标编号</th><th>指标名称</th><th>抽取状态</th><th>事实</th><th>证据</th><th>Guard</th><th>审核</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty-state">没有匹配指标。</div>';
  const selectMetric = (row) => {
    const metric = metrics.find((item) => item.metric_id === row.dataset.txMetric);
    targetedState.selectedMetricId = metric?.metric_id || null;
    targetedState.selectedFactId = metric?.facts?.[0]?.fact_id || null;
    renderTargetMetricTable();
  };
  document.querySelectorAll("[data-tx-metric]").forEach((row) => {
    row.addEventListener("click", () => selectMetric(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectMetric(row);
      }
    });
  });
  renderTargetMetricDetail();
}

function renderTargetMetricDetail() {
  const metric = (targetedState.inspection?.metrics || []).find((item) => item.metric_id === targetedState.selectedMetricId);
  if (!metric) { $("#tx-result-detail").innerHTML = ""; return; }
  const facts = metric.facts || [];
  const sufficiency = metric.retrieval?.sufficiency || {};
  const budget = metric.retrieval?.packet_budget || {};
  const attempts = metric.decision?.attempts || [];
  const accountBlocked = metricProviderAccountBlocked(metric);
  const contractFailed = metric.contract_validation_status === "failed";
  const providers = [...new Set(attempts.map((item) => item.provider || item.source_provider).filter(Boolean))];
  const models = [...new Set(attempts.map((item) => item.model_id || item.source_model_id).filter(Boolean))];
  const regionCount = Math.max(0, ...attempts.map((item) => Number(item.region_count || item.region_index || 0)));
  const hits = metric.retrieval?.top_hits || [];
  if (!facts.some((fact) => fact.fact_id === targetedState.selectedFactId)) targetedState.selectedFactId = facts[0]?.fact_id || null;
  const selectedFact = facts.find((fact) => fact.fact_id === targetedState.selectedFactId) || null;
  const evidence = selectedFact?.evidence || [];
  const displayedFacts = facts;
  $("#tx-result-detail").innerHTML = `<div class="inspection-detail-head"><div><p class="eyebrow">${esc(metric.source_datapoint_id)}</p><h3>${esc(metricName(metric))}</h3><p>${esc(metric.description)}</p></div><div class="detail-id-stack"><code>${esc(metric.metric_id)}</code>${metric.task_id ? `<code>${esc(metric.task_id)}</code>` : ""}</div></div>
    <div class="metric-fact-heading"><div><p class="eyebrow">Selected metric facts</p><h4>当前指标的抽取结果</h4><small>${esc(metricSortDescription(metric))}</small></div><div><strong>${facts.length}</strong><span>行事实</span></div></div>
    ${renderMetricFactTable(metric, displayedFacts)}
    ${(metric.unmapped_measurements || []).length ? `<details open><summary>归属待确认 / 其他口径：${metric.unmapped_measurements.length} 条（保留原量，不贴标准标签）</summary><pre class="json compact-json">${esc(JSON.stringify(metric.unmapped_measurements, null, 2))}</pre></details>` : ""}
    <h4>当前事实行证据${selectedFact ? ` · ${esc(selectedFact.fact_id)}` : ""}</h4><div class="evidence-list">${evidence.map((item) => `<blockquote><p>${esc(item.excerpt)}</p><footer>PDF 第 ${item.pdf_page_number} 页${item.printed_page_label ? ` · 报告页 ${esc(item.printed_page_label)}` : ""} · ${esc(item.section_title || item.ir_object_id)} · ${esc(item.evidence_id)}</footer></blockquote>`).join("") || '<p class="muted">当前指标没有可选择的事实行或该行没有接受的证据记录。</p>'}</div>
    <div class="diagnostic-grid"><section><h4>确定性来源与结果合同</h4><div class="guard-summary ${contractFailed || !metric.guard_accepted ? "failed" : "passed"}"><strong>${contractFailed ? "模型结果已保留，但结果物化合同失败" : accountBlocked ? "服务商拒绝请求；未获得模型输出" : metric.guard_accepted === true ? facts.length ? "来源 Guard 与结果合同均通过" : "任务结构检查通过；无业务事实记录" : metric.guard_accepted === false ? "存在确定性来源合同错误" : "Guard 未执行"}</strong><span>${esc(contractFailed ? metric.contract_error || "该指标记录已隔离，未污染其他指标成果。" : accountBlocked ? "七牛账户计费状态异常。账户恢复后重新运行该任务；本次结果不能用于判断条款是否披露。" : (facts.length ? metric.guard?.feedback || "无来源反馈。" : "本项没有可验证的业务事实；来源候选与标准覆盖须分别确认。"))}</span></div><pre class="json compact-json">${esc(JSON.stringify(metric.guard?.issues || [], null, 2))}</pre></section><section><h4>模型实际执行</h4><dl class="compact-facts"><div><dt>Provider</dt><dd>${esc(providers.join(" / ") || (accountBlocked ? "七牛云（请求被拒绝）" : "未调用模型"))}</dd></div><div><dt>模型</dt><dd>${esc(models.join(" / ") || "未记录")}</dd></div><div><dt>Evidence Regions</dt><dd>${regionCount || (attempts.length ? 1 : 0)}</dd></div><div><dt>本地充分性</dt><dd>${esc(sufficiency.status || "未产生")}</dd></div><div><dt>扫描证明</dt><dd>${esc((sufficiency.reason_codes || []).join(" / ") || "未产生")}</dd></div><div><dt>完整选择</dt><dd>${metric.retrieval?.packet_span_count || 0} spans · ${metric.retrieval?.packet_candidate_count || 0} candidates · ${metric.retrieval?.packet_image_count || 0} crops</dd></div><div><dt>模型调用/合并记录</dt><dd>${attempts.length}</dd></div></dl></section></div>
    <h4>Region / 模型调用明细</h4><div class="model-attempt-list">${attempts.map((item) => `<article><strong>${esc(item.provider || item.route || "本地步骤")} · ${esc(item.model_id || "无模型")}</strong><span>${item.region_index ? `Region ${item.region_index}/${item.region_count || "?"} · ` : ""}${Number(item.input_group_count || 0)} groups · ${Number(item.input_span_count || 0)} spans · ${Number(item.input_candidate_count || 0)} candidates · ${Number(item.image_count || 0)} images → ${Number(item.output_row_count || 0)} rows</span><small>${Number(item.input_chars || 0)} chars · prompt ${Number(item.prompt_tokens || 0)} tokens · output ${Number(item.generation_tokens || 0)} tokens · ${Number(item.elapsed_seconds || 0).toFixed(2)}s${item.finish_reason ? ` · ${esc(item.finish_reason)}` : ""}</small></article>`).join("") || '<p class="muted">本指标未调用语义模型。</p>'}</div>
    <h4>召回片段（非对象 Top N）· 前 ${hits.length}</h4><div class="retrieval-hits">${hits.map((hit, index) => `<article><span>#${index + 1}</span><div><strong>${hit.page_index == null ? "页码未记录" : `PDF 第 ${hit.page_index + 1} 页`}${hit.section_title ? ` · ${esc(hit.section_title)}` : ""}</strong><p>${esc(hit.text || "历史产物未保留该命中文本")}</p><small>final ${Number(hit.final_score || 0).toFixed(4)} · topic ${Math.round(Number(hit.topic_coverage || 0) * 100)}% · ${esc((hit.reasons || []).join(" / "))}</small></div></article>`).join("") || '<p class="muted">没有可展示的召回命中。</p>'}</div>
    ${metric.dependency_blocks?.length ? `<section class="error-callout"><h4>派生依赖阻塞</h4><p>直接披露事实保留原有状态；缺少可计算操作数不表示本项未披露。</p><ul>${metric.dependency_blocks.map((block) => `<li><strong>${esc(block.code)}</strong> · ${esc(block.reason)}${block.missing_fields?.length ? `（缺少 ${esc(block.missing_fields.join(" / "))}）` : ""}</li>`).join("")}</ul></section>` : ""}
    <details><summary>完整 Guard、检索和模型决定 JSON</summary><pre class="json compact-json">${esc(JSON.stringify({guard: metric.guard, retrieval: metric.retrieval, decision: metric.decision, dependency_status: metric.dependency_status, dependency_blocks: metric.dependency_blocks}, null, 2))}</pre></details>`;
  document.querySelectorAll("[data-tx-fact-row]").forEach((row) => {
    const selectFact = () => {
      targetedState.selectedFactId = row.dataset.txFactRow;
      renderTargetMetricDetail();
    };
    row.addEventListener("click", selectFact);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectFact();
      }
    });
  });
}

function renderTargetOutcomes() {
  const metrics = targetedState.inspection?.metrics || [];
  $("#tx-tab-outcomes").innerHTML = `<table class="data-table"><thead><tr><th>指标</th><th>状态</th><th>Guard</th><th>尝试</th><th>事实</th></tr></thead><tbody>${metrics.map((item) => `<tr><td>${esc(item.source_datapoint_id)}</td><td>${metricProviderAccountBlocked(item) ? "服务商账户阻塞" : statusLabel(item.status)}</td><td>${metricProviderAccountBlocked(item) ? "未执行" : item.guard_accepted === true ? "通过" : item.guard_accepted === false ? "未通过" : "未执行"}</td><td>${item.attempts}</td><td>${item.facts.length}</td></tr>`).join("")}</tbody></table>`;
}

async function loadTargetAudit(name, signal) {
  const job = targetedState.activeJob;
  if (!job || !["events", "artifacts"].includes(name)) return;
  const items = await txRequest(`/api/jobs/${encodeURIComponent(job.job_id)}/${name}`, {signal});
  if (targetedState.activeJob?.job_id !== job.job_id) return;
  targetedState[name] = items;
  name === "events" ? renderTargetEvents() : renderTargetArtifacts();
}

function renderTargetEvents() {
  $("#tx-tab-events").innerHTML = targetedState.events.map((event) => {
    const detail = event.detail || {};
    const identity = [detail.tool && `工具 ${detail.tool}`, detail.object_id && `对象 ${detail.object_id}`, detail.metric_id && `指标 ${detail.metric_id}`].filter(Boolean).join(" · ");
    return `<article class="event-row ${esc(event.level)}"><time>${new Date(event.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time><strong>${esc(event.stage)}</strong><span>${esc(event.message)}${identity ? `<small>${esc(identity)}</small>` : ""}</span></article>`;
  }).join("") || '<div class="empty-state">没有事件。</div>';
}

function renderTargetArtifacts() {
  $("#tx-artifact-list").innerHTML = targetedState.artifacts.map((item) => `<button type="button" class="artifact-button" data-tx-artifact="${esc(item.path)}">${esc(item.path)}<small>${formatBytes(item.size_bytes)} · ${esc(item.media_type)}</small></button>`).join("") || '<div class="empty-state">尚无中间文件。</div>';
  document.querySelectorAll("[data-tx-artifact]").forEach((button) => button.addEventListener("click", () => previewTargetArtifact(button.dataset.txArtifact)));
}

async function previewTargetArtifact(path) {
  $("#tx-preview-title").textContent = path;
  const download = $("#tx-download-artifact");
  download.href = `${targetedState.backend}/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/download?path=${encodeURIComponent(path)}`;
  download.classList.remove("hidden");
  const artifact = targetedState.artifacts.find((item) => item.path === path);
  if (!artifact || !/(json|jsonl|csv|text|markdown)/.test(artifact.media_type)) {
    $("#tx-artifact-preview").textContent = "二进制文件请使用下载。";
    return;
  }
  const raw = await txRequest(`/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/artifact?path=${encodeURIComponent(path)}`, {responseType: "text"});
  try { $("#tx-artifact-preview").textContent = JSON.stringify(JSON.parse(raw), null, 2); }
  catch (_) { $("#tx-artifact-preview").textContent = raw; }
}

async function resumeTargetJob() {
  const job = targetedState.activeJob;
  if (!job) return;
  try {
    const cloudKey = job.request.semantic_provider === "qiniu_vlm"
      ? getSecret("qiniu_api_key")
      : null;
    const task = await docRequest(`/api/pipeline/tasks/targeted-extraction/${encodeURIComponent(job.job_id)}/resume`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({qiniu_api_key: cloudKey}),
    });
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: task.task_id}}));
  } catch (error) {
    window.alert(`续跑加入失败：${error.message}`);
  }
}

async function cancelTargetJob() {
  const job = targetedState.activeJob;
  if (!job || !window.confirm(`确认终止 ${job.job_id} 吗？`)) return;
  try {
    const tasks = await docRequest("/api/pipeline/tasks?limit=1000");
    const pipelineTask = tasks.find((item) => item.native_job_id === job.job_id && ["queued", "running"].includes(item.status));
    if (pipelineTask) {
      await docRequest(`/api/pipeline/tasks/${encodeURIComponent(pipelineTask.task_id)}/cancel`, {method: "POST"});
      window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: pipelineTask.task_id}}));
    } else {
      await txRequest(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, {method: "POST"});
    }
    await refreshTargetJobs();
  } catch (error) {
    window.alert(`终止失败：${error.message}`);
  }
}

async function deleteTargetJob() {
  const job = targetedState.activeJob;
  const action = job?.lifecycle?.available_actions?.find((item) => ["archive", "open_parent"].includes(item.action));
  if (!job || !action) return;
  if (action.action === "open_parent") {
    window.dispatchEvent(new CustomEvent("esg:select-session", {detail: {sessionId: job.request.report_session_id}}));
    window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "extract-tasks"}}));
    return;
  }
  const reason = window.prompt("归档后从日常列表隐藏，已有成果与来源引用保留。请填写原因。", "整理已结束的提取记录");
  if (!reason?.trim()) return;
  try {
    await txRequest(action.endpoint, {method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({reason:reason.trim(), expected_revision:job.lifecycle.revision})});
    targetedState.activeJob = null;
    targetedState.inspection = null;
    $("#tx-job-title").textContent = "选择一个任务";
    $("#tx-result-document").className = "result-document-banner hidden";
    $("#tx-result-document").innerHTML = "";
    $("#tx-job-summary").className = "job-summary empty-state";
    $("#tx-job-summary").textContent = "选择任务后查看成果、证据和中间文件。";
    $("#tx-agent-progress").innerHTML = "";
    await refreshTargetJobs();
  } catch (error) {
    window.alert(`归档失败：${error.message}`);
  }
}

function bindTargetedEvents() {
  $("#tx-backend-url").value = targetedState.backend;
  $("#tx-reconnect").addEventListener("click", () => connectTargeted({allowDiscovery: false}));
  document.querySelectorAll("[data-result-perspective]").forEach((button) => button.addEventListener("click", () => {
    resultPerspective = button.dataset.resultPerspective;
    document.querySelectorAll("[data-result-perspective]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-selected", String(item === button)); });
    renderTargetMetricTable();
  }));
  window.addEventListener("esg:open-result", (event) => selectTargetJob(event.detail.jobId).catch((error) => { $("#tx-job-list").innerHTML = `<div class="empty-state error">${esc(error.message)}</div>`; }));
  $("#tx-refresh-jobs").addEventListener("click", refreshTargetJobs);
  $("#tx-result-search").addEventListener("input", renderTargetMetricTable);
  $("#tx-result-filter").addEventListener("change", renderTargetMetricTable);
  $("#tx-review-filter").addEventListener("change", renderTargetMetricTable);
  $("#tx-resume-job").addEventListener("click", resumeTargetJob);
  $("#tx-cancel-job").addEventListener("click", cancelTargetJob);
  $("#tx-delete-job").addEventListener("click", deleteTargetJob);
  $("#semantic-index-refresh")?.addEventListener("click", refreshSemanticIndexes);
  document.querySelectorAll("[data-tx-tab]").forEach((button) => button.addEventListener("click", async () => {
    activeAuditTab = button.dataset.txTab;
    await loadTargetAudit(activeAuditTab).catch((error) => { $(`#tx-tab-${activeAuditTab}`).innerHTML = `<div class="empty-state error">${esc(error.message)}</div>`; });
    document.querySelectorAll("[data-tx-tab]").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll("#tx-result-tabs ~ .tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tx-tab-${button.dataset.txTab}`));
  }));
  window.addEventListener("esg:viewchange", (event) => {
    resultViewActive = event.detail?.view === "results";
    unsubscribe("targeted-results");
    if (resultViewActive) {
      connectTargeted({allowDiscovery: true});
      subscribe("targeted-results", refreshTargetJobs, {interval: () => targetedState.jobs.some((job) => ["queued", "running"].includes(job.status)) ? 2500 : 15000, immediate: false});
    }
    if (event.detail?.view === "settings") connectTargeted({allowDiscovery: true});
  });
}

bindTargetedEvents();
resultViewActive = document.body.dataset.activeView === "results";
if (resultViewActive) {
  connectTargeted({allowDiscovery: true});
  subscribe("targeted-results", refreshTargetJobs, {interval: () => targetedState.jobs.some((job) => ["queued", "running"].includes(job.status)) ? 2500 : 15000, immediate: false});
}
