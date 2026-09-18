import {
  ocrProviderName,
  reportIdentity,
  runDate,
  searchableText,
} from "./selection.js?v=input-selectors-v3-20260829";

const $ = (selector) => document.querySelector(selector);

const pipelineDefinitions = [
  ["01", "PaddleOCR 主解析", "Local-first / API fallback → same package"],
  ["02", "页图与本地取证", "Render + PDF cross-check"],
  ["03", "Layout 转换", "Raw objects + coordinates"],
  ["04", "确定性融合", "Dedupe + local observations"],
  ["05", "结构与视觉区域", "Sections + tables + crops"],
  ["06", "左右跨页建模", "Seam detection + composite"],
  ["07", "分层质量路由", "Typed question · scoped risks"],
  ["08", "分类与修复 Agent", "Classify · correct · abstain"],
  ["09", "Patch Guard", "Atomic candidate + invariants"],
  ["10", "Verifier 二次核验", "云端异族 / 本地隔离复核"],
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
let storageInventory = {available: false, summary: {}, documents: []};
let selectedStorageTargets = new Map();
let selectedAssetKey = null;
let currentDeletionPlan = null;
let currentDeletionTargets = [];
let activeAppView = "report-assets";
let irSourceOcrRuns = [];
let irSourceIrRuns = [];
let selectedIrSourceRunId = localStorage.getItem("esg-v2-ir-source-run-id") || "";
let reviewWorklistEntries = [];
let reviewWorklistLatestRunCount = 0;
let reviewWorklistFailedRunCount = 0;
let reviewWorklistRequestId = 0;
let openingReviewWorkItemKey = null;

const appViewDefinitions = {
  "report-assets": ["Report library", "报告资产", "管理专用 PDF 目录，并查看 OCR、IR 修订、文件体积与依赖关系。"],
  ocr: ["Primary parsing", "OCR 任务", "从报告资产选择 PDF，配置本地或 API Provider，然后加入统一任务队列。"],
  "document-ir": ["Document model", "Document IR 任务", "从不可变 OCR 包构建结构化 Document IR revision。"],
  "ir-review": ["Review and inspection", "IR 复核检查中心", "检查页面、坐标、表格、跨页、质量准入与 Agent 复核决策。"],
  targeted: ["Targeted recall", "定向抽取任务", "选择 evidence-ready IR、标准要求包和指标，并加入统一任务队列。"],
  results: ["Result bundles", "抽取结果", "检查 Core records、证据、Guard、检索、模型决定与全部中间文件。"],
  "pipeline-plan": ["Pipeline control", "07 · 链路控制", "选择报告文件夹、模型和多个标准指标，持久化计划并依次排队到最终成果。"],
};

const appViewAliases = {
  tasks: "document-ir",
  assets: "report-assets",
  inspector: "ir-review",
  reviews: "ir-review",
  system: "report-assets",
};

const appViewSources = {
  "report-assets": ["assets", "report-assets"],
  ocr: ["tasks", "ocr"],
  "document-ir": ["tasks", "document-ir"],
  "ir-review": ["inspector", "reviews", "ir-review"],
  targeted: ["targeted"],
  results: ["results"],
  "pipeline-plan": ["pipeline-plan"],
};

const artifactGroupLabels = {
  manifest: "00 · Package 入口",
  source: "01 · 原始输入与画布预检",
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

function selectedReviewProvider() {
  return $("#review-provider").value || "local_nuextract";
}

function selectedOcrProvider() {
  return $("#ocr-provider").value || "local_first";
}

function syncOcrProviderControls() {
  const provider = selectedOcrProvider();
  const localFirst = provider === "local_first";
  const apiOnly = provider === "paddle_api";
  $("#allow-api-fallback").disabled = !localFirst;
  $("#ocr-fallback-field").classList.toggle("muted", !localFirst);
  $("#ocr-token-field").classList.toggle("muted", provider === "local_paddleocr");
  $("#token").disabled = provider === "local_paddleocr";
  if (apiOnly) $("#allow-api-fallback").checked = false;
}

async function refreshOcrProviderStatus() {
  const payload = await fetchJson("/api/ocr/providers/status", null);
  const root = $("#ocr-provider-status");
  if (!payload) {
    root.textContent = "OCR Provider 状态暂不可用。";
    root.className = "agent-note error";
    return;
  }
  const local = payload.providers?.local_paddleocr || {};
  const api = payload.providers?.paddle_api || {};
  const reasonLabels = {
    runtime_python_missing: "本地运行环境不存在",
    runtime_import_failed: "本地运行库导入失败",
    runtime_probe_invalid: "本地运行环境探测异常",
    vlm_server_executable_missing: "MLX-VLM Server 未安装",
    model_weights_missing: "模型权重尚未完整下载",
    mlx_model_probe_failed: "本地模型完整性或处理器检查失败",
    mlx_model_unsupported: "当前 MLX-VLM 不支持该模型结构",
    model_weights_unreadable: "模型权重无法读取",
    api_token_missing: "后端未配置 API Token",
  };
  const missingPipelineModels = Object.entries(local.details?.required_pipeline_models || {})
    .filter(([, ready]) => !ready)
    .map(([name]) => name);
  const localText = local.available
    ? `本地 OCR ${local.status === "ready" ? "已就绪" : `核心模型已就绪，首次运行将下载 ${missingPipelineModels.join("、") || "流水线依赖"}`} · Paddle ${local.details?.runtime_versions?.paddle || "?"} · MLX ${local.details?.runtime_versions?.mlx_vlm || "?"} · 权重 ${formatBytes(local.details?.model_size_bytes || 0)} · tensors ${local.details?.model_tensor_count || 0}`
    : `本地 OCR 未就绪：${(local.reason_codes || []).map((code) => reasonLabels[code] || code).join("、") || "未知原因"}`;
  const apiText = api.available ? "后端 API Token 已配置" : "API 可在本页临时填写 Token";
  root.textContent = `${localText}；${apiText}。当前默认：${payload.default_provider || "local_first"}`;
  root.className = `agent-note ${local.available ? "" : "warning"}`.trim();
}

function syncReviewProviderControls() {
  const provider = selectedReviewProvider();
  const isQiniu = provider === "qiniu";
  $("#qiniu-key").disabled = !isQiniu;
  $("#qiniu-key-field").classList.toggle("muted", !isQiniu);
  $("#review-provider-note").textContent = isQiniu
    ? "七牛模式：主审候选 → 本地 Patch Guard → 不同模型家族独立复核 → 有界反馈返修。受网络、RPM 与 TPD 限额约束。"
    : "本地模式：NuExtract3 主审候选 → 同一 Patch Guard → 隔离上下文的 NuExtract3 二次核验 → 有界反馈返修。无需 API Key；IR 输出合同与七牛模式完全一致。";
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
  reviewWorklistEntries = [];
  reviewWorklistLatestRunCount = 0;
  reviewWorklistFailedRunCount = 0;
  reviewWorklistRequestId += 1;
  openingReviewWorkItemKey = null;
  storageInventory = {available: false, summary: {}, documents: []};
  selectedStorageTargets.clear();
  selectedAssetKey = null;
  currentDeletionPlan = null;
  activeReviewGroup = "human_required";
  selectedReviewTaskId = null;
  setReviewRetryInFlight(false);
  $("#current-run").textContent = "尚未启动";
  $("#current-ir-run").textContent = "尚未启动";
  $("#logs").textContent = "";
  setJson($("#ocr-preflight"), null);
  $("#ir-logs").textContent = "";
  $("#artifacts").replaceChildren();
  $("#ir-artifacts").replaceChildren();
  setIrWorkspaceAvailable(false);
  renderStorageInventory(storageInventory);
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
  setIrWorkspaceAvailable(false);
  renderRuntimeTelemetry(null);
  renderPipeline();
}

function resetOcrView() {
  clearTimeout(pollTimer);
  activeRunId = null;
  $("#current-run").textContent = "尚未启动";
  $("#logs").textContent = "";
  setJson($("#ocr-preflight"), null);
  setJson($("#manifest"), null);
  $("#artifacts").replaceChildren();
  $("#markdown-preview").textContent = "点击任意 content/pages/page-0001.md。";
}

function setIrWorkspaceAvailable(available) {
  $("#ir-workbench").classList.toggle("hidden", !available);
  $("#ir-empty-review").classList.toggle("hidden", available);
}

function switchAppView(view, {scroll = true} = {}) {
  const requested = appViewAliases[view] || view;
  const selected = appViewDefinitions[requested] ? requested : "report-assets";
  const definition = appViewDefinitions[selected];
  const sources = appViewSources[selected] || [selected];
  activeAppView = selected;
  document.querySelectorAll("[data-app-view]").forEach((node) => {
    node.classList.toggle("view-hidden", !sources.includes(node.dataset.appView));
  });
  document.querySelectorAll("[data-component-view]").forEach((node) => {
    const componentViews = node.dataset.componentView.split(/\s+/);
    node.classList.toggle("component-hidden", !componentViews.includes(selected));
  });
  document.querySelectorAll("[data-app-view-target]").forEach((node) => {
    node.classList.toggle("active", node.dataset.appViewTarget === selected);
  });
  $("#view-eyebrow").textContent = definition[0];
  $("#view-title").textContent = definition[1];
  $("#view-description").textContent = definition[2];
  localStorage.setItem("esg-v2-active-view", selected);
  window.dispatchEvent(new CustomEvent("esg:viewchange", {detail: {view: selected}}));
  if (selected === "ir-review") refreshReviewWorklist();
  if (scroll) window.scrollTo({top: 0, behavior: "smooth"});
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
      [calls.actual_attempts || 0, "实际模型请求"],
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
        ? `${current.task_id} · ${current.role} · ${current.provider || "model"} · round ${current.round_index} · retry ${current.retry_index} · ${current.model_id}`
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
  const payload = await fetchJson(`/api/models/status?provider=${encodeURIComponent(selectedReviewProvider())}`, {});
  if (requestBackend !== backendUrl()) return;
  const root = $("#model-status"); root.replaceChildren();
  const providers = payload.providers || {};
  Object.entries(providers).forEach(([providerName, provider]) => {
    const title = providerName === "local_nuextract" ? "本地 NuExtract3" : "七牛云 VLM";
    const verification = provider.independent_model_family
      ? "不同模型家族独立复核"
      : "同模型隔离上下文二次复核";
    root.appendChild(documentNode("div", "model-health provider-heading", `${title} · ${verification}`));
    if (provider.catalog_error) {
      root.appendChild(documentNode("div", "model-health error", `模型目录暂不可用：${provider.catalog_error}`));
    }
    const providerBlock = provider.provider_rate_limit?.account_block;
    if (providerBlock) {
      root.appendChild(documentNode(
        "div",
        "model-health error",
        `七牛账号 TPD 熔断中 · 约 ${formatDuration(providerBlock.retry_after_seconds || 0)} 后恢复；七牛重试会在发起前被拦截`,
      ));
    }
    (provider.approved_vision_models || []).forEach((modelId) => {
      const health = (provider.health || []).find((item) => item.model_id === modelId);
      const card = documentNode("div", `model-health ${health?.last_status || "unknown"}`);
      card.append(
        documentNode("strong", "", modelId),
        documentNode(
          "span",
          "",
          health
            ? `${health.last_status} · 成功 ${health.successes} · 运行 ${health.transport_failures || 0} · 协议 ${health.protocol_failures || 0}`
            : providerName === "local_nuextract" ? "本地运行时与权重就绪 · 尚未调用" : "approved · 当前进程尚未调用",
        ),
      );
      root.appendChild(card);
    });
    if (providerName === "local_nuextract" && !(provider.approved_vision_models || []).length) {
      const reason = provider.probe?.error || "未找到本地运行时或模型权重";
      root.appendChild(documentNode("div", "model-health error", `NuExtract3 不可用：${reason}`));
    }
    (provider.retired_models_still_listed || []).forEach((modelId) => {
      const card = documentNode("div", "model-health retired");
      card.append(documentNode("strong", "", modelId), documentNode("span", "", "七牛目录仍列出，但系统已禁用"));
      root.appendChild(card);
    });
  });
  if (!root.children.length) root.appendChild(documentNode("span", "muted", "未发现已批准且可用的视觉模型。"));
}

async function refreshHistories() {
  syncBackendContext();
  const requestBackend = backendUrl();
  await checkHealth();
  const [ocrRuns, irRuns, documents, inventory] = await Promise.all([
    fetchJson("/api/ocr/jobs", []),
    fetchJson("/api/document-ir/jobs", []),
    fetchJson("/api/document-ir/documents", []),
    fetchJson("/api/storage/inventory", {available: false, summary: {}, documents: []}),
  ]);
  if (requestBackend !== backendUrl()) return;
  populateDocumentHistory($("#ocr-history"), ocrRuns, "选择 OCR Run", "ocr");
  populateDocumentHistory($("#ir-history"), irRuns, "选择 IR Run", "ir");
  irSourceOcrRuns = ocrRuns;
  irSourceIrRuns = irRuns;
  renderIrSourcePicker();
  if (activeAppView === "ir-review") await refreshReviewWorklist(irRuns);
  renderDocumentCatalog(documents);
  storageInventory = inventory || {available: false, summary: {}, documents: []};
  renderStorageInventory(storageInventory);
  if (activeRunId && !ocrRuns.some((item) => item.run_id === activeRunId)) {
    resetOcrView();
  }
  if (activeIrRunId && !irRuns.some((item) => item.run_id === activeIrRunId)) {
    resetIrView();
  }
}

function populateDocumentHistory(select, items, placeholder, kind) {
  const selected = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  const groups = new Map();
  [...items].sort(compareRunRecency).forEach((item) => {
    const documentId = item.summary?.document_id || `unclassified-${item.run_id}`;
    if (!groups.has(documentId)) groups.set(documentId, []);
    groups.get(documentId).push(item);
  });
  groups.forEach((runs, documentId) => {
    const label = runs[0]?.summary?.document_label || "未命名 PDF";
    const group = document.createElement("optgroup");
    group.label = `${label} · ${shortIdentity(documentId)}`;
    runs.forEach((item) => {
      const summary = item.summary || {};
      const runSuffix = String(item.run_id).slice(-12);
      const text = kind === "ocr"
        ? `OCR · ${runTimestamp(item.run_id)} · ${summary.ocr_provider === "local_paddleocr" ? "本地" : summary.ocr_provider === "paddle_api" ? "API" : summary.model || item.status} · ${runSuffix}`
        : `分支 ${shortIdentity(summary.lineage_id)} · r${summary.ir_revision || 1} · ${summary.readiness || item.status} · ${runSuffix}`;
      group.appendChild(new Option(text, item.run_id));
    });
    select.appendChild(group);
  });
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
}

function ocrRunIsUsable(run) {
  return run?.status === "done" && Boolean(run?.manifest_path);
}

function ocrStatusName(status) {
  return {
    done: "OCR 已完成",
    running: "正在处理",
    queued: "等待执行",
    failed: "处理失败",
    cancelled: "已取消",
    interrupted: "已中断",
  }[status] || status || "状态未知";
}

function selectIrSource(runId, {persist = true} = {}) {
  const run = irSourceOcrRuns.find((item) => item.run_id === runId);
  selectedIrSourceRunId = runId || "";
  $("#ocr-run-id").value = selectedIrSourceRunId;
  if (persist) localStorage.setItem("esg-v2-ir-source-run-id", selectedIrSourceRunId);
  if (run?.summary?.document_label) $("#document-label").value = run.summary.document_label;
  renderIrSourcePicker();
  if (selectedIrSourceRunId) loadRevisionOptions(selectedIrSourceRunId);
}

function renderIrSourcePicker() {
  const root = $("#ir-source-picker");
  const selectedRoot = $("#ir-selected-source");
  if (!root || !selectedRoot) return;
  if ($("#ocr-run-id").value !== selectedIrSourceRunId) {
    $("#ocr-run-id").value = selectedIrSourceRunId;
  }

  const query = $("#ir-source-search").value.trim().toLocaleLowerCase("zh-CN");
  const filter = $("#ir-source-filter").value;
  const usableCount = irSourceOcrRuns.filter(ocrRunIsUsable).length;
  const visible = [...irSourceOcrRuns].sort(compareRunRecency).filter((run) => {
    const provider = run.summary?.ocr_provider || run.progress_detail?.provider || "";
    const matchesFilter = filter === "all"
      || (filter === "usable" && ocrRunIsUsable(run))
      || (filter === "local" && provider === "local_paddleocr" && ocrRunIsUsable(run))
      || (filter === "api" && provider === "paddle_api" && ocrRunIsUsable(run))
      || (filter === "incomplete" && !ocrRunIsUsable(run));
    const haystack = searchableText(
      run.run_id,
      run.status,
      provider,
      run.summary?.document_label,
      run.summary?.document_id,
      run.summary?.source_pdf_sha256,
    );
    return matchesFilter && (!query || haystack.includes(query));
  });
  $("#ir-source-summary").textContent = `${visible.length} 条匹配 · ${usableCount} 个可构建`;

  selectedRoot.replaceChildren();
  const selected = irSourceOcrRuns.find((item) => item.run_id === selectedIrSourceRunId);
  if (!selected && selectedIrSourceRunId) {
    selectedRoot.className = "selected-input-summary manual";
    selectedRoot.append(
      documentNode("strong", "", "手动指定 OCR Run"),
      documentNode("span", "", "该 Run 未出现在当前目录中，提交时仍由后端执行存在性与包合同校验。"),
      documentNode("code", "", selectedIrSourceRunId),
    );
  } else if (!selected) {
    selectedRoot.className = "selected-input-summary empty-state";
    selectedRoot.textContent = "尚未选择 OCR 解析结果。请从下方报告卡片中选择。";
  } else {
    const identity = reportIdentity(selected.summary?.document_label, selected.run_id);
    const irCount = irSourceIrRuns.filter((item) => item.ocr_run_id === selected.run_id).length;
    selectedRoot.className = "selected-input-summary selected";
    const title = documentNode("div", "selected-input-title");
    const reportName = documentNode("strong", "", identity.displayName);
    reportName.title = identity.displayName;
    title.append(
      documentNode("span", "year-badge", identity.year),
      reportName,
      documentNode("span", "status-pill success", "已选输入"),
    );
    const facts = documentNode("div", "selected-input-facts");
    [
      [`${selected.page_count || selected.summary?.page_count || 0} 页`, "页数"],
      [ocrProviderName(selected), "OCR Provider"],
      [`${irCount} 个`, "已有 IR"],
      [runDate(selected.run_id, selected.summary?.written_at), "OCR 时间"],
    ].forEach(([value, label]) => {
      const item = documentNode("span");
      item.append(documentNode("strong", "", value), documentNode("small", "", label));
      facts.appendChild(item);
    });
    selectedRoot.append(title, facts, documentNode("code", "", selected.run_id));
  }

  root.replaceChildren();
  visible.forEach((run) => {
    const identity = reportIdentity(run.summary?.document_label, run.run_id);
    const usable = ocrRunIsUsable(run);
    const irRuns = irSourceIrRuns.filter((item) => item.ocr_run_id === run.run_id);
    const card = documentNode("button", `input-choice-card ${run.run_id === selectedIrSourceRunId ? "selected" : ""} ${usable ? "" : "unavailable"}`.trim());
    card.type = "button";
    card.disabled = !usable;
    card.title = usable ? `选择 ${run.run_id}` : `${ocrStatusName(run.status)}，暂不能构建 IR`;
    const head = documentNode("div", "input-choice-head");
    const name = documentNode("div", "input-choice-name");
    const reportName = documentNode("strong", "", identity.displayName);
    reportName.title = identity.displayName;
    name.append(documentNode("span", "year-badge", identity.year), reportName);
    head.append(name, documentNode("span", `status-pill ${usable ? "success" : run.status}`, ocrStatusName(run.status)));
    const facts = documentNode("div", "input-choice-facts");
    facts.append(
      documentNode("span", "", `${run.page_count || run.summary?.page_count || 0} 页`),
      documentNode("span", "", ocrProviderName(run)),
      documentNode("span", "", `已有 IR ${irRuns.length}`),
      documentNode("span", "", runDate(run.run_id, run.summary?.written_at)),
    );
    card.append(head, facts, documentNode("code", "", run.run_id));
    if (usable) card.addEventListener("click", () => selectIrSource(run.run_id));
    root.appendChild(card);
  });
  if (!visible.length) root.appendChild(documentNode("div", "empty-state", "没有符合搜索和筛选条件的 OCR Package。"));
}

function compareRunRecency(a, b) {
  const aTime = String(a.summary?.written_at || a.updated_at || a.run_id || "");
  const bTime = String(b.summary?.written_at || b.updated_at || b.run_id || "");
  return bTime.localeCompare(aTime);
}

function latestReviewRuns(irRuns) {
  const latestByLineage = new Map();
  (irRuns || []).filter((run) => run.status === "done" && run.manifest_path).forEach((run) => {
    const lineage = run.summary?.lineage_id || run.run_id;
    const current = latestByLineage.get(lineage);
    const revision = Number(run.summary?.ir_revision || 0);
    const currentRevision = Number(current?.summary?.ir_revision || 0);
    if (!current || revision > currentRevision || (revision === currentRevision && compareRunRecency(run, current) < 0)) {
      latestByLineage.set(lineage, run);
    }
  });
  return [...latestByLineage.values()].sort(compareRunRecency);
}

function reviewGroupLabel(group) {
  return {
    human_required: "人工内容判断",
    system_blocked: "链路修复阻塞",
    blocking_deferred: "自动修复待处理",
    optional_deferred: "非阻塞增强",
    resolved: "已解决审计记录",
  }[group] || group || "状态未知";
}

function reviewTargetTypeLabel(targetType) {
  return {
    page: "页面",
    table: "表格",
    figure: "图片/图表",
    spread: "左右跨页",
    block: "文字块",
    cell: "表格单元格",
    logical_table: "逻辑表格",
  }[targetType] || targetType || "结构对象";
}

async function refreshReviewWorklist(irRuns = irSourceIrRuns) {
  const body = $("#review-worklist-body");
  if (!body) return;
  const requestId = ++reviewWorklistRequestId;
  const requestBackend = backendUrl();
  body.innerHTML = '<tr><td colspan="6"><div class="empty-state">正在读取最新 IR 分支的复核任务…</div></td></tr>';
  const catalog = await fetchJson("/api/document-ir/review-worklist", null);
  if (requestId !== reviewWorklistRequestId || requestBackend !== backendUrl()) return;
  if (catalog?.schema_version === "document-ir-review-worklist-catalog-v1") {
    reviewWorklistLatestRunCount = Number(catalog.latest_run_count || 0);
    reviewWorklistFailedRunCount = (catalog.failed_run_ids || []).length;
    reviewWorklistEntries = sortReviewWorklistEntries(catalog.entries || []);
    renderReviewWorklist();
    return;
  }

  // Compatibility path for an older backend that has not exposed the compact catalog yet.
  const latestRuns = latestReviewRuns(irRuns);
  reviewWorklistLatestRunCount = latestRuns.length;
  reviewWorklistFailedRunCount = 0;
  if (!latestRuns.length) {
    reviewWorklistEntries = [];
    renderReviewWorklist();
    return;
  }
  const results = await Promise.all(latestRuns.map(async (run) => ({
    run,
    inbox: await fetchJson(`/api/document-ir/jobs/${encodeURIComponent(run.run_id)}/human-review/inbox`, null),
  })));
  if (requestId !== reviewWorklistRequestId || requestBackend !== backendUrl()) return;
  const entries = [];
  ["human_required", "system_blocked", "blocking_deferred", "optional_deferred"].forEach((group) => {
    results.forEach(({run, inbox}) => {
      if (!inbox) return;
      (inbox.groups?.[group] || []).forEach((bundle) => {
        if (bundle?.task?.task_id) entries.push({run, group, bundle});
      });
    });
  });
  results.forEach(({run, inbox}) => {
    (inbox?.validation_issues || []).forEach((issue) => {
      const taskId = `validation:${issue.code}:${issue.target_id || issue.page_index || "report"}`;
      entries.push({
        run, group: "system_blocked", validation_issue: issue,
        bundle: {
          task: {task_id: taskId, target_id: issue.target_id, target_type: issue.target_id?.startsWith("table") ? "table" : "page", page_index: issue.page_index, blocking: true, status: "validation_issue"},
          guidance: {title: issue.target_id ? `验证问题 · ${issue.target_id}` : "报告验证问题", question: issue.message, resume_stage: "validation", can_retry_automation: false},
        },
      });
    });
  });
  reviewWorklistFailedRunCount = results.filter((item) => !item.inbox).length;
  reviewWorklistEntries = sortReviewWorklistEntries(entries);
  renderReviewWorklist();
}

function sortReviewWorklistEntries(entries) {
  const priority = {human_required: 0, system_blocked: 1, blocking_deferred: 2, optional_deferred: 3};
  return [...entries].sort((a, b) =>
    (priority[a.group] ?? 9) - (priority[b.group] ?? 9)
      || Number(Boolean(b.bundle.task.blocking)) - Number(Boolean(a.bundle.task.blocking))
      || compareRunRecency(a.run, b.run)
      || String(a.bundle.task.task_id).localeCompare(String(b.bundle.task.task_id))
  );
}

function renderReviewWorklist() {
  const body = $("#review-worklist-body");
  const summary = $("#review-worklist-summary");
  if (!body || !summary) return;
  const counts = reviewWorklistEntries.reduce((result, item) => {
    result[item.group] = (result[item.group] || 0) + 1;
    return result;
  }, {});
  const reportCount = new Set(reviewWorklistEntries.map((item) => item.run.summary?.document_id || item.run.run_id)).size;
  const summaryValues = [
    [reviewWorklistEntries.length, "未完成任务"],
    [reportCount, "涉及报告"],
    [counts.human_required || 0, "需人工判断"],
    [(counts.system_blocked || 0) + (counts.blocking_deferred || 0), "阻塞待处理"],
    [counts.optional_deferred || 0, "非阻塞增强"],
  ];
  summary.replaceChildren();
  summaryValues.forEach(([value, label]) => {
    const item = documentNode("div", "review-worklist-stat");
    item.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    summary.appendChild(item);
  });
  if (reviewWorklistFailedRunCount) {
    const warning = documentNode("div", "review-worklist-warning", `${reviewWorklistFailedRunCount} 个最新 IR 的复核目录读取失败，请刷新或检查包完整性。`);
    summary.appendChild(warning);
  }

  const query = $("#review-worklist-search").value.trim().toLocaleLowerCase("zh-CN");
  const groupFilter = $("#review-worklist-group").value;
  const impactFilter = $("#review-worklist-impact").value;
  const visible = reviewWorklistEntries.filter((entry) => {
    const task = entry.bundle.task || {};
    const guidance = entry.bundle.guidance || {};
    const matchesGroup = groupFilter === "all" || entry.group === groupFilter;
    const matchesImpact = impactFilter === "all"
      || (impactFilter === "blocking" && task.blocking)
      || (impactFilter === "optional" && !task.blocking);
    const haystack = searchableText(
      entry.run.run_id,
      entry.run.summary?.document_label,
      entry.run.summary?.document_id,
      entry.run.summary?.readiness,
      task.task_id,
      task.target_id,
      task.target_type,
      task.status,
      guidance.title,
      guidance.question,
      guidance.review_kind,
    );
    return matchesGroup && matchesImpact && (!query || haystack.includes(query));
  });

  body.replaceChildren();
  if (!reviewWorklistLatestRunCount) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.appendChild(documentNode("div", "empty-state", "还没有可读取的 Document IR revision。"));
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  if (!visible.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.appendChild(documentNode("div", "empty-state", reviewWorklistEntries.length
      ? "没有符合当前搜索和筛选条件的未完成任务。"
      : "各条最新 IR 分支当前没有未完成复核任务。"));
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }

  visible.forEach((entry) => {
    const {run, group, bundle} = entry;
    const task = bundle.task || {};
    const guidance = bundle.guidance || {};
    const identity = reportIdentity(run.summary?.document_label, run.run_id);
    const row = document.createElement("tr");
    const workItemKey = `${run.run_id}:${task.task_id}`;
    const isOpening = openingReviewWorkItemKey === workItemKey;
    if (activeIrRunId === run.run_id && selectedReviewTaskId === task.task_id) row.classList.add("selected");
    if (isOpening) row.classList.add("opening");

    const report = document.createElement("td");
    const reportName = documentNode("strong", "", identity.displayName);
    reportName.title = identity.displayName;
    report.append(
      reportName,
      documentNode("small", "", `${identity.year} · Revision r${run.summary?.ir_revision || 1} · ${run.summary?.readiness || "状态未知"}`),
      documentNode("code", "", run.run_id),
    );

    const question = document.createElement("td");
    question.append(
      documentNode("strong", "", guidance.title || task.task_id),
      documentNode("small", "", guidance.question || (task.reason_codes || []).join("；") || "查看复核计划"),
    );

    const target = document.createElement("td");
    const pageIndex = Number(task.page_index);
    const pageLabel = Number.isInteger(pageIndex) && pageIndex >= 0 ? `PDF 第 ${pageIndex + 1} 页` : "页码未记录";
    target.append(
      documentNode("strong", "", `${reviewTargetTypeLabel(task.target_type)} · ${pageLabel}`),
      documentNode("small", "", task.target_id || "未记录 Target ID"),
      documentNode("code", "", task.task_id),
    );

    const status = document.createElement("td");
    status.append(
      documentNode("span", `status-pill review-group-${group}`, reviewGroupLabel(group)),
      documentNode("small", "", task.blocking ? "阻塞 Evidence 准入" : "不阻塞 Evidence 准入"),
    );

    const progress = document.createElement("td");
    progress.append(
      documentNode("strong", "", `阶段：${guidance.resume_stage || task.resume_stage || "reviewer_pending"}`),
      documentNode("small", "", `执行 ${guidance.execution_count ?? task.execution_count ?? 0} · Reviewer ${guidance.reviewer_call_count ?? task.reviewer_call_count ?? 0} · Verifier ${guidance.verifier_call_count ?? task.verifier_call_count ?? 0}`),
    );

    const action = document.createElement("td");
    const open = documentNode("button", "secondary review-worklist-open", isOpening ? "正在打开…" : "打开复核");
    open.type = "button";
    open.disabled = isOpening;
    open.addEventListener("click", (event) => {
      event.stopPropagation();
      openReviewWorkItem(entry);
    });
    action.appendChild(open);
    row.append(report, question, target, status, progress, action);
    row.tabIndex = 0;
    row.title = `打开 ${task.task_id}`;
    row.addEventListener("click", () => openReviewWorkItem(entry));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openReviewWorkItem(entry);
      }
    });
    body.appendChild(row);
  });
}

