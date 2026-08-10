const $ = (selector) => document.querySelector(selector);

const pipelineDefinitions = [
  ["01", "PaddleOCR 主解析", "Markdown + Raw JSONL"],
  ["02", "页图与本地取证", "Render + PDF cross-check"],
  ["03", "Layout 转换", "Raw objects + coordinates"],
  ["04", "确定性融合", "Dedupe + local observations"],
  ["05", "结构与视觉区域", "Sections + tables + crops"],
  ["06", "左右跨页建模", "Seam detection + composite"],
  ["07", "分层质量路由", "Typed question · scoped risks"],
  ["08", "分类与修复 Agent", "Classify · correct · abstain"],
  ["09", "Patch Guard", "Atomic candidate + invariants"],
  ["10", "独立 Verifier", "Different model family"],
  ["11", "验证与版本冻结", "Readiness gate"],
];

let activeRunId = null;
let activeIrRunId = null;
let irDocument = null;
let irArtifacts = [];
let currentManifest = null;
let reviewState = {};
let activeReviewGroup = "human_required";
let selectedReviewTaskId = null;
let pollTimer = null;
let irPollTimer = null;
let runtimeClockTimer = null;
let lastRuntimeState = null;
let backendContextUrl = "";
let reviewRetryInFlight = false;

const artifactGroupLabels = {
  manifest: "00 · Package 入口",
  source: "01 · 输入请求",
  provider: "02 · OCR供应商原始响应",
  content: "03 · 可读内容",
  observations: "04 · 解析观察",
  canonical: "05 · 正式Document IR",
  artifacts: "06 · 页图、拼接图与区域图",
  quality: "07 · 质量与准入",
  review: "08 · Agent复核审计",
  integrity: "09 · 文件完整性",
  exports: "10 · 兼容导出",
  other: "99 · 其他文件",
};

function backendUrl() {
  return $("#backend-url").value.replace(/\/$/, "");
}

function endpoint(path) {
  return `${backendUrl()}${path}`;
}

function syncBackendContext() {
  const next = backendUrl();
  if (next === backendContextUrl) return false;
  backendContextUrl = next;
  clearTimeout(pollTimer);
  clearTimeout(irPollTimer);
  clearInterval(runtimeClockTimer);
  activeRunId = null;
  activeIrRunId = null;
  irDocument = null;
  irArtifacts = [];
  currentManifest = null;
  reviewState = {};
  activeReviewGroup = "human_required";
  selectedReviewTaskId = null;
  setReviewRetryInFlight(false);
  $("#current-run").textContent = "尚未启动";
  $("#current-ir-run").textContent = "尚未启动";
  $("#logs").textContent = "";
  $("#ir-logs").textContent = "";
  $("#artifacts").replaceChildren();
  $("#ir-artifacts").replaceChildren();
  $("#ir-workbench").classList.add("hidden");
  renderRuntimeTelemetry(null);
  renderPipeline();
  return true;
}

function resetIrView() {
  clearTimeout(irPollTimer);
  activeIrRunId = null;
  irDocument = null;
  irArtifacts = [];
  currentManifest = null;
  reviewState = {};
  activeReviewGroup = "human_required";
  selectedReviewTaskId = null;
  setReviewRetryInFlight(false);
  $("#current-ir-run").textContent = "尚未启动";
  $("#ir-logs").textContent = "";
  $("#ir-artifacts").replaceChildren();
  $("#ir-workbench").classList.add("hidden");
  renderRuntimeTelemetry(null);
  renderPipeline();
}

function durationSeconds(startedAt, finishedAt = null) {
  const start = Date.parse(startedAt || "");
  const finish = finishedAt ? Date.parse(finishedAt) : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(finish)) return 0;
  return Math.max(0, Math.floor((finish - start) / 1000));
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  return [hours, minutes, secs].map((item) => String(item).padStart(2, "0")).join(":");
}

function renderRuntimeTelemetry(state) {
  lastRuntimeState = state;
  clearInterval(runtimeClockTimer);
  const telemetry = state?.telemetry;
  const metricsRoot = $("#runtime-metrics");
  const stagesRoot = $("#runtime-stages");
  const currentRoot = $("#runtime-current");
  metricsRoot.replaceChildren();
  stagesRoot.replaceChildren();
  currentRoot.replaceChildren();
  if (!telemetry?.started_at) {
    $("#runtime-updated").textContent = "等待 Document IR 任务";
    currentRoot.appendChild(documentNode("span", "muted", "旧版本运行或尚未启动的任务没有实时遥测。"));
    return;
  }

  const render = () => {
    const live = lastRuntimeState?.telemetry;
    if (!live) return;
    const calls = live.model_calls || {};
    const totalSeconds = durationSeconds(live.started_at, live.finished_at);
    const stage = live.current_stage || {};
    const stageSeconds = durationSeconds(stage.started_at, stage.finished_at);
    const values = [
      [formatDuration(totalSeconds), "总耗时"],
      [formatDuration(stageSeconds), "当前阶段耗时"],
      [calls.logical_started || 0, "逻辑模型调用"],
      [calls.actual_attempts || 0, "真实 HTTP 请求"],
      [calls.retries || 0, "同模型重试"],
      [calls.succeeded || 0, "成功响应"],
      [calls.invalid_response || 0, "协议无效响应"],
    ];
    metricsRoot.replaceChildren();
    values.forEach(([value, label]) => {
      const node = documentNode("div", "runtime-metric");
      node.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
      metricsRoot.appendChild(node);
    });
    const current = calls.current_call;
    currentRoot.replaceChildren();
    currentRoot.append(
      documentNode("strong", "", stage.name ? `${stage.index}/${stage.total} · ${stage.name}` : "等待阶段更新"),
      documentNode("span", "", current
        ? `${current.task_id} · ${current.role} · round ${current.round_index} · retry ${current.retry_index} · ${current.model_id}`
        : live.status === "running" ? "当前处于本地处理或两次模型请求之间。" : `运行已${live.status === "done" ? "完成" : "结束"}。`),
    );
    $("#runtime-updated").textContent = `${live.status} · 更新 ${new Date(live.updated_at).toLocaleTimeString("zh-CN", {hour12: false})}`;
  };

  (telemetry.stages || []).forEach((stage) => {
    const node = documentNode("div", `runtime-stage ${stage.status || ""}`);
    node.append(
      documentNode("strong", "", `${stage.index}. ${stage.name}`),
      documentNode("span", "", formatDuration(durationSeconds(stage.started_at, stage.finished_at))),
    );
    stagesRoot.appendChild(node);
  });
  render();
  if (state.status === "running") runtimeClockTimer = setInterval(render, 1000);
}

async function fetchJson(path, fallback = null) {
  try {
    const response = await fetch(endpoint(path));
    if (!response.ok) return fallback;
    return await response.json();
  } catch {
    return fallback;
  }
}