async function openReviewWorkItem(entry) {
  const taskId = entry.bundle.task.task_id;
  const workItemKey = `${entry.run.run_id}:${taskId}`;
  if (openingReviewWorkItemKey) return;
  openingReviewWorkItemKey = workItemKey;
  renderReviewWorklist();
  try {
    if (entry.validation_issue) {
      await loadIrRun(entry.run.run_id);
      const issue = entry.validation_issue;
      if (issue.target_id) $("#repair-targets").value = issue.target_id;
      if (issue.code) $("#repair-reason").value = issue.code;
      if (Number.isInteger(issue.page_index) && issue.page_index >= 0) {
        $("#page-select").value = String(issue.page_index);
        renderPage(issue.page_index);
      }
      $("#validation-view").scrollIntoView({behavior: "smooth", block: "start"});
      return;
    }
    selectedReviewTaskId = taskId;
    await loadIrRun(entry.run.run_id);
    if (!irDocument) {
      alert("该 Document IR 无法读取，请检查 Package 完整性。");
      return;
    }
    selectedReviewTaskId = taskId;
    renderReviewInbox(entry.group);
    document.querySelector(".review-cockpit")?.scrollIntoView({behavior: "smooth", block: "start"});
  } finally {
    openingReviewWorkItemKey = null;
    renderReviewWorklist();
  }
}

function shortIdentity(value) {
  const text = String(value || "unknown");
  return text.length > 16 ? `${text.slice(0, 7)}…${text.slice(-8)}` : text;
}

function runTimestamp(runId) {
  const match = String(runId || "").match(/-(\d{8})T(\d{6})Z-/);
  return match ? `${match[1].slice(0, 4)}-${match[1].slice(4, 6)}-${match[1].slice(6)} ${match[2].slice(0, 2)}:${match[2].slice(2, 4)}` : String(runId || "");
}

function renderDocumentCatalog(documents) {
  const root = $("#document-catalog");
  root.replaceChildren();
  $("#document-catalog-summary").textContent = `${documents.length} 份唯一 PDF · ${documents.reduce((sum, item) => sum + item.ocr_run_count, 0)} 次 OCR · ${documents.reduce((sum, item) => sum + item.ir_run_count, 0)} 个 IR 包`;
  documents.forEach((item) => {
    const card = documentNode("button", "document-catalog-item");
    card.type = "button";
    card.title = item.document_id;
    card.append(
      documentNode("strong", "", item.document_label || "未命名 PDF"),
      documentNode("span", "", `${shortIdentity(item.document_id)} · OCR ${item.ocr_run_count} · IR ${item.ir_run_count} · 分支 ${item.lineage_count}`),
      documentNode("span", `catalog-readiness ${item.latest_readiness || "ocr-only"}`, item.latest_readiness || "仅 OCR"),
    );
    if (item.latest_ir_run_id) card.addEventListener("click", async () => {
      await loadIrRun(item.latest_ir_run_id);
      switchAppView("ir-review");
    });
    else if (item.ocr_run_ids?.[0]) card.addEventListener("click", async () => {
      await loadOcrRun(item.ocr_run_ids[0]);
      switchAppView("ocr");
    });
    root.appendChild(card);
  });
  if (!documents.length) root.appendChild(documentNode("div", "empty-state", "还没有 OCR 或 Document IR 产物。"));
}