async function postJson(path, payload) {
  const response = await fetch(endpoint(path), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || text || `HTTP ${response.status}`;
    const error = new Error(message);
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return body;
}

function setJson(element, payload) {
  element.textContent = payload == null ? "" : JSON.stringify(payload, null, 2);
}

function renderLogs(element, lines = []) {
  element.textContent = lines.join("\n");
  element.scrollTop = element.scrollHeight;
}

function renderPipeline(progress = -1, finalState = null) {
  const root = $("#pipeline-stages");
  root.replaceChildren();
  pipelineDefinitions.forEach(([number, title, description], index) => {
    const item = document.createElement("div");
    item.className = "pipeline-stage";
    if (index < progress) item.classList.add("done");
    if (index === progress) item.classList.add("active");
    if (finalState && index === pipelineDefinitions.length - 1) {
      item.classList.remove("active", "done");
      item.classList.add(["ready", "ready_with_warnings"].includes(finalState) ? "done" : "warning");
    }
    const no = document.createElement("div"); no.className = "number"; no.textContent = number;
    const strong = document.createElement("strong"); strong.textContent = title;
    const span = document.createElement("span"); span.textContent = description;
    item.append(no, strong, span);
    root.appendChild(item);
  });
  $("#pipeline-state").textContent = finalState ? `IR ${finalState.toUpperCase()}` : progress >= 0 ? `正在执行第 ${progress + 1} 层` : "等待任务";
}

function progressFromLogs(logs) {
  const last = [...logs].reverse().find((line) => /^\d+\/(?:10|11)\b/.test(line));
  if (!last) return activeRunId ? 0 : -1;
  const step = Number(last.split("/")[0]);
  return Math.min(pipelineDefinitions.length - 1, Math.max(1, step));
}

async function checkHealth() {
  const payload = await fetchJson("/health");
  const health = $("#health");
  health.classList.toggle("online", Boolean(payload));
  health.classList.toggle("offline", !payload);
  health.textContent = payload ? `Backend: online (${payload.service})` : "Backend: offline";
}

async function refreshModelStatus() {
  const requestBackend = backendUrl();
  const payload = await fetchJson("/api/models/status", {});
  if (requestBackend !== backendUrl()) return;
  const root = $("#model-status"); root.replaceChildren();
  if (payload.catalog_error) root.appendChild(documentNode("div", "model-health error", `模型目录暂不可用：${payload.catalog_error}`));
  const providerBlock = payload.provider_rate_limit?.account_block;
  if (providerBlock) {
    root.appendChild(documentNode(
      "div",
      "model-health error",
      `七牛账号 TPD 熔断中 · 约 ${formatDuration(providerBlock.retry_after_seconds || 0)} 后恢复；自动重试会在发起前被拦截`,
    ));
  }
  (payload.approved_vision_models || []).forEach((modelId) => {
    const health = (payload.health || []).find((item) => item.model_id === modelId);
    const card = documentNode("div", `model-health ${health?.last_status || "unknown"}`);
    card.append(
      documentNode("strong", "", modelId),
      documentNode(
        "span",
        "",
        health
          ? `${health.last_status} · 成功 ${health.successes} · 网络 ${health.transport_failures || 0} · RPM ${health.rpm_failures || 0} · 协议 ${health.protocol_failures || 0} · TPD ${health.quota_failures || 0}`
          : "approved · 当前进程尚未调用",
      ),
    );
    root.appendChild(card);
  });
  (payload.retired_models_still_listed || []).forEach((modelId) => {
    const card = documentNode("div", "model-health retired");
    card.append(documentNode("strong", "", modelId), documentNode("span", "", "七牛目录仍列出，但系统已禁用"));
    root.appendChild(card);
  });
  if (!root.children.length) root.appendChild(documentNode("span", "muted", "未发现已批准且可用的视觉模型。"));
}

async function refreshHistories() {
  syncBackendContext();
  const requestBackend = backendUrl();
  await checkHealth();
  const [ocrRuns, irRuns] = await Promise.all([
    fetchJson("/api/ocr/jobs", []),
    fetchJson("/api/document-ir/jobs", []),
  ]);
  if (requestBackend !== backendUrl()) return;
  populateHistory($("#ocr-history"), ocrRuns, "选择 OCR Run", "run_id", (item) => `${item.run_id} · ${item.status}`);
  populateHistory($("#ir-history"), irRuns, "选择 IR Run", "run_id", (item) => `${item.run_id} · ${item.summary?.readiness || item.status}`);
  if (activeIrRunId && !irRuns.some((item) => item.run_id === activeIrRunId)) {
    resetIrView();
  }
  if (!activeIrRunId && irRuns.length) {
    const latest = [...irRuns].sort((a, b) => Number(b.summary?.ir_revision || 0) - Number(a.summary?.ir_revision || 0))[0];
    await loadIrRun(latest.run_id);
  }
}

function populateHistory(select, items, placeholder, key, labeler) {
  const selected = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  const ordered = [...items].sort((a, b) => {
    const revisionDelta = Number(b.summary?.ir_revision || 0) - Number(a.summary?.ir_revision || 0);
    return revisionDelta || String(b[key]).localeCompare(String(a[key]));
  });
  ordered.forEach((item) => select.add(new Option(labeler(item), item[key])));
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
}

$("#ocr-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  const file = $("#pdf-file").files[0];
  const fileUrl = $("#file-url").value.trim();
  if (Boolean(file) === Boolean(fileUrl)) return alert("本地 PDF 和 PDF URL 必须二选一。 ");
  button.disabled = true;
  const data = new FormData();
  if (file) data.append("file", file);
  if (fileUrl) data.append("file_url", fileUrl);
  if ($("#token").value.trim()) data.append("token", $("#token").value.trim());
  data.append("model", $("#model").value.trim() || "PaddleOCR-VL-1.6");
  data.append("useDocOrientationClassify", $("#use-orientation").checked);
  data.append("useDocUnwarping", $("#use-unwarping").checked);
  data.append("useChartRecognition", $("#use-chart").checked);
  try {
    const response = await fetch(endpoint("/api/ocr/jobs"), {method: "POST", body: data});
    if (!response.ok) throw new Error(await response.text());
    const state = await response.json();
    activeRunId = state.run_id;
    $("#ocr-run-id").value = activeRunId;
    $("#current-run").textContent = `${activeRunId} · ${state.status}`;
    renderLogs($("#logs"), [state.message]);
    renderPipeline(0);
    pollOcrState();
  } catch (error) {
    renderLogs($("#logs"), [`启动失败: ${error.message}`]);
    button.disabled = false;
  }
});

$("#ir-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  const ocrRunId = $("#ocr-run-id").value.trim();
  if (!ocrRunId) return alert("请先填写 OCR Run ID 或选择一个 OCR 运行。 ");
  button.disabled = true;
  const payload = {
    ocr_run_id: ocrRunId,
    pdf_path: $("#pdf-path").value.trim() || null,
    parent_ir_run_id: $("#parent-ir-run").value || null,
    render_dpi: Number($("#render-dpi").value || 144),
    execute_vlm_reviews: $("#execute-vlm").checked,
    qiniu_api_key: $("#qiniu-key").value.trim() || null,
  };
  try {
    const response = await fetch(endpoint("/api/document-ir/jobs"), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await response.text());
    const state = await response.json();
    activeIrRunId = state.run_id;
    $("#current-ir-run").textContent = `${activeIrRunId} · ${state.status}`;
    renderLogs($("#ir-logs"), [state.message]);
    renderRuntimeTelemetry(state);
    renderPipeline(1);
    pollIrState();
  } catch (error) {
    renderLogs($("#ir-logs"), [`启动失败: ${error.message}`]);
    button.disabled = false;
  }
});

async function pollOcrState() {
  clearTimeout(pollTimer);
  if (!activeRunId) return;
  const runId = activeRunId;
  const requestBackend = backendUrl();
  const state = await fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}`);
  if (requestBackend !== backendUrl() || runId !== activeRunId) return;
  if (!state) return;
  renderLogs($("#logs"), state.logs || []);
  $("#current-run").textContent = `${state.run_id} · ${state.status}`;
  if (state.status === "done") {
    $("#ocr-form button[type=submit]").disabled = false;
    $("#ocr-run-id").value = state.run_id;
    renderPipeline(1);
    await loadOcrRun(state.run_id);
    await refreshHistories();
    return;
  }
  if (state.status === "failed") {
    $("#ocr-form button[type=submit]").disabled = false;
    renderLogs($("#logs"), [...(state.logs || []), `失败: ${state.error || state.message}`]);
    return;
  }
  pollTimer = setTimeout(pollOcrState, 1800);
}

async function pollIrState() {
  clearTimeout(irPollTimer);
  if (!activeIrRunId) return;
  const runId = activeIrRunId;
  const requestBackend = backendUrl();
  const state = await fetchJson(`/api/document-ir/jobs/${encodeURIComponent(runId)}`);
  if (requestBackend !== backendUrl() || runId !== activeIrRunId) return;
  if (!state) {
    irPollTimer = setTimeout(pollIrState, 1200);
    return;
  }
  renderLogs($("#ir-logs"), state.logs || []);
  $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
  renderPipeline(progressFromLogs(state.logs || []));
  renderRuntimeTelemetry(state);
  if (state.status === "done") {
    $("#ir-form button[type=submit]").disabled = false;
    setReviewRetryInFlight(false);
    await loadIrRun(state.run_id);
    await refreshHistories();
    return;
  }
  if (state.status === "failed") {
    $("#ir-form button[type=submit]").disabled = false;
    setReviewRetryInFlight(false);
    renderLogs($("#ir-logs"), [...(state.logs || []), `失败: ${state.error || state.message}`]);
    return;
  }
  irPollTimer = setTimeout(pollIrState, 900);
}

async function loadOcrRun(runId) {
  const requestBackend = backendUrl();
  activeRunId = runId;
  $("#ocr-history").value = runId;
  $("#ocr-run-id").value = runId;
  const [state, manifest, artifacts] = await Promise.all([
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}`),
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}/manifest`),
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}/artifacts`, {files: []}),
  ]);
  if (requestBackend !== backendUrl()) return;
  if (state) {
    $("#current-run").textContent = `${state.run_id} · ${state.status}`;
    renderLogs($("#logs"), state.logs || []);
  }
  setJson($("#manifest"), manifest);
  if (typeof manifest?.source === "string" && !manifest.source.startsWith("http")) {
    $("#pdf-path").value = manifest.source;
  } else if (manifest?.package_schema_version === "ocr-package-v1") {
    $("#pdf-path").value = "";
    $("#pdf-path").placeholder = "上传副本由后端按OCR Run自动推断";
  }
  renderArtifactLinks($("#artifacts"), artifacts.files || [], "ocr");
  await loadRevisionOptions(runId);
}

async function loadRevisionOptions(ocrRunId) {
  const requestBackend = backendUrl();
  const revisions = await fetchJson(`/api/document-ir/revisions/${encodeURIComponent(ocrRunId)}`, []);
  if (requestBackend !== backendUrl()) return;
  const select = $("#parent-ir-run");
  select.replaceChildren(new Option("新建独立 revision", ""));
  revisions.forEach((item) => select.add(new Option(`r${item.ir_revision} · ${item.run_id} · ${item.readiness}`, item.run_id)));
}