function renderStorageInventory(inventory) {
  const summary = inventory?.summary || {};
  const metrics = [
    [summary.document_count || 0, "唯一报告"],
    [summary.ocr_run_count || 0, "OCR Runs"],
    [summary.ir_run_count || 0, "IR Revisions"],
    [summary.incomplete_count || 0, "失败/不完整"],
    [summary.file_count || 0, "本地文件"],
    [formatBytes(summary.size_bytes || 0), "占用空间"],
  ];
  const summaryRoot = $("#storage-summary");
  summaryRoot.replaceChildren();
  metrics.forEach(([value, label]) => {
    const node = documentNode("div", "storage-metric");
    node.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    summaryRoot.appendChild(node);
  });

  const root = $("#storage-inventory");
  root.replaceChildren();
  root.classList.toggle("storage-management", $("#storage-management-mode").checked);
  const available = inventory?.available !== false;
  $("#cleanup-incomplete").disabled = !available;
  $("#storage-management-mode").disabled = !available;
  if (!available) {
    root.appendChild(documentNode("div", "deletion-message warning", "当前运行的后端尚未提供资产库存 API。请重启 Document IR 后端以加载新版本；现有 OCR/IR 产物没有受到影响。"));
    updateStorageSelectionBar();
    return;
  }
  const query = $("#storage-search").value.trim().toLowerCase();
  const filter = $("#storage-filter").value;
  const documents = (inventory?.documents || []).filter((document) => storageDocumentMatches(document, query, filter));
  documents.forEach((document, index) => root.appendChild(renderStorageDocument(document, index === 0 || Boolean(query))));
  if (!documents.length) {
    root.appendChild(documentNode("div", "empty-state compact-empty", query || filter !== "all" ? "没有匹配的本地产物。" : "当前没有 OCR 或 Document IR 产物。"));
  }
  reconcileStorageSelection(inventory);
}