async function loadIrRun(runId) {
  const requestBackend = backendUrl();
  activeIrRunId = runId;
  $("#ir-history").value = runId;
  const base = `/api/document-ir/jobs/${encodeURIComponent(runId)}`;
  const [state, manifest, quality, validation, document, tasks, patches, conflicts, artifacts,
    modelCalls, reviewerResults, guards, verifierResults, decisions, candidates, inbox] = await Promise.all([
    fetchJson(base), fetchJson(`${base}/manifest`), fetchJson(`${base}/quality-report`, {}),
    fetchJson(`${base}/validation-report`, {}), fetchJson(`${base}/document`),
    fetchJson(`${base}/review-tasks`, []), fetchJson(`${base}/atomic-patches`, []),
    fetchJson(`${base}/conflict-groups`, []), fetchJson(`${base}/artifacts`, {files: []}),
    fetchJson(`${base}/model-calls`, []), fetchJson(`${base}/reviewer-results`, []),
    fetchJson(`${base}/guard-results`, []), fetchJson(`${base}/verifier-results`, []),
    fetchJson(`${base}/final-decisions`, []), fetchJson(`${base}/candidate-revisions`, []),
    fetchJson(`${base}/human-review/inbox`, {
      counts: {human_required: 0, system_blocked: 0, blocking_deferred: 0, optional_deferred: 0, resolved: 0},
      groups: {human_required: [], system_blocked: [], blocking_deferred: [], optional_deferred: [], resolved: []},
    }),
  ]);
  if (requestBackend !== backendUrl()) return;
  if (state) {
    $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
    renderLogs($("#ir-logs"), state.logs || []);
    renderRuntimeTelemetry(state);
    setReviewRetryInFlight(["queued", "running"].includes(state.status));
  }
  currentManifest = manifest;
  irDocument = document;
  irArtifacts = artifacts.files || [];
  reviewState = {tasks, patches, conflicts, modelCalls, reviewerResults, guards, verifierResults, decisions, candidates, inbox};
  setJson($("#ir-manifest"), manifest);
  setJson($("#quality-report"), quality);
  renderReviewRetryOutcome(quality?.review_retry_result || manifest?.review_retry_result || state?.summary?.review_retry_result);
  renderPipeline(pipelineDefinitions.length - 1, manifest?.readiness);
  if (!document) {
    $("#ir-workbench").classList.add("hidden");
    return;
  }
  $("#ir-workbench").classList.remove("hidden");
  renderOverview(manifest, validation, document);
  renderPageOptions(document.pages || []);
  renderTableOptions(document.tables || []);
  renderFigures(document.figures || []);
  renderSpreadRouting(document.spreads || []);
  renderReviewList($("#correction-patches"), patches, "尚无原子修正补丁。", (item) => [item.patch_id, `${item.operation} · risk ${item.risk_level}`, `${item.status} · ${item.target_id}`, item.rationale || ""], (item) => showPatch(item));
  renderReviewList($("#conflict-groups"), conflicts, "当前没有冲突组。", (item) => [item.conflict_id, item.conflict_type, `${item.status} · ${item.routing_disposition || "unrouted"} · ${item.target_id}`, item.resolution || "未裁决"]);
  renderReviewSummary(reviewState);
  activeReviewGroup = firstAvailableReviewGroup(inbox);
  renderReviewInbox(activeReviewGroup);
  populateHumanPatches(patches);
  renderArtifactLinks($("#ir-artifacts"), irArtifacts, "ir");
  await renderRevisions(manifest?.ocr_run_id);
}

function renderOverview(manifest, validation, document) {
  const readiness = manifest?.readiness || document.readiness || "unknown";
  const badge = $("#readiness-badge");
  badge.className = `readiness ${readiness}`;
  badge.textContent = `${readiness.toUpperCase()} · r${manifest?.ir_revision || 1}`;
  const metrics = [
    [manifest?.page_count ?? document.pages?.length ?? 0, "Pages"],
    [manifest?.layout_object_count ?? document.layout_objects?.length ?? 0, "Raw layout"],
    [manifest?.block_count ?? document.blocks?.length ?? 0, "Blocks"],
    [manifest?.table_count ?? document.tables?.length ?? 0, "Tables"],
    [manifest?.logical_table_count ?? document.logical_tables?.length ?? 0, "Logical tables"],
    [manifest?.figure_count ?? document.figures?.length ?? 0, "Figures"],
    [manifest?.spread_count ?? document.spreads?.length ?? 0, "Spreads"],
    [manifest?.structure_edge_count ?? document.structure_edges?.length ?? 0, "Relations"],
  ];
  const grid = $("#metric-grid"); grid.replaceChildren();
  metrics.forEach(([value, label]) => {
    const box = documentNode("div", "metric");
    box.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    grid.appendChild(box);
  });
  renderValidation(validation || document.validation_report || {});
}

function renderValidation(validation) {
  const root = $("#validation-view"); root.replaceChildren();
  const coverageLabels = {
    page_image_coverage: "页图覆盖率", layout_geometry_coverage: "Layout 坐标覆盖率",
    block_geometry_coverage: "Block 坐标覆盖率", table_geometry_coverage: "表格坐标覆盖率",
    table_cell_geometry_coverage: "表格 Cell 坐标覆盖率",
    figure_geometry_coverage: "视觉区域坐标覆盖率",
  };
  Object.entries(coverageLabels).forEach(([key, label]) => {
    const value = Number(validation.metrics?.[key] ?? 0);
    const row = documentNode("div", "coverage-row");
    const bar = documentNode("div", "bar");
    const fill = document.createElement("i"); fill.style.width = `${Math.max(0, Math.min(1, value)) * 100}%`; bar.appendChild(fill);
    row.append(documentNode("span", "", label), bar, documentNode("strong", "", `${Math.round(value * 100)}%`));
    root.appendChild(row);
  });
  (validation.issues || []).forEach((issue) => {
    root.appendChild(documentNode("div", `issue ${issue.severity}`, `${issue.severity.toUpperCase()} · ${issue.code}: ${issue.message}`));
  });
}

function renderPageOptions(pages) {
  const select = $("#page-select"); select.replaceChildren();
  pages.forEach((page) => select.add(new Option(`Page ${page.page_number} · ${page.layout_object_ids?.length || 0} layout objects`, page.page_index)));
  if (pages.length) renderPage(Number(select.value));
}

function renderPage(pageIndex) {
  const page = irDocument.pages.find((item) => item.page_index === pageIndex);
  if (!page) return;
  const objects = (irDocument.layout_objects || []).filter((item) => item.page_index === pageIndex);
  const fallbackBlocks = (irDocument.blocks || []).filter((item) => item.page_index === pageIndex);
  const visibleObjects = objects.length ? objects : fallbackBlocks.map((item) => ({...item, layout_object_id: item.block_id, label: item.block_type}));
  const canvas = $("#page-canvas"); canvas.replaceChildren();
  if (page.page_image_path) {
    const image = document.createElement("img");
    image.alt = `Rendered page ${page.page_number}`;
    image.src = pathToArtifactUrl(page.page_image_path);
    canvas.appendChild(image);
    visibleObjects.filter((item) => item.bbox).forEach((item) => canvas.appendChild(createOverlay(item, page)));
  } else {
    canvas.appendChild(documentNode("div", "empty-state", "该 IR 没有页图；请提供原 PDF 重新构建 revision。"));
  }
  const meta = $("#page-meta"); meta.replaceChildren();
  [`physical ${page.page_number}`, `printed ${page.printed_page_label || "?"}`, `${page.width || "?"} × ${page.height || "?"}`, `${visibleObjects.length} objects`, ...(page.quality_flags || [])].forEach((text) => meta.appendChild(documentNode("span", "chip", text)));
  const list = $("#layout-list"); list.replaceChildren();
  visibleObjects.forEach((item) => {
    const node = documentNode("div", "layout-item"); node.dataset.objectId = objectId(item);
    node.append(documentNode("strong", "", `${item.order ?? "-"} · ${item.label || item.block_type}`), documentNode("span", "", item.text || "[no text]"));
    node.addEventListener("click", () => selectObject(item));
    list.appendChild(node);
  });
  $("#object-detail").textContent = JSON.stringify({page, coordinate_systems: (irDocument.coordinate_systems || []).filter((item) => item.page_index === pageIndex)}, null, 2);
}

function createOverlay(item, page) {
  const bbox = item.bbox;
  const node = documentNode("button", "bbox-overlay");
  node.type = "button";
  node.dataset.objectId = objectId(item);
  node.dataset.label = String(item.label || item.block_type || "unknown").toLowerCase();
  node.title = `${item.label || item.block_type}: ${(item.text || "").slice(0, 140)}`;
  node.style.left = `${(bbox.x0 / page.width) * 100}%`;
  node.style.top = `${(bbox.y0 / page.height) * 100}%`;
  node.style.width = `${((bbox.x1 - bbox.x0) / page.width) * 100}%`;
  node.style.height = `${((bbox.y1 - bbox.y0) / page.height) * 100}%`;
  const tag = documentNode("span", "bbox-tag", item.label || item.block_type || "object");
  node.appendChild(tag);
  node.addEventListener("click", () => selectObject(item));
  return node;
}

function selectObject(item) {
  const id = objectId(item);
  document.querySelectorAll("[data-object-id]").forEach((node) => node.classList.toggle("selected", node.dataset.objectId === id));
  $("#object-detail").textContent = JSON.stringify(item, null, 2);
}

function objectId(item) {
  return item.layout_object_id || item.block_id || item.table_id || item.figure_id || "unknown";
}

function renderTableOptions(tables) {
  const select = $("#table-select"); select.replaceChildren();
  if (!tables.length) {
    select.add(new Option("没有表格", ""));
    $("#table-grid").textContent = "该报告未生成表格对象。";
    $("#logical-table-view").replaceChildren();
    return;
  }
  tables.forEach((table) => select.add(new Option(`${table.table_id} · ${table.row_count}×${table.column_count}`, table.table_id)));
  renderTable(select.value);
}

function renderSpreadRouting(spreads) {
  const summary = $("#spread-routing-summary");
  const root = $("#spread-routing-list");
  summary.replaceChildren();
  root.replaceChildren();
  const counts = spreads.reduce((result, spread) => {
    const key = spread.classification || "legacy_unknown";
    result[key] = (result[key] || 0) + 1;
    return result;
  }, {});
  const labels = {
    content_crossing: "信息跨缝",
    visual_continuity: "纯视觉连续",
    standalone_pages: "独立页面",
    uncertain: "不确定",
    legacy_unknown: "旧版本未分类",
  };
  Object.entries(counts).forEach(([key, count]) => {
    summary.appendChild(documentNode("span", "chip", `${labels[key] || key} ${count}`));
  });
  if (!spreads.length) {
    root.appendChild(documentNode("div", "empty-state", "本报告没有左右跨页候选。"));
    return;
  }
  spreads.forEach((spread) => {
    const classification = spread.classification || "legacy_unknown";
    const card = documentNode(
      "article",
      `spread-route-card ${spread.requires_detailed_review ? "review" : ""} ${classification === "content_crossing" ? "content" : ""}`,
    );
    card.append(
      documentNode("strong", "", `${spread.spread_id} · ${labels[classification] || classification}`),
      documentNode("span", "", `physical pages ${(spread.page_indices || []).map((value) => value + 1).join(" + ")} · ${spread.status}`),
      documentNode("span", "", spread.requires_detailed_review
        ? `进入模型详细复核 · content dependency ${Boolean(spread.content_dependency)}`
        : "本地已终结 · 不占用视觉模型"),
    );
    const signals = document.createElement("ul");
    signals.className = "spread-signal-list";
    (spread.preflight_signals || []).slice(0, 4).forEach((signal) => {
      const item = document.createElement("li"); item.textContent = signal; signals.appendChild(item);
    });
    card.appendChild(signals);
    const artifact = artifactById(spread.composite_artifact_id);
    if (artifact) {
      const image = document.createElement("img");
      image.loading = "lazy";
      image.src = pathToArtifactUrl(artifact.path);
      image.alt = `${spread.spread_id} composite`;
      card.appendChild(image);
    }
    root.appendChild(card);
  });
}

function renderTable(tableId) {
  const table = irDocument.tables.find((item) => item.table_id === tableId);
  if (!table) return;
  const meta = $("#table-meta"); meta.replaceChildren();
  const logical = (irDocument.logical_tables || []).find((item) => (item.source_table_ids || []).includes(table.table_id));
  [`page ${table.page_index + 1}`, `${table.row_count} rows`, `${table.column_count} columns`, `${table.graph_edges?.length || 0} graph edges`, table.continuation_group_id || "single page", ...(logical ? [`${logical.logical_table_id} · ${logical.composition_axis} · ${logical.logical_row_count}×${logical.logical_column_count}`] : []), ...(table.quality_flags || [])].forEach((text) => meta.appendChild(documentNode("span", "chip", text)));
  const grid = $("#table-grid"); grid.replaceChildren();
  const htmlTable = document.createElement("table");
  for (let rowIndex = 0; rowIndex < table.row_count; rowIndex += 1) {
    const row = document.createElement("tr");
    table.cells.filter((cell) => cell.row_index === rowIndex).sort((a, b) => a.col_index - b.col_index).forEach((cell) => {
      const node = document.createElement(cell.is_header ? "th" : "td");
      node.textContent = cell.text;
      if (cell.row_span > 1) node.rowSpan = cell.row_span;
      if (cell.col_span > 1) node.colSpan = cell.col_span;
      node.title = `${cell.cell_id}\nrow headers: ${(cell.row_header_path || []).join(" > ")}\ncolumn headers: ${(cell.column_header_path || []).join(" > ")}\nunit: ${cell.unit_hint || "?"}`;
      row.appendChild(node);
    });
    htmlTable.appendChild(row);
  }
  grid.appendChild(htmlTable);
  const visual = $("#table-visual"); visual.replaceChildren();
  const crop = artifactById(table.crop_artifact_id);
  if (crop) {
    const image = document.createElement("img"); image.src = pathToArtifactUrl(crop.path); image.alt = `${table.table_id} crop`; visual.appendChild(image);
  } else visual.textContent = "无表格区域截图";
  const counts = {};
  (table.graph_edges || []).forEach((edge) => { counts[edge.relation] = (counts[edge.relation] || 0) + 1; });
  $("#table-edges").textContent = `关系：${Object.entries(counts).map(([key, value]) => `${key} ${value}`).join(" · ") || "无"}`;
  renderLogicalTable(logical);
}

function renderLogicalTable(logical) {
  const root = $("#logical-table-view");
  root.replaceChildren();
  if (!logical) {
    root.appendChild(documentNode("div", "muted", "该物理表不属于跨页逻辑表。"));
    return;
  }
  const statusClass = logical.status === "review_required" ? "error" : "ok";
  root.append(
    documentNode("h3", "", "跨页逻辑表（派生视图）"),
    documentNode(
      "div",
      `logical-table-status ${statusClass}`,
      `${logical.logical_table_id} · ${logical.status} · ${logical.composition_mode || logical.composition_axis} · ${logical.logical_row_count}×${logical.logical_column_count}`,
    ),
    documentNode("p", "muted", `物理来源：${(logical.source_table_ids || []).join(" → ")}`),
  );
  if (logical.status === "review_required") {
    root.appendChild(documentNode(
      "div",
      "issue error",
      "左右片段的行对齐尚未闭环。物理表仍完整保留，但该逻辑表不能进入 Evidence。",
    ));
  }
  if ((logical.cells || []).length) {
    const wrapper = documentNode("div", "table-grid logical-grid");
    const table = document.createElement("table");
    for (let rowIndex = 0; rowIndex < logical.logical_row_count; rowIndex += 1) {
      const row = document.createElement("tr");
      (logical.cells || [])
        .filter((cell) => cell.row_index === rowIndex)
        .sort((a, b) => a.col_index - b.col_index)
        .forEach((cell) => {
          const node = document.createElement(cell.is_header ? "th" : "td");
          node.textContent = cell.text;
          node.title = `source cells: ${(cell.source_cell_ids || []).join(", ") || "none"}`;
          row.appendChild(node);
        });
      table.appendChild(row);
    }
    wrapper.appendChild(table);
    root.appendChild(wrapper);
  }
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `查看 ${logical.cell_mappings?.length || 0} 条源 Cell 映射`;
  details.append(
    summary,
    documentNode("pre", "json compact-json", JSON.stringify({
      segments: logical.segments || [],
      cell_mappings: logical.cell_mappings || [],
      quality_flags: logical.quality_flags || [],
    }, null, 2)),
  );
  root.appendChild(details);
}

function renderFigures(figures) {
  const root = $("#figure-gallery"); root.replaceChildren();
  if (!figures.length) return root.appendChild(documentNode("div", "empty-state", "没有图片或图表候选。"));
  figures.forEach((figure) => {
    const card = documentNode("div", "figure-card");
    const crop = artifactById(figure.crop_artifact_id);
    const imagePath = crop?.path || figure.image_path;
    if (imagePath) {
      const image = document.createElement("img"); image.src = pathToArtifactUrl(imagePath); image.alt = figure.figure_id; card.appendChild(image);
    }
    const elements = new Set(figure.element_block_ids || []);
    const visualRelations = (irDocument.structure_edges || []).filter((edge) =>
      edge.relation?.startsWith("visual_")
      && (edge.source_id === figure.figure_id || elements.has(edge.source_id) || elements.has(edge.target_id))
    );
    card.append(
      documentNode("strong", "", figure.figure_id),
      documentNode("span", "", `page ${figure.page_index + 1} · ${figure.visual_status}`),
      documentNode("span", "", `图内元素 ${elements.size} · 视觉关系 ${visualRelations.length}`),
      documentNode("span", "", (figure.quality_flags || []).join(", ")),
    );
    card.addEventListener("click", () => { $("#json-preview").textContent = JSON.stringify(figure, null, 2); });
    root.appendChild(card);
  });
}

function renderReviewList(root, items, emptyText, fields, onClick = null) {
  root.replaceChildren();
  if (!items.length) return root.appendChild(documentNode("div", "muted", emptyText));
  items.forEach((item) => {
    const [title, subtitle, meta, detail] = fields(item);
    const card = documentNode("div", "review-card");
    card.append(documentNode("strong", "", title), documentNode("span", "", subtitle), documentNode("span", "", meta), documentNode("span", "", detail));
    card.addEventListener("click", () => {
      $("#json-preview").textContent = JSON.stringify(item, null, 2);
      if (onClick) onClick(item);
    });
    root.appendChild(card);
  });
}