function storageDocumentMatches(document, query, filter) {
  if (filter === "ready" && !allDocumentRuns(document).some((item) => item.kind === "ir" && item.can_build_evidence)) return false;
  if (filter === "incomplete" && !(document.incomplete_count > 0)) return false;
  if (filter === "ocr_only" && document.ir_run_count > 0) return false;
  if (!query) return true;
  const searchable = [
    document.document_id,
    document.document_label,
    document.source_pdf_sha256,
    ...allDocumentRuns(document).flatMap((item) => [item.run_id, item.lineage_id, item.readiness, item.status]),
  ].join(" ").toLowerCase();
  return searchable.includes(query);
}

function allDocumentRuns(document) {
  return [
    ...(document.ocr_runs || []),
    ...(document.ocr_runs || []).flatMap((ocr) => ocr.ir_runs || []),
    ...(document.unlinked_ir_runs || []),
  ];
}

function renderStorageDocument(documentItem, open) {
  const details = documentNode("details", "storage-document");
  details.open = open;
  const summary = document.createElement("summary");
  const main = documentNode("div", "document-summary-main");
  main.append(
    documentNode("strong", "", documentItem.document_label || "未命名 PDF"),
    documentNode("span", "", `${shortIdentity(documentItem.document_id)} · ${documentItem.source_pdf_sha256 ? `SHA ${documentItem.source_pdf_sha256.slice(0, 12)}…` : "无原始 SHA"}`),
  );
  const stats = documentNode("div", "document-summary-stats");
  [
    `OCR ${documentItem.ocr_run_count}`,
    `IR ${documentItem.ir_run_count}`,
    `${documentItem.file_count} files`,
    formatBytes(documentItem.size_bytes),
    documentItem.incomplete_count ? `异常 ${documentItem.incomplete_count}` : "结构完整",
  ].forEach((value) => stats.appendChild(documentNode("span", "chip", value)));
  summary.append(main, stats);
  const body = documentNode("div", "storage-document-body");
  const documentActions = documentNode("div", "asset-run-actions document-actions");
  const deleteDocument = documentNode("button", "danger management-action", "清理整份报告");
  deleteDocument.type = "button";
  deleteDocument.addEventListener("click", () => openDeletionPlan(
    [{kind: "document", target_id: documentItem.document_id}],
    true,
  ));
  documentActions.appendChild(deleteDocument);
  body.appendChild(documentActions);
  (documentItem.ocr_runs || []).forEach((ocr) => {
    const group = documentNode("section", "storage-run-group");
    group.appendChild(renderStorageRunRow(ocr));
    const irRuns = [...(ocr.ir_runs || [])].sort((a, b) => {
      const lineage = String(a.lineage_id || "").localeCompare(String(b.lineage_id || ""));
      return lineage || Number(a.ir_revision || 0) - Number(b.ir_revision || 0);
    });
    irRuns.forEach((ir) => group.appendChild(renderStorageRunRow(ir)));
    body.appendChild(group);
  });
  if ((documentItem.unlinked_ir_runs || []).length) {
    const group = documentNode("section", "storage-run-group");
    group.appendChild(documentNode("div", "orphan-label", "未链接到可用 OCR 包的 IR"));
    documentItem.unlinked_ir_runs.forEach((ir) => group.appendChild(renderStorageRunRow(ir)));
    body.appendChild(group);
  }
  details.append(summary, body);
  return details;
}

function renderStorageRunRow(run) {
  const key = storageTargetKey(run.kind, run.run_id);
  const row = documentNode("article", `asset-run-row ${run.kind}${selectedAssetKey === key ? " selected" : ""}`);
  row.dataset.assetKey = key;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "asset-checkbox";
  checkbox.checked = selectedStorageTargets.has(key);
  checkbox.setAttribute("aria-label", `选择 ${run.run_id}`);
  checkbox.addEventListener("click", (event) => event.stopPropagation());
  checkbox.addEventListener("change", () => toggleStorageTarget(run, checkbox.checked));
  const main = documentNode("div", "asset-run-main");
  const badge = documentNode("span", `asset-kind ${run.kind}`, run.kind === "ocr" ? "OCR PACKAGE" : "DOCUMENT IR");
  const status = run.kind === "ir" ? (run.readiness || run.status) : run.status;
  const identity = run.kind === "ir"
    ? `${run.run_id} · ${shortIdentity(run.lineage_id)} · r${run.ir_revision || "?"}`
    : run.run_id;
  main.append(
    badge,
    documentNode("strong", "", identity),
    documentNode("span", "", `${status || "unknown"} · ${run.package_state} · ${run.file_count} files · ${formatBytes(run.size_bytes)}`),
  );
  if (run.kind === "ir" && run.retention_status === "current_best") {
    main.appendChild(documentNode("span", "chip", "当前唯一保留版"));
  }
  const actions = documentNode("div", "asset-run-actions");
  const inspect = documentNode("button", "secondary", "查看");
  inspect.type = "button";
  inspect.addEventListener("click", async (event) => {
    event.stopPropagation();
    await showStorageRun(run);
  });
  const remove = documentNode("button", "danger management-action", "清理");
  remove.type = "button";
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    openDeletionPlan([{kind: run.kind, target_id: run.run_id}], $("#storage-cascade").checked);
  });
  actions.append(inspect, remove);
  row.append(checkbox, main, actions);
  row.addEventListener("click", () => showStorageRun(run));
  return row;
}

async function showStorageRun(run) {
  selectedAssetKey = storageTargetKey(run.kind, run.run_id);
  document.querySelectorAll("[data-asset-key]").forEach((node) => {
    node.classList.toggle("selected", node.dataset.assetKey === selectedAssetKey);
  });
  $("#asset-detail-title").textContent = run.kind === "ocr" ? "OCR 原始产物" : `Document IR r${run.ir_revision || "?"}`;
  const status = run.kind === "ir" ? (run.readiness || run.status) : run.status;
  const statusNode = $("#asset-detail-status");
  statusNode.className = `asset-status ${status || "unknown"}`;
  statusNode.textContent = status || "unknown";
  const meta = $("#asset-detail-meta");
  meta.replaceChildren();
  [
    ["Run ID", run.run_id],
    ["报告", run.document_label || shortIdentity(run.document_id)],
    ["包状态", run.package_state],
    ["Manifest", run.manifest_health],
    ["任务状态", run.state_health],
    ["文件", `${run.file_count} 个 · ${formatBytes(run.size_bytes)}`],
    ["最后活动", formatDateTime(run.updated_at)],
    ...(run.kind === "ir" ? [
      ["Lineage", run.lineage_id || "unknown"],
      ["父修订", run.parent_ir_run_id || "root"],
      ["子修订", (run.child_ir_run_ids || []).join(", ") || "无"],
      ["Evidence 准入", run.can_build_evidence ? "允许" : "未允许"],
      ["保留策略", run.retention_status === "current_best" ? "当前唯一保留版" : run.retention_status === "candidate" ? "质量候选，尚未晋升" : "尚未经过保留策略"],
    ] : [
      ["页数", String(run.page_count || 0)],
      ["OCR Provider", run.ocr_provider === "local_paddleocr" ? "本地 PaddleOCR-VL" : run.ocr_provider === "paddle_api" ? "PaddleOCR-VL API" : "历史包未记录"],
      ["Provider 路由", run.provider_route?.fallback_reason ? `回退：${run.provider_route.fallback_reason}` : run.provider_route?.requested || "历史包未记录"],
      ["原 PDF 副本", run.has_upload ? "存在" : "无"],
    ]),
  ].forEach(([label, value]) => {
    const item = documentNode("div", "asset-detail-row");
    item.append(documentNode("span", "", label), documentNode("span", "", String(value || "-")));
    meta.appendChild(item);
  });
  const actions = $("#asset-detail-actions");
  actions.replaceChildren();
  const open = documentNode("button", "", run.kind === "ocr" ? "载入任务页" : "打开 IR 检查器");
  open.type = "button";
  open.addEventListener("click", async () => {
    if (run.kind === "ocr") {
      await loadOcrRun(run.run_id);
      switchAppView("tasks");
    } else {
      await loadIrRun(run.run_id);
      switchAppView("inspector");
    }
  });
  actions.appendChild(open);
  if (run.kind === "ir") {
    const reviews = documentNode("button", "secondary", "打开复核中心");
    reviews.type = "button";
    reviews.addEventListener("click", async () => {
      await loadIrRun(run.run_id);
      switchAppView("reviews");
    });
    actions.appendChild(reviews);
  }
  const plan = documentNode("button", "secondary", "检查清理影响");
  plan.type = "button";
  plan.addEventListener("click", () => openDeletionPlan(
    [{kind: run.kind, target_id: run.run_id}],
    $("#storage-cascade").checked,
  ));
  actions.appendChild(plan);
  $("#asset-detail-files").replaceChildren(documentNode("span", "muted", "正在读取文件列表…"));
  $("#asset-detail-preview").textContent = "点击 JSON、JSONL 或 Markdown 文件在此预览。";
  const path = run.kind === "ocr"
    ? `/api/ocr/jobs/${encodeURIComponent(run.run_id)}/artifacts`
    : `/api/document-ir/jobs/${encodeURIComponent(run.run_id)}/artifacts`;
  const artifacts = await fetchJson(path, {files: []});
  if (selectedAssetKey !== storageTargetKey(run.kind, run.run_id)) return;
  renderArtifactLinks($("#asset-detail-files"), artifacts.files || [], run.kind, $("#asset-detail-preview"));
}