function renderReviewSummary(state) {
  const root = $("#review-summary"); root.replaceChildren();
  const counts = state.inbox?.counts || {};
  const values = [
    [counts.human_required || 0, "人工内容判断"],
    [counts.system_blocked || 0, "需修链路后再跑"],
    [counts.blocking_deferred || 0, "自动修复待处理"],
    [counts.optional_deferred || 0, "非阻塞增强"],
    [counts.resolved || 0, "已解决任务"],
    [(state.decisions || []).filter((item) => item.outcome === "auto_corrected").length, "自动修正"],
    [(state.modelCalls || []).length, "模型调用记录"],
  ];
  values.forEach(([value, label]) => {
    const card = documentNode("div", "review-stat");
    card.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    root.appendChild(card);
  });
}

function renderReviewRetryOutcome(result) {
  const root = $("#review-retry-outcome");
  root.replaceChildren();
  if (!result) {
    root.className = "review-retry-outcome hidden";
    return;
  }
  const selected = Number(result.selected_count || 0);
  const resolved = Number(result.resolved_count || 0);
  const remaining = Number(result.remaining_count || 0);
  const failures = result.new_model_call_failure_categories || {};
  const tpd = Number(failures.rate_limit_tpd || failures.quota || 0);
  const rpm = Number(failures.rate_limit_rpm || 0);
  const skipped = Number(result.provider_short_circuited_count || 0);
  const checkpoint = Number(result.checkpoint_resumed_count || 0);
  root.className = `review-retry-outcome ${remaining ? "warning" : "success"}`;
  root.append(
    documentNode("strong", "", remaining
      ? `本轮解决 ${resolved}/${selected}，仍待处理 ${remaining}`
      : `本轮已解决 ${resolved}/${selected}`),
    documentNode("span", "", remaining && resolved === 0
      ? "重试流程确实执行了，但没有任务完成 Reviewer、Guard、Verifier 与写回闭环；请结合下面的限额和检查点数据判断原因。"
      : "这是父版本与当前子版本的真实任务状态差异，不是模型调用次数。"),
  );
  const metrics = documentNode("div", "review-retry-metrics");
  [
    [`新模型记录 ${result.new_model_call_count || 0}`, "calls"],
    [`TPD ${tpd}`, "tpd"],
    [`RPM ${rpm}`, "rpm"],
    [`熔断跳过 HTTP ${skipped}`, "skipped"],
    [`从 Verifier 检查点继续 ${checkpoint}`, "checkpoint"],
  ].forEach(([label]) => metrics.appendChild(documentNode("b", "", label)));
  root.appendChild(metrics);
}

function setReviewRetryInFlight(running) {
  reviewRetryInFlight = Boolean(running);
  const blocking = $("#retry-blocking-reviews");
  const optional = $("#run-optional-reviews");
  [blocking, optional].forEach((button) => { if (button) button.disabled = reviewRetryInFlight; });
  if (blocking) blocking.textContent = reviewRetryInFlight ? "自动复核运行中…" : "重试全部阻塞待处理";
  if (optional) optional.textContent = reviewRetryInFlight ? "请等待当前任务" : "运行全部非阻塞增强";
}

function firstAvailableReviewGroup(inbox) {
  const groups = inbox?.groups || {};
  return ["human_required", "system_blocked", "blocking_deferred", "optional_deferred", "resolved"]
    .find((name) => (groups[name] || []).length) || "human_required";
}

function renderReviewInbox(groupName) {
  activeReviewGroup = groupName;
  const labels = {
    human_required: "人工内容判断",
    system_blocked: "链路修复阻塞",
    blocking_deferred: "自动修复待处理",
    optional_deferred: "非阻塞增强",
    resolved: "已解决审计记录",
  };
  $("#review-queue-title").textContent = labels[groupName] || groupName;
  document.querySelectorAll("[data-review-group]").forEach((button) => {
    button.classList.toggle("active", button.dataset.reviewGroup === groupName);
  });
  const root = $("#review-tasks");
  root.replaceChildren();
  const bundles = reviewState.inbox?.groups?.[groupName] || [];
  if (!bundles.length) {
    root.appendChild(documentNode("div", "empty-review-queue", "这一组现在没有任务。"));
    clearReviewSelection();
    return;
  }
  bundles.forEach((bundle) => {
    const task = bundle.task || {};
    const guidance = bundle.guidance || {};
    const card = documentNode("button", "review-card review-inbox-card");
    card.type = "button";
    card.dataset.reviewTaskId = task.task_id;
    card.append(
      documentNode("strong", "", guidance.title || task.task_id),
      documentNode("span", "review-status-line", `${guidance.retryable === false ? "链路修复后再运行" : reviewStatusLabel(task.status)} · ${guidance.retryable === false ? "不可原样重试" : "可自动重试"} · ${task.blocking ? "影响 Evidence 准入" : "不阻塞"} · ${task.task_id}`),
      documentNode("span", "review-card-question", guidance.question || (guidance.diagnosis || task.reason_codes || []).join("；")),
    );
    card.addEventListener("click", () => showReviewTask(task));
    root.appendChild(card);
  });
  const selected = bundles.find((item) => item.task?.task_id === selectedReviewTaskId) || bundles[0];
  showReviewTask(selected.task);
}

function clearReviewSelection() {
  selectedReviewTaskId = null;
  $("#review-guidance").replaceChildren(documentNode("div", "muted", "这一组没有待查看任务。"));
  $("#review-visual").replaceChildren(documentNode("div", "muted", "暂无证据图。"));
  $("#review-actions").replaceChildren(documentNode("span", "muted", "暂无操作。"));
  $("#candidate-diff").replaceChildren(documentNode("div", "muted", "尚无候选差异。"));
  $("#review-timeline").replaceChildren(documentNode("div", "muted", "尚无审计时间线。"));
}

function reviewBundle(taskId) {
  return Object.values(reviewState.inbox?.groups || {})
    .flat()
    .find((item) => item.task?.task_id === taskId);
}

function renderReviewGuidance(bundle) {
  const root = $("#review-guidance");
  root.replaceChildren();
  const task = bundle?.task || {};
  const guidance = bundle?.guidance || {};
  root.append(
    documentNode("div", "review-impact", guidance.impact || (task.blocking ? "阻塞 Evidence 阶段" : "可选复核")),
    documentNode("h3", "", guidance.title || task.task_id || "Review task"),
    documentNode("div", "review-question-label", task.status === "human_required" ? "你现在只需要回答" : "系统正在处理的问题"),
    documentNode("div", "review-question", guidance.question || "当前候选是否忠实表示原页？"),
    documentNode("p", "review-risk", guidance.current_risk || ""),
    documentNode(
      "div",
      "transaction-summary",
      `恢复阶段：${guidance.resume_stage || task.resume_stage || "reviewer_pending"} · `
        + `任务执行 ${guidance.execution_count ?? task.execution_count ?? 0} 次 · `
        + `Reviewer ${guidance.reviewer_call_count ?? task.reviewer_call_count ?? 0} 次 · `
        + `Verifier ${guidance.verifier_call_count ?? task.verifier_call_count ?? 0} 次`,
    ),
  );
  const checklist = documentNode("ol", "evidence-checklist");
  (guidance.evidence_checklist || []).forEach((item) => checklist.appendChild(documentNode("li", "", item)));
  if (checklist.children.length) root.append(documentNode("strong", "finding-title", "判断顺序"), checklist);
  const list = documentNode("ul", "diagnosis-list");
  (guidance.diagnosis || task.reason_codes || ["系统要求复核该区域。"]).forEach((item) => {
    list.appendChild(documentNode("li", "", item));
  });
  root.append(
    documentNode("strong", "finding-title", "为什么出现"),
    list,
    documentNode("div", task.status === "human_required" ? "human-boundary" : "automation-boundary", guidance.why_human || ""),
    documentNode("p", "recommended-action", guidance.recommended_action || ""),
  );
  const failed = guidance.failed_guard_checks || [];
  if (failed.length) {
    root.appendChild(documentNode("strong", "finding-title", `Guard 未通过 · ${failed.length}`));
    failed.forEach((item) => {
      const details = item.details || {};
      const missing = details.missing_target_ids || details.missing_source_cell_ids || [];
      const conflictText = details.conflicting_source_text || {};
      const lines = [item.message || item.code];
      if (missing.length) lines.push(`缺失目标/源单元格：${missing.join("、")}`);
      if (Object.keys(conflictText).length) {
        lines.push(`必须保留或明确映射：${Object.entries(conflictText).map(([id, text]) => `${id}=${text}`).join("；")}`);
      }
      if (details.recommended_operation) lines.push(`建议操作：${details.recommended_operation}`);
      root.appendChild(documentNode("div", "review-finding error", lines.join("\n")));
    });
  }
  const disagreements = guidance.verifier_disagreements || [];
  if (disagreements.length) {
    root.appendChild(documentNode("strong", "finding-title", `Verifier 分歧 · ${disagreements.length}`));
    disagreements.forEach((item) => root.appendChild(documentNode("div", "review-finding warning", String(item))));
  }
  if (guidance.failure_class && guidance.failure_class !== "none") {
    root.appendChild(documentNode(
      "div",
      `failure-owner ${guidance.retryable ? "retryable" : "blocked"}`,
      `失败归属：${guidance.failure_owner || "unknown"} · ${guidance.failure_class} · ${guidance.retryable ? "允许自动重试" : "禁止原样重试"}${guidance.failure_fingerprint ? ` · ${guidance.failure_fingerprint}` : ""}`,
    ));
  }
  const transactionSummary = guidance.transaction_summary || {};
  const transactionText = Object.entries(transactionSummary)
    .filter(([, count]) => Number(count) > 0)
    .map(([status, count]) => `${status} ${count}`)
    .join(" · ");
  if (transactionText) {
    root.appendChild(documentNode("div", "transaction-summary", `补丁事务：${transactionText}`));
  }
  const verifierTransactionDecisions = (bundle?.verifier_results || [])
    .flatMap((item) => item.transaction_decisions || []);
  if (verifierTransactionDecisions.length) {
    root.appendChild(documentNode("strong", "finding-title", "Verifier 逐事务结论"));
    verifierTransactionDecisions.forEach((item) => {
      const details = (item.disagreements || []).join("；");
      root.appendChild(documentNode(
        "div",
        `review-finding ${item.verdict === "accept" ? "success" : item.verdict === "reject" ? "error" : "warning"}`,
        `${item.transaction_id} · ${item.verdict} · 置信度 ${formatConfidence(item.confidence)}${details ? ` · ${details}` : ""}`,
      ));
    });
  }
  const scope = documentNode("details", "scope-contract");
  const scopeSummary = document.createElement("summary");
  scopeSummary.textContent = "查看审核读写边界";
  scope.append(
    scopeSummary,
    documentNode("pre", "json", JSON.stringify({
      required: guidance.required_decision_target_ids || [],
      mutable: guidance.mutable_target_ids || [],
      context_read_only: (guidance.context_target_ids || []).filter((id) => !(guidance.mutable_target_ids || []).includes(id)),
      compiler_version: task.review_plan?.compiler_version,
      contract_hash: task.review_plan?.contract_hash,
    }, null, 2)),
  );
  root.appendChild(scope);
}

function renderReviewVisual(bundle) {
  const root = $("#review-visual");
  root.replaceChildren();
  const visual = bundle?.visual_context || {};
  const refs = [
    ...(visual.spread_refs || []).map((ref) => ({ref, kind: "spread"})),
    ...(visual.member_pages || []).map((page) => ({
      ref: page.page_image_path,
      kind: "member",
      page,
    })),
    ...(!visual.member_pages?.length && visual.page_image_path
      ? [{ref: visual.page_image_path, kind: "page"}]
      : []),
    ...(visual.crop_refs || []).map((ref) => ({ref, kind: "crop"})),
  ].filter((item, index, items) =>
    item.ref && items.findIndex((candidate) => candidate.ref === item.ref) === index
  );
  if (!refs.length) {
    root.appendChild(documentNode("div", "muted", "该任务没有可显示的页图或区域截图。"));
    return;
  }
  refs.forEach((item) => {
    const figure = documentNode("figure", `review-figure ${item.kind === "spread" ? "spread-composite" : ""}`);
    const image = document.createElement("img");
    image.src = pathToArtifactUrl(item.ref);
    image.alt = item.kind === "spread"
      ? "Horizontal spread composite"
      : `Page ${Number(item.page?.page_index ?? visual.page_index) + 1}`;
    let caption = `局部证据 · ${item.ref.split("/").pop()}`;
    if (item.kind === "spread") caption = "左右拼接总览 · 先用它判断跨页关系";
    if (item.kind === "member") {
      caption = `原始物理页 · Page ${Number(item.page.page_index) + 1} · 印刷页 ${item.page.printed_page_label || "?"}`;
    }
    if (item.kind === "page") caption = `完整原页 · Page ${Number(visual.page_index) + 1}`;
    figure.append(
      image,
      documentNode("figcaption", "", caption),
    );
    root.appendChild(figure);
  });
}

function renderReviewActions(task, bundle) {
  const root = $("#review-actions");
  root.replaceChildren();
  const guidance = bundle?.guidance || {};
  const targets = [task.target_id, ...(task.scope || []).map((item) => item.target_id)].filter(Boolean);
  $("#repair-targets").value = [...new Set(targets)].join(", ");
  const locate = documentNode("button", "secondary", "在页面浏览器中定位");
  locate.type = "button";
  locate.addEventListener("click", () => syncReviewToExplorers(task));
  root.appendChild(locate);

  if (guidance.can_retry_automation) {
    const retry = documentNode(
      "button",
      "",
      task.blocking ? "只重试这个自动任务" : "运行这个非阻塞增强",
    );
    retry.type = "button";
    retry.addEventListener("click", () => startReviewRetry([task.task_id], !task.blocking));
    root.appendChild(retry);
  } else if (["pending", "queued", "deferred", "failed", "skipped"].includes(task.status)) {
    root.appendChild(documentNode("div", "decision-note", guidance.recommended_action || "该任务不能原样重试。"));
  }
  if (guidance.can_accept_current_nonmaterial) {
    const keepOptional = documentNode("button", "secondary", "接受当前非关键状态，不执行增强");
    keepOptional.type = "button";
    keepOptional.addEventListener("click", () => resolveOptionalTask(task));
    root.appendChild(keepOptional);
    root.appendChild(documentNode(
      "div",
      "decision-note",
      "该操作只关闭可选增强队列，不会把候选 Spread、图表结构或版式关系标成已验证事实。",
    ));
  }

  const safePatchIds = new Set(guidance.safe_patch_ids || []);
  const humanPatches = (bundle?.patches || []).filter((item) => item.status === "human_required");
  humanPatches.forEach((patch) => {
    const safe = safePatchIds.has(patch.patch_id);
    const box = documentNode("div", `patch-action ${safe ? "safe" : "unsafe"}`);
    box.append(
      documentNode("strong", "", `${operationLabel(patch.operation)} · ${patch.target_id}`),
      documentNode(
        "span",
        `patch-safety ${safe ? "safe" : "unsafe"}`,
        safe ? "已通过本地 Patch Guard，可进行内容裁决" : "未通过本地 Guard，不允许人工接受",
      ),
      documentNode("span", "", patch.rationale || "查看差异后作出决定"),
    );
    if (patch.proposed_value != null) {
      box.appendChild(documentNode("pre", "patch-proposal", JSON.stringify(patch.proposed_value, null, 2)));
    }
    const buttons = documentNode("div", "button-row");
    const accept = documentNode("button", "", "原页支持，接受修订");
    const reject = documentNode("button", "danger", "原页不支持，拒绝修订");
    [accept, reject].forEach((button) => { button.type = "button"; });
    accept.addEventListener("click", () => {
      $("#human-patch").value = patch.patch_id;
      decideHumanPatch("accept");
    });
    reject.addEventListener("click", () => {
      $("#human-patch").value = patch.patch_id;
      decideHumanPatch("reject");
    });
    if (safe) buttons.appendChild(accept);
    buttons.appendChild(reject);
    if (buttons.children.length) box.appendChild(buttons);
    root.appendChild(box);
  });
  if (task.status === "human_required" && !humanPatches.length) {
    if (task.target_type === "spread") {
      root.appendChild(documentNode("div", "decision-note", "模型没有形成可靠结论。请先看拼接总览，再分别看左右原页。"));
      const confirmSpread = documentNode("button", "", "确认：两页应左右拼接");
      const rejectSpread = documentNode("button", "secondary", "确认：两页相互独立");
      [confirmSpread, rejectSpread].forEach((button) => { button.type = "button"; });
      confirmSpread.addEventListener("click", () => resolveHumanTask(task, "confirm_spread"));
      rejectSpread.addEventListener("click", () => resolveHumanTask(task, "reject_spread"));
      root.append(confirmSpread, rejectSpread);
      root.appendChild(documentNode("div", "decision-note", "仍无法判断时不要关闭任务，保留为“需要内容判断”并发起定向复核。"));
    } else {
      root.appendChild(documentNode("div", "decision-note", "没有待接受补丁。请在下方填写判断依据，然后二选一。"));
    }
    const keep = documentNode("button", "secondary", "当前 IR 正确，关闭本项");
    keep.type = "button";
    keep.addEventListener("click", () => resolveHumanTask(task));
    if (task.target_type !== "spread") root.appendChild(keep);
    const repair = documentNode("button", "", "当前 IR 仍有问题，发起定向修复");
    repair.type = "button";
    repair.addEventListener("click", () => {
      $("#repair-reason").value = `human_review_followup_${guidance.review_kind || "document_ir"}`;
      $("#repair-form").scrollIntoView({behavior: "smooth", block: "center"});
      $("#repair-reason").focus();
    });
    root.appendChild(repair);
  }
  if (["auto_resolved", "reviewed", "done"].includes(task.status)) {
    root.appendChild(documentNode("div", "resolved-note", "该任务已解决。这里只保留审计查看，不需要再次操作。"));
  }
}