function toggleStorageTarget(run, selected) {
  const key = storageTargetKey(run.kind, run.run_id);
  if (selected) selectedStorageTargets.set(key, {kind: run.kind, target_id: run.run_id});
  else selectedStorageTargets.delete(key);
  updateStorageSelectionBar();
}

function reconcileStorageSelection(inventory) {
  const known = new Set((inventory?.documents || []).flatMap(allDocumentRuns).map((run) => storageTargetKey(run.kind, run.run_id)));
  [...selectedStorageTargets.keys()].forEach((key) => {
    if (!known.has(key)) selectedStorageTargets.delete(key);
  });
  if (selectedAssetKey && !known.has(selectedAssetKey)) clearAssetDetail();
  updateStorageSelectionBar();
}

function storageTargetKey(kind, targetId) {
  return `${kind}:${targetId}`;
}

function updateStorageSelectionBar() {
  const bar = $("#storage-selection-bar");
  const count = selectedStorageTargets.size;
  bar.classList.toggle("hidden", !$("#storage-management-mode").checked || !count);
  $("#storage-selection-summary").textContent = count ? `已选择 ${count} 个 Run` : "尚未选择清理对象";
}

function clearAssetDetail() {
  selectedAssetKey = null;
  $("#asset-detail-title").textContent = "选择一个产物";
  $("#asset-detail-status").className = "asset-status";
  $("#asset-detail-status").textContent = "未选择";
  $("#asset-detail-meta").innerHTML = '<p class="muted">点击 OCR 或 IR 行可查看包状态、文件分类与体积。</p>';
  $("#asset-detail-actions").replaceChildren();
  $("#asset-detail-files").replaceChildren();
  $("#asset-detail-preview").textContent = "点击 JSON、JSONL 或 Markdown 文件在此预览。";
}

async function openDeletionPlan(targets, cascade = false) {
  if (!targets.length) return alert("请先选择要清理的产物。");
  currentDeletionTargets = targets;
  $("#deletion-plan-content").replaceChildren(documentNode("span", "muted", "正在编译依赖与影响范围…"));
  $("#execute-storage-deletion").disabled = true;
  const dialog = $("#deletion-dialog");
  if (!dialog.open) dialog.showModal();
  try {
    currentDeletionPlan = await postJson("/api/storage/deletion-plans", {targets, cascade});
    renderDeletionPlan(currentDeletionPlan, cascade);
  } catch (error) {
    currentDeletionPlan = null;
    $("#deletion-plan-content").replaceChildren(documentNode("div", "deletion-message blocker", `删除预检失败：${error.message}`));
  }
}

function renderDeletionPlan(plan, cascade) {
  const root = $("#deletion-plan-content");
  root.replaceChildren();
  const summary = documentNode("div", "deletion-plan-summary");
  [
    [plan.selected_runs?.length || 0, "Run 数量"],
    [plan.file_count || 0, "文件数量"],
    [formatBytes(plan.size_bytes || 0), "释放空间"],
  ].forEach(([value, label]) => {
    const node = documentNode("div", "storage-metric");
    node.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    summary.appendChild(node);
  });
  root.appendChild(summary);
  (plan.blockers || []).forEach((item) => root.appendChild(documentNode("div", "deletion-message blocker", `${item.code} · ${item.message}`)));
  (plan.warnings || []).forEach((item) => root.appendChild(documentNode("div", "deletion-message warning", `${item.code} · ${item.message}`)));
  if (!plan.can_execute && !cascade && (plan.blockers || []).some((item) => ["ocr_has_ir_dependencies", "ir_has_child_revisions", "document_requires_cascade"].includes(item.code))) {
    const replan = documentNode("button", "secondary", "开启级联并重新检查");
    replan.type = "button";
    replan.addEventListener("click", () => {
      $("#storage-cascade").checked = true;
      openDeletionPlan(currentDeletionTargets, true);
    });
    root.appendChild(replan);
  }
  const list = documentNode("div", "deletion-run-list");
  (plan.selected_runs || []).forEach((item) => list.appendChild(documentNode("code", "", `${item.kind.toUpperCase()} · ${item.run_id} · ${item.status}`)));
  root.appendChild(list);
  root.appendChild(documentNode("span", "muted", `计划有效至 ${formatDateTime(plan.expires_at)}；后端重启或存储变化后必须重新检查。`));
  updateDeletionExecuteState();
}

function updateDeletionExecuteState() {
  $("#execute-storage-deletion").disabled = !currentDeletionPlan?.can_execute;
}

async function executeStorageDeletion() {
  if (!currentDeletionPlan?.can_execute) return;
  const button = $("#execute-storage-deletion");
  if (!window.confirm(`确认清理计划中的 ${currentDeletionPlan.selected_runs?.length || 0} 个 Run 吗？`)) return;
  button.disabled = true;
  try {
    const result = await postJson(
      `/api/storage/deletion-plans/${encodeURIComponent(currentDeletionPlan.plan_id)}/execute`,
      {},
    );
    const deleted = new Set((result.deleted_runs || []).map((item) => storageTargetKey(item.kind, item.run_id)));
    if (activeRunId && deleted.has(storageTargetKey("ocr", activeRunId))) resetOcrView();
    if (activeIrRunId && deleted.has(storageTargetKey("ir", activeIrRunId))) resetIrView();
    selectedStorageTargets.clear();
    clearAssetDetail();
    currentDeletionPlan = null;
    $("#deletion-dialog").close();
    await refreshHistories();
  } catch (error) {
    alert(`清理失败：${error.message}`);
    updateDeletionExecuteState();
  }
}

function formatDateTime(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString("zh-CN", {hour12: false}) : "-";
}

function ocrRunStatusText(state) {
  const current = Number(state?.progress_current || 0);
  const total = Number(state?.progress_total || 0);
  const progress = total ? ` · ${current}/${total} 页` : "";
  const detail = state?.progress_detail || {};
  const waiting = Number(detail.noProgressSeconds || 0);
  const heartbeat = detail.state === "heartbeat" && waiting
    ? ` · 当前页已处理 ${formatDuration(waiting)}`
    : "";
  return `${state.run_id} · ${state.status}${progress}${heartbeat}`;
}

$("#ocr-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  const file = $("#pdf-file").files[0];
  const fileUrl = $("#file-url").value.trim();
  let assetId = $("#ocr-asset-select").value;
  if (file && fileUrl) return alert("临时 PDF 和 PDF URL 不能同时提供。 ");
  if (file && assetId) return alert("已选择报告资产时无需再上传临时 PDF，请保留一种来源。 ");
  if (!file && Boolean(assetId) === Boolean(fileUrl)) return alert("报告资产和 PDF URL 必须二选一。 ");
  if (fileUrl && selectedOcrProvider() === "local_paddleocr") return alert("仅本地 OCR 需要上传 PDF；URL 只能交给 API 获取。 ");
  if (fileUrl && selectedOcrProvider() === "local_first" && !$("#allow-api-fallback").checked) return alert("URL 输入在本地优先模式下必须允许 API 回退；要完全本地请上传 PDF。 ");
  button.disabled = true;
  try {
    if (file) {
      const upload = new FormData();
      upload.append("file", file);
      const uploadResponse = await fetch(endpoint("/api/report-assets"), {method: "POST", body: upload});
      if (!uploadResponse.ok) throw new Error(await uploadResponse.text());
      const asset = await uploadResponse.json();
      assetId = asset.asset_id;
      window.dispatchEvent(new CustomEvent("esg:assets-changed", {detail: {assetId}}));
    }
    const task = await postJson("/api/pipeline/tasks/ocr", {
      asset_id: fileUrl ? null : assetId,
      file_url: fileUrl || null,
      token: $("#token").value.trim() || null,
      model: $("#model").value.trim() || "PaddleOCR-VL-1.6",
      ocr_provider: selectedOcrProvider(),
      allow_api_fallback: $("#allow-api-fallback").checked,
      optional_payload: {
        useDocOrientationClassify: $("#use-orientation").checked,
        useDocUnwarping: $("#use-unwarping").checked,
        useChartRecognition: $("#use-chart").checked,
      },
    });
    activeRunId = task.native_job_id;
    $("#current-run").textContent = `${activeRunId} · 已加入统一队列`;
    renderLogs($("#logs"), [`统一任务 ${task.task_id} 已排队。`]);
    renderPipeline(0);
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: task.task_id}}));
    pollOcrState();
  } catch (error) {
    renderLogs($("#logs"), [`加入任务失败: ${error.message}`]);
  } finally {
    // The queue, rather than the form, owns task lifetime. Re-enable as soon
    // as the enqueue request finishes so another report can be queued.
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
    document_label: $("#document-label").value.trim() || null,
    external_document_id: $("#external-document-id").value.trim() || null,
    render_dpi: Number($("#render-dpi").value || 144),
    execute_vlm_reviews: $("#execute-vlm").checked,
    review_provider: selectedReviewProvider(),
    qiniu_api_key: $("#qiniu-key").value.trim() || null,
  };
  try {
    const task = await postJson("/api/pipeline/tasks/document-ir", {
      operation: "build",
      build_request: payload,
    });
    activeIrRunId = task.native_job_id;
    $("#current-ir-run").textContent = `${activeIrRunId} · 已加入统一队列`;
    renderLogs($("#ir-logs"), [`统一任务 ${task.task_id} 已排队。`]);
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: task.task_id}}));
    renderPipeline(1);
    pollIrState();
  } catch (error) {
    renderLogs($("#ir-logs"), [`加入任务失败: ${error.message}`]);
  } finally {
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
  $("#current-run").textContent = ocrRunStatusText(state);
  if (state.status === "done") {
    $("#ocr-form button[type=submit]").disabled = false;
    renderPipeline(1);
    await loadOcrRun(state.run_id);
    await refreshHistories();
    return;
  }
  if (["failed", "cancelled", "interrupted"].includes(state.status)) {
    $("#ocr-form button[type=submit]").disabled = false;
    renderLogs($("#logs"), [...(state.logs || []), `失败: ${state.error || state.message}`]);
    await loadOcrRun(state.run_id);
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
  if (["failed", "cancelled", "interrupted"].includes(state.status)) {
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
  const [state, manifest, artifacts] = await Promise.all([
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}`),
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}/manifest`),
    fetchJson(`/api/ocr/jobs/${encodeURIComponent(runId)}/artifacts`, {files: []}),
  ]);
  if (requestBackend !== backendUrl()) return;
  if (state) {
    $("#current-run").textContent = ocrRunStatusText(state);
    renderLogs($("#logs"), state.logs || []);
  }
  setJson($("#manifest"), manifest);
  const preflightArtifact = (artifacts.files || []).find((item) => item.path === "source/preflight.json");
  const preflight = preflightArtifact
    ? await fetchJson(preflightArtifact.url, state?.summary?.pdf_preflight || manifest?.pdf_preflight || {})
    : (state?.summary?.pdf_preflight || manifest?.pdf_preflight || {});
  if (requestBackend !== backendUrl()) return;
  setJson($("#ocr-preflight"), preflight);
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
  select.replaceChildren(new Option("新建独立 IR 分支（r1）", ""));
  revisions.forEach((item) => select.add(new Option(`延续 ${shortIdentity(item.lineage_id)} · r${item.ir_revision} · ${item.readiness}`, item.run_id)));
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
    setIrWorkspaceAvailable(false);
    return;
  }
  setIrWorkspaceAvailable(true);
  renderOverview(manifest, validation, document, state?.summary || {});
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
  await renderRevisions(manifest?.document_id || state?.summary?.document_id, manifest?.ocr_run_id);
}