function syncReviewToExplorers(task) {
  if (!irDocument) return;
  $("#page-select").value = String(task.page_index);
  renderPage(Number(task.page_index));
  const targetIds = new Set([task.target_id, ...(task.scope || []).map((item) => item.target_id)]);
  const table = (irDocument.tables || []).find((item) =>
    targetIds.has(item.table_id) || (item.cells || []).some((cell) => targetIds.has(cell.cell_id))
  );
  if (table) {
    $("#table-select").value = table.table_id;
    renderTable(table.table_id);
  }
}

function showReviewTask(task) {
  if (!task) return;
  selectedReviewTaskId = task.task_id;
  document.querySelectorAll("[data-review-task-id]").forEach((node) => {
    node.classList.toggle("selected", node.dataset.reviewTaskId === task.task_id);
  });
  const bundle = reviewBundle(task.task_id) || {task};
  renderReviewGuidance(bundle);
  renderReviewVisual(bundle);
  renderReviewActions(task, bundle);
  syncReviewToExplorers(task);
  const root = $("#review-timeline"); root.replaceChildren();
  root.appendChild(timelineCard("Quality Router", `${task.priority} · ${task.blocking ? "阻断" : "可选"}`, {
    reason_codes: task.reason_codes,
    scope: task.scope,
    input_refs: task.input_refs,
  }));
  const calls = (reviewState.modelCalls || []).filter((item) => item.task_id === task.task_id);
  const reviewers = (reviewState.reviewerResults || []).filter((item) => item.task_id === task.task_id);
  const guards = (reviewState.guards || []).filter((item) => item.task_id === task.task_id);
  const verifiers = (reviewState.verifierResults || []).filter((item) => item.task_id === task.task_id);
  const decisions = (reviewState.decisions || []).filter((item) => item.task_id === task.task_id);
  const transactions = bundle.patch_transactions || [];
  calls.forEach((call) => root.appendChild(timelineCard(
    `${call.role === "reviewer" ? "Reviewer" : "Verifier"} · round ${call.round_index}`,
    `${call.model_id} · ${call.status} · ${Math.round(call.latency_ms || 0)} ms`,
    call,
    call.status === "succeeded" ? "success" : "error",
  )));
  reviewers.forEach((item) => root.appendChild(timelineCard(`Reviewer result · round ${item.attempt}`, `${item.verdict} · confidence ${formatConfidence(item.confidence)}`, item)));
  transactions.forEach((item) => root.appendChild(timelineCard(
    `Patch transaction · ${item.transaction_id}`,
    `${item.status} · ${item.failure_class || "none"} · ${item.retryable === false ? "non-retryable" : "retryable"}`,
    item,
    item.status === "accepted" ? "success" : item.status === "guard_failed" ? "error" : "",
  )));
  guards.forEach((item) => root.appendChild(timelineCard("Local Patch Guard", item.passed ? "PASS" : "FAIL", item, item.passed ? "success" : "error")));
  verifiers.forEach((item) => root.appendChild(timelineCard("Independent verifier", `${item.verdict} · ${item.model_id} · confidence ${formatConfidence(item.confidence)}`, item, item.verdict === "accept" ? "success" : "warning")));
  decisions.forEach((item) => root.appendChild(timelineCard("Final decision", `${item.outcome} · ${item.decided_by}`, item, item.blocking_resolved ? "success" : "warning")));
  if (!calls.length && !decisions.length) root.appendChild(documentNode("div", "muted", "该任务尚未执行模型复核。"));
  const humanPatches = (reviewState.patches || []).filter((item) => item.source_task_id === task.task_id && item.status === "human_required");
  if (task.status === "human_required" && !humanPatches.length) {
    root.appendChild(documentNode("div", "muted", "该任务没有待裁决补丁，可在上方操作区确认保留当前 IR。"));
  }
  renderCandidateDiff(task.task_id);
}

function timelineCard(title, meta, payload, status = "") {
  const card = documentNode("details", `timeline-card ${status}`);
  const summary = document.createElement("summary");
  summary.append(documentNode("strong", "", title), documentNode("span", "", meta));
  const pre = documentNode("pre", "json", JSON.stringify(payload, null, 2));
  card.append(summary, pre);
  return card;
}

function renderCandidateDiff(taskId) {
  const root = $("#candidate-diff"); root.replaceChildren();
  const candidates = (reviewState.candidates || []).filter((item) => item.task_id === taskId);
  if (!candidates.length) return root.appendChild(documentNode("div", "muted", "该任务没有生成候选修订。"));
  candidates.forEach((candidate, index) => {
    const box = documentNode("div", "diff-round");
    box.append(documentNode("strong", "", `Candidate ${index + 1} · ${candidate.status}`));
    if (!(candidate.diffs || []).length) box.appendChild(documentNode("span", "muted", "确认型补丁：内容无变化。"));
    const structuralDelta = [
      ...((candidate.created_entity_ids || []).map((id) => `新增对象 ${id}`)),
      ...((candidate.retired_entity_ids || []).map((id) => `退役对象 ${id}`)),
      ...((candidate.created_structure_edge_ids || []).map((id) => `新增关系 ${id}`)),
    ];
    structuralDelta.forEach((value) => box.appendChild(documentNode("span", "diff-path", value)));
    (candidate.diffs || []).forEach((diff) => {
      const row = documentNode("div", "diff-row");
      row.append(
        documentNode("span", "diff-path", `${diff.target_id} · ${diff.field}`),
        documentNode("pre", "diff-before", JSON.stringify(diff.before, null, 2)),
        documentNode("pre", "diff-after", JSON.stringify(diff.after, null, 2)),
      );
      box.appendChild(row);
    });
    root.appendChild(box);
  });
}

function showPatch(patch) {
  const task = (reviewState.tasks || []).find((item) => item.task_id === patch.source_task_id);
  if (task) showReviewTask(task);
  $("#json-preview").textContent = JSON.stringify(patch, null, 2);
  if (patch.status === "human_required") $("#human-patch").value = patch.patch_id;
}

function populateHumanPatches(patches) {
  const select = $("#human-patch");
  select.replaceChildren(new Option("选择 human_required patch", ""));
  const safeIds = safeHumanPatchIds();
  patches.filter((item) => item.status === "human_required").forEach((item) => {
    const safety = safeIds.has(item.patch_id) ? "Guard PASS" : "Guard FAIL";
    select.add(new Option(`${item.patch_id} · ${safety} · ${operationLabel(item.operation)} · ${item.target_id}`, item.patch_id));
  });
}

async function decideHumanPatch(action) {
  const patchId = $("#human-patch").value;
  if (!activeIrRunId || !patchId) return alert("请选择一个 human_required patch。");
  if (action === "accept" && !safeHumanPatchIds().has(patchId)) {
    return alert("该补丁没有通过本地 Patch Guard，不能接受；你可以拒绝它，再决定保留当前 IR 或发起定向修复。");
  }
  const actionLabel = action === "accept" ? "接受并应用" : "拒绝";
  if (!window.confirm(`确认${actionLabel}补丁 ${patchId}，并创建不可变子版本吗？`)) return;
  try {
    const manifest = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/patches/${encodeURIComponent(patchId)}/decision`, {
      action,
      decided_by: $("#human-operator").value.trim() || "local-operator",
      notes: $("#human-notes").value.trim() || null,
    });
    await refreshHistories();
    await loadIrRun(manifest.run_id);
  } catch (error) {
    alert(`人工裁决失败：${error.message}`);
  }
}

function safeHumanPatchIds() {
  return new Set(
    Object.values(reviewState.inbox?.groups || {})
      .flat()
      .flatMap((bundle) => bundle.guidance?.safe_patch_ids || []),
  );
}

async function resolveHumanTask(task, action = "keep_current") {
  if (!activeIrRunId) return;
  const notes = $("#human-notes").value.trim();
  if (!notes) return alert("请先在“人工依据”中写清楚：你看到了什么，以及为什么作出这个判断。");
  const actionText = {
    keep_current: "保留当前 IR",
    confirm_spread: "确认两页左右拼接",
    reject_spread: "确认两页相互独立",
  }[action] || action;
  if (!window.confirm(`${actionText}，并将任务 ${task.task_id} 标记为人工已解决吗？`)) return;
  try {
    const manifest = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/review-tasks/${encodeURIComponent(task.task_id)}/decision`, {
      action,
      decided_by: $("#human-operator").value.trim() || "local-operator",
      notes,
    });
    await refreshHistories();
    await loadIrRun(manifest.run_id);
  } catch (error) {
    alert(`任务裁决失败：${error.message}`);
  }
}