function renderOverview(manifest, validation, document, summary = {}) {
  const readiness = manifest?.readiness || document.readiness || "unknown";
  const badge = $("#readiness-badge");
  badge.className = `readiness ${readiness}`;
  badge.textContent = `${readiness.toUpperCase()} · r${manifest?.ir_revision || 1}`;
  const identity = $("#document-identity");
  identity.replaceChildren(
    documentNode("strong", "", manifest?.document_label || document.metadata?.document_label || summary.document_label || "未命名 PDF"),
    documentNode("span", "", `Document ${shortIdentity(manifest?.document_id || document.metadata?.document_id || summary.document_id)}`),
    documentNode("span", "", `Lineage ${shortIdentity(manifest?.lineage_id || document.metadata?.lineage_id || summary.lineage_id)} · r${manifest?.ir_revision || 1}`),
    documentNode("span", "", `OCR ${shortIdentity(manifest?.ocr_run_id)}`),
  );
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
  const evidencePolicy = currentManifest?.evidence_policy || {};
  if (evidencePolicy.mode === "limited") {
    root.appendChild(documentNode(
      "div", "issue warning",
      `证据受限：可用 ${evidencePolicy.available_page_count || 0} 页；排除 PDF 页码 ${(evidencePolicy.excluded_page_indices || []).map((index) => Number(index) + 1).join("、") || "未记录"}。排除范围不能当作未披露。`,
    ));
  }
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
  const missingTables = (irDocument?.tables || []).filter((table) => !table.bbox);
  missingTables.forEach((table) => {
    const row = documentNode("div", "issue error");
    const locate = documentNode("button", "secondary", `定位并准备修复 ${table.table_id} · PDF 第 ${Number(table.page_index) + 1} 页`);
    locate.type = "button";
    locate.addEventListener("click", () => {
      $("#repair-targets").value = table.table_id;
      $("#repair-reason").value = "table_geometry_missing";
      $("#page-select").value = String(table.page_index);
      renderPage(Number(table.page_index));
      $("#repair-targets").scrollIntoView({block: "center", behavior: "smooth"});
    });
    row.appendChild(locate);
    root.appendChild(row);
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
    [counts.validation_blocked || 0, "验证问题待修复"],
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
  if (blocking) blocking.textContent = reviewRetryInFlight ? "正在提交…" : "将阻塞复核加入任务";
  if (optional) optional.textContent = reviewRetryInFlight ? "正在提交…" : "将非阻塞增强加入任务";
}

function firstAvailableReviewGroup(inbox) {
  const groups = inbox?.groups || {};
  return ["human_required", "system_blocked", "blocking_deferred", "optional_deferred", "resolved"]
    .find((name) => (groups[name] || []).length) || "human_required";
}

function renderReviewInbox(groupName) {
  activeReviewGroup = groupName;
  $("#review-queue-title").textContent = reviewGroupLabel(groupName);
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
      if ((details.recognized_aliases || []).length) {
        lines.push(`检测到可确定转换的字段别名：${details.recognized_aliases.join("、")}`);
      }
      if (details.adapter_contract_mismatch) {
        lines.push("应由本地 Adapter 归一化后重新执行 Guard，不应继续原样调用模型。");
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
    const ownerLabel = {
      model: "模型候选",
      system: "本地链路/合同",
      evidence: "证据输入",
      service: "模型服务",
      none: "无",
    }[guidance.failure_owner] || guidance.failure_owner || "unknown";
    const classLabel = {
      repeated_failure: "同语义候选连续未通过 Guard",
      system_contract: "本地合同或应用错误",
      evidence_missing: "证据缺失",
      model_protocol: "模型输出协议不合规",
      model_service: "模型服务故障",
      rate_limit: "服务额度限制",
      verifier_disagreement: "Verifier 分歧",
      semantic_ambiguity: "内容语义存在歧义",
      scheduler_deferred: "调度延期",
    }[guidance.failure_class] || guidance.failure_class;
    root.appendChild(documentNode(
      "div",
      `failure-owner ${guidance.retryable ? "retryable" : "blocked"}`,
      `失败归属：${ownerLabel} (${guidance.failure_owner || "unknown"}) · ${classLabel} (${guidance.failure_class}) · ${guidance.retryable ? "允许自动重试" : "禁止原样重试"}${guidance.failure_fingerprint ? ` · ${guidance.failure_fingerprint}` : ""}`,
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
  if (task.blocking && !["auto_resolved", "reviewed", "done"].includes(task.status)) {
    const limited = documentNode("button", "secondary", "隔离问题页面，继续其余页面抽取");
    limited.type = "button";
    limited.addEventListener("click", () => resolveHumanTask(task, "continue_limited"));
    root.appendChild(limited);
    root.appendChild(documentNode("div", "decision-note", "创建受限子版本；不接受失败补丁，也不将未解决任务标为通过。排除范围和缺漏会保留。全局完整性错误仍不可绕过。"));
  }

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
  renderReviewWorklist();
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
  const actionText = {
    keep_current: "保留当前 IR",
    confirm_spread: "确认两页左右拼接",
    reject_spread: "确认两页相互独立",
    continue_limited: "隔离未解决问题涉及的页面，让其余可用页面继续抽取（不会标记复核通过）",
  }[action] || action;
  if (action === "continue_limited" && !notes) return alert("请在人工备注中说明受限继续的原因。");
  if (!window.confirm(`${actionText}，并为任务 ${task.task_id} 创建新修订吗？`)) return;
  try {
    const manifest = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/review-tasks/${encodeURIComponent(task.task_id)}/decision`, {
      action,
      decided_by: $("#human-operator").value.trim() || "local-operator",
      notes: notes || null,
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
    const parentRunId = activeIrRunId;
    const task = await postJson("/api/pipeline/tasks/document-ir", {
      operation: "review_retry",
      parent_run_id: parentRunId,
      review_retry_request: {
        task_ids: taskIds,
        include_optional: includeOptional,
        max_auto_review_rounds: 3,
        requested_by: $("#human-operator").value.trim() || "local-operator",
        notes: $("#human-notes").value.trim() || null,
        review_provider: selectedReviewProvider(),
        qiniu_api_key: $("#qiniu-key").value.trim() || null,
      },
    });
    activeIrRunId = task.native_job_id;
    $("#current-ir-run").textContent = `${activeIrRunId} · 已加入统一队列`;
    renderLogs($("#ir-logs"), [`统一任务 ${task.task_id} 已排队。`]);
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: task.task_id}}));
    pollIrState();
  } catch (error) {
    const retryAfter = error.detail?.provider_state?.retry_after_seconds;
    const suffix = retryAfter ? `\n预计 ${formatDuration(Number(retryAfter))} 后可再次尝试，或更换独立额度的 API Key。` : "";
    alert(`自动审核重试未启动：${error.message}${suffix}`);
  } finally {
    setReviewRetryInFlight(false);
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

async function renderRevisions(documentId, ocrRunId) {
  const root = $("#revision-list"); root.replaceChildren();
  if (!documentId && !ocrRunId) return;
  const requestBackend = backendUrl();
  const revisions = documentId
    ? await fetchJson(`/api/document-ir/documents/${encodeURIComponent(documentId)}/revisions`, [])
    : await fetchJson(`/api/document-ir/revisions/${encodeURIComponent(ocrRunId)}`, []);
  if (requestBackend !== backendUrl()) return;
  let lastLineage = null;
  revisions.forEach((item) => {
    if (item.lineage_id !== lastLineage) {
      root.appendChild(documentNode("span", "revision-lineage", `分支 ${shortIdentity(item.lineage_id)}`));
      lastLineage = item.lineage_id;
    }
    const chip = documentNode("button", "revision", `r${item.ir_revision} · ${item.readiness}`);
    chip.type = "button";
    chip.title = item.run_id;
    chip.addEventListener("click", () => loadIrRun(item.run_id));
    root.appendChild(chip);
  });
  await loadRevisionOptions(ocrRunId);
}

function renderArtifactLinks(root, files, kind, previewTarget = null) {
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
    items.forEach((item) => list.appendChild(artifactLink(item, kind, previewTarget)));
    section.appendChild(list);
    root.appendChild(section);
  });
}

function artifactLink(item, kind, previewTarget = null) {
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
        if (previewTarget) previewTarget.textContent = rendered;
        else if (kind === "ocr" && item.path.endsWith(".md")) $("#markdown-preview").textContent = rendered;
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
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

$("#page-select").addEventListener("change", (event) => renderPage(Number(event.target.value)));
$("#table-select").addEventListener("change", (event) => renderTable(event.target.value));
$("#ocr-history").addEventListener("change", (event) => event.target.value && loadOcrRun(event.target.value));
$("#ir-history").addEventListener("change", (event) => event.target.value && loadIrRun(event.target.value));
$("#ocr-run-id").addEventListener("change", (event) => {
  const runId = event.target.value.trim();
  const known = irSourceOcrRuns.some((item) => item.run_id === runId);
  if (known) selectIrSource(runId);
  else {
    selectedIrSourceRunId = runId;
    if (runId) loadRevisionOptions(runId);
    renderIrSourcePicker();
  }
});
$("#ir-source-search").addEventListener("input", renderIrSourcePicker);
$("#ir-source-filter").addEventListener("change", renderIrSourcePicker);
$("#ir-source-refresh").addEventListener("click", refreshHistories);
$("#backend-url").addEventListener("change", () => {
  localStorage.setItem("esg-v2-backend-url", backendUrl());
  refreshHistories();
  refreshOcrProviderStatus();
});
$("#refresh-all").addEventListener("click", () => {
  refreshHistories();
  refreshOcrProviderStatus();
});
$("#refresh-models").addEventListener("click", refreshModelStatus);
$("#ocr-provider").addEventListener("change", () => {
  syncOcrProviderControls();
  refreshOcrProviderStatus();
});
$("#refresh-storage").addEventListener("click", refreshHistories);
$("#storage-search").addEventListener("input", () => renderStorageInventory(storageInventory));
$("#storage-filter").addEventListener("change", () => renderStorageInventory(storageInventory));
$("#storage-management-mode").addEventListener("change", (event) => {
  if (!event.target.checked) selectedStorageTargets.clear();
  renderStorageInventory(storageInventory);
});
$("#plan-storage-deletion").addEventListener("click", () => openDeletionPlan(
  [...selectedStorageTargets.values()],
  $("#storage-cascade").checked,
));
$("#cleanup-incomplete").addEventListener("click", () => openDeletionPlan(
  [{kind: "incomplete", target_id: "all"}],
  false,
));
$("#execute-storage-deletion").addEventListener("click", executeStorageDeletion);
$("#deletion-dialog").addEventListener("close", () => {
  currentDeletionPlan = null;
  currentDeletionTargets = [];
});
document.querySelectorAll("[data-app-view-target]").forEach((button) => {
  button.addEventListener("click", () => switchAppView(button.dataset.appViewTarget));
});
$("#review-provider").addEventListener("change", () => {
  syncReviewProviderControls();
  refreshModelStatus();
});
$("#review-worklist-refresh").addEventListener("click", () => refreshReviewWorklist());
$("#review-worklist-search").addEventListener("input", renderReviewWorklist);
$("#review-worklist-group").addEventListener("change", renderReviewWorklist);
$("#review-worklist-impact").addEventListener("change", renderReviewWorklist);
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
    const parentRunId = activeIrRunId;
    const task = await postJson("/api/pipeline/tasks/document-ir", {
      operation: "repair",
      repair_request: {
        parent_ir_run_id: parentRunId,
        target_ids: targetIds,
        reason_code: $("#repair-reason").value.trim() || "downstream_document_ir_mismatch",
        requested_by: $("#repair-requester").value.trim() || "local-operator",
        notes: $("#human-notes").value.trim() || null,
        execute_vlm_reviews: $("#repair-execute-vlm").checked,
        review_provider: selectedReviewProvider(),
        qiniu_api_key: $("#qiniu-key").value.trim() || null,
      },
    });
    activeIrRunId = task.native_job_id;
    $("#current-ir-run").textContent = `${activeIrRunId} · 已加入统一队列`;
    renderLogs($("#ir-logs"), [`统一任务 ${task.task_id} 已排队。`]);
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: task.task_id}}));
    pollIrState();
  } catch (error) {
    alert(`返修启动失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

const storedBackend = localStorage.getItem("esg-v2-backend-url");
if (storedBackend) $("#backend-url").value = storedBackend;
syncReviewProviderControls();
syncOcrProviderControls();
switchAppView(localStorage.getItem("esg-v2-active-view") || "report-assets", {scroll: false});
renderPipeline();
refreshHistories();
refreshModelStatus();
refreshOcrProviderStatus();