async function resolveOptionalTask(task) {
  if (!activeIrRunId) return;
  const notes = $("#human-notes").value.trim()
    || "本 revision 接受当前非关键状态；未执行可选视觉增强，候选关系不得视为已验证语义。";
  if (!window.confirm(`关闭非阻塞增强 ${task.task_id}，并创建不可变子版本吗？`)) return;
  try {
    const manifest = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/review-tasks/${encodeURIComponent(task.task_id)}/decision`, {
      action: "accept_current_nonmaterial",
      decided_by: $("#human-operator").value.trim() || "local-operator",
      notes,
    });
    await refreshHistories();
    await loadIrRun(manifest.run_id);
  } catch (error) {
    alert(`关闭非阻塞增强失败：${error.message}`);
  }
}

async function startReviewRetry(taskIds = [], includeOptional = false) {
  if (!activeIrRunId) return;
  const blockingCount = reviewState.inbox?.counts?.blocking_deferred || 0;
  if (!taskIds.length && !blockingCount && !includeOptional) return alert("当前没有阻塞待处理任务。");
  if (reviewRetryInFlight) return;
  setReviewRetryInFlight(true);
  try {
    const state = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/reviews/retry`, {
      task_ids: taskIds,
      include_optional: includeOptional,
      max_auto_review_rounds: 3,
      requested_by: $("#human-operator").value.trim() || "local-operator",
      notes: $("#human-notes").value.trim() || null,
      qiniu_api_key: $("#qiniu-key").value.trim() || null,
    });
    activeIrRunId = state.run_id;
    $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
    renderLogs($("#ir-logs"), [state.message]);
    renderRuntimeTelemetry(state);
    pollIrState();
  } catch (error) {
    setReviewRetryInFlight(false);
    const retryAfter = error.detail?.provider_state?.retry_after_seconds;
    const suffix = retryAfter ? `\n预计 ${formatDuration(Number(retryAfter))} 后可再次尝试，或更换独立额度的 API Key。` : "";
    alert(`自动审核重试未启动：${error.message}${suffix}`);
  }
}

function startAllOptionalReviews() {
  const taskIds = (reviewState.inbox?.groups?.optional_deferred || [])
    .map((bundle) => bundle.task)
    .filter((task) => task?.retryable !== false)
    .map((task) => task.task_id);
  if (!taskIds.length) return alert("当前没有可执行的非阻塞增强。");
  startReviewRetry(taskIds, true);
}

function formatConfidence(value) {
  return Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "?";
}

function reviewStatusLabel(status) {
  return {
    human_required: "需要内容判断",
    deferred: "等待自动重试",
    pending: "等待自动执行",
    queued: "已进入自动队列",
    running: "自动处理中",
    auto_resolved: "已自动解决",
    reviewed: "已人工解决",
    done: "已完成",
    failed: "自动执行失败",
    skipped: "本轮未执行",
  }[status] || status || "未知状态";
}

function operationLabel(operation) {
  return {
    confirm: "确认当前对象",
    confirm_spread: "确认左右跨页",
    reject_spread: "确认两页独立",
    link_horizontal_continuation: "建立横向跨缝关系",
    replace_block_text: "修正文字块",
    replace_cell_text: "修正单元格文字",
    set_table_grid: "重建完整表格网格",
    insert_table_row: "补入缺失表格行",
    set_bbox: "修正区域坐标",
    set_printed_page_label: "修正印刷页码",
    set_visual_type: "修正视觉类型",
    set_caption: "修正标题",
    link_continuation: "建立跨页表格关系",
    merge_blocks: "合并文字块",
    split_block: "拆分文字块",
    add_quality_flags: "补充质量标记",
    retire_table_candidate: "退役伪表格候选",
    add_visual_text_block: "补入图内可见文字",
    set_figure_legend_text: "修正图例文字",
    upsert_figure_structure: "补充图内元素与关系",
  }[operation] || operation;
}

async function renderRevisions(ocrRunId) {
  const root = $("#revision-list"); root.replaceChildren();
  if (!ocrRunId) return;
  const requestBackend = backendUrl();
  const revisions = await fetchJson(`/api/document-ir/revisions/${encodeURIComponent(ocrRunId)}`, []);
  if (requestBackend !== backendUrl()) return;
  revisions.forEach((item) => {
    const chip = documentNode("button", "revision", `r${item.ir_revision} · ${item.readiness}`);
    chip.type = "button";
    chip.title = item.run_id;
    chip.addEventListener("click", () => loadIrRun(item.run_id));
    root.appendChild(chip);
  });
  await loadRevisionOptions(ocrRunId);
}

function renderArtifactLinks(root, files, kind) {
  root.replaceChildren();
  const groups = new Map();
  files.forEach((item) => {
    const first = item.path.includes("/") ? item.path.split("/", 1)[0] : item.path === "manifest.json" ? "manifest" : "other";
    if (!groups.has(first)) groups.set(first, []);
    groups.get(first).push(item);
  });
  const order = Object.keys(artifactGroupLabels);
  [...groups.entries()].sort(([left], [right]) => order.indexOf(left) - order.indexOf(right)).forEach(([group, items]) => {
    const section = document.createElement("details");
    section.className = "artifact-group";
    section.open = ["manifest", "canonical", "provider", "content"].includes(group);
    section.appendChild(documentNode("summary", "", `${artifactGroupLabels[group] || artifactGroupLabels.other} · ${items.length} files`));
    const list = documentNode("div", "artifact-group-files");
    items.forEach((item) => list.appendChild(artifactLink(item, kind)));
    section.appendChild(list);
    root.appendChild(section);
  });
}

function artifactLink(item, kind) {
    const link = documentNode("a", `artifact-link ${kind}`, `${item.path} · ${formatBytes(item.size)}`);
    link.href = endpoint(item.url);
    link.target = "_blank";
    if (/\.(json|jsonl|md|txt)$/i.test(item.path)) {
      link.addEventListener("click", async (event) => {
        event.preventDefault();
        const text = await (await fetch(link.href)).text();
        let rendered = text;
        if (item.path.endsWith(".json")) {
          try { rendered = JSON.stringify(JSON.parse(text), null, 2); } catch { /* keep text */ }
        }
        if (kind === "ocr" && item.path.endsWith(".md")) $("#markdown-preview").textContent = rendered;
        else $("#json-preview").textContent = rendered;
      });
    }
    return link;
}

function artifactById(id) {
  return (irDocument?.artifacts || []).find((item) => item.artifact_id === id);
}

function pathToArtifactUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  if (path.startsWith("ocr-package://")) {
    const value = path.slice("ocr-package://".length);
    const [runId, ...parts] = value.split("/");
    const relative = parts.map(encodeURIComponent).join("/");
    return endpoint(`/ocr_output/${encodeURIComponent(runId)}/${relative}`);
  }
  if (/^(source-pdf|external-local):\/\//.test(path)) return "";
  for (const marker of ["/document_ir_output/", "/ocr_output/"]) {
    const index = path.indexOf(marker);
    if (index >= 0) {
      const relative = path.slice(index + marker.length).split("/").map(encodeURIComponent).join("/");
      return endpoint(`${marker}${relative}`);
    }
  }
  if (activeIrRunId && !path.startsWith("/")) {
    const relative = path.split("/").map(encodeURIComponent).join("/");
    return endpoint(`/document_ir_output/${encodeURIComponent(activeIrRunId)}/${relative}`);
  }
  return path;
}

function documentNode(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function formatBytes(value) {
  if (!Number.isFinite(value)) return "?";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

$("#page-select").addEventListener("change", (event) => renderPage(Number(event.target.value)));
$("#table-select").addEventListener("change", (event) => renderTable(event.target.value));
$("#ocr-history").addEventListener("change", (event) => event.target.value && loadOcrRun(event.target.value));
$("#ir-history").addEventListener("change", (event) => event.target.value && loadIrRun(event.target.value));
$("#ocr-run-id").addEventListener("change", (event) => event.target.value.trim() && loadRevisionOptions(event.target.value.trim()));
$("#backend-url").addEventListener("change", () => { localStorage.setItem("esg-v2-backend-url", backendUrl()); refreshHistories(); });
$("#refresh-all").addEventListener("click", refreshHistories);
$("#refresh-models").addEventListener("click", refreshModelStatus);
$("#accept-patch").addEventListener("click", () => decideHumanPatch("accept"));
$("#reject-patch").addEventListener("click", () => decideHumanPatch("reject"));
$("#review-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-review-group]");
  if (button) renderReviewInbox(button.dataset.reviewGroup);
});
$("#retry-blocking-reviews").addEventListener("click", () => startReviewRetry());
$("#run-optional-reviews").addEventListener("click", startAllOptionalReviews);
$("#repair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activeIrRunId) return alert("请先选择一个父 Document IR revision。");
  const targetIds = $("#repair-targets").value.split(",").map((item) => item.trim()).filter(Boolean);
  if (!targetIds.length) return alert("请至少填写一个 Target ID。");
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const state = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/repair`, {
      parent_ir_run_id: activeIrRunId,
      target_ids: targetIds,
      reason_code: $("#repair-reason").value.trim() || "downstream_document_ir_mismatch",
      requested_by: $("#repair-requester").value.trim() || "local-operator",
      notes: $("#human-notes").value.trim() || null,
      execute_vlm_reviews: $("#repair-execute-vlm").checked,
      qiniu_api_key: $("#qiniu-key").value.trim() || null,
    });
    activeIrRunId = state.run_id;
    $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
    renderLogs($("#ir-logs"), [state.message]);
    pollIrState();
  } catch (error) {
    alert(`返修启动失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

const storedBackend = localStorage.getItem("esg-v2-backend-url");
if (storedBackend) $("#backend-url").value = storedBackend;
renderPipeline();
refreshHistories();
refreshModelStatus();
