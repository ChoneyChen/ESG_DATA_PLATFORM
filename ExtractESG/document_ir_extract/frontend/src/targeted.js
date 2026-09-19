import {
  readinessName,
  reportIdentity,
  runDate,
  searchableText,
} from "./selection.js?v=input-selectors-v3-20260829";
function metricSortDescription(metric) {
  return `后端统一组织：${metric.logical_measurement_count ?? metric.facts.length} 个逻辑测量；保留全部原始展示及证据`;
}
const $ = (selector) => document.querySelector(selector);

function savedTargetMetricSelections() {
  try {
    const payload = JSON.parse(localStorage.getItem("esg-targeted-metric-selections") || "{}");
    return new Map(Object.entries(payload).map(([key, values]) => [
      key,
      new Set(Array.isArray(values) ? values.filter((value) => typeof value === "string") : []),
    ]));
  } catch (_) {
    return new Map();
  }
}

const targetedState = {
  backend: localStorage.getItem("esg-targeted-backend-url") || "http://127.0.0.1:18180",
  config: null,
  irRuns: [],
  standards: [],
  standardCatalog: null,
  standard: null,
  standardDetails: new Map(),
  metricSelections: savedTargetMetricSelections(),
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

const targetedStages = [
  ["01", "输入准入", "固定 IR revision 与标准包 digest"],
  ["02", "原始证据清单", "Block、Cell、Header、Literal 与 crop"],
  ["03", "对象级混合检索", "BM25 + Qwen3 Embedding + FactPattern；任务可选 Top 1–5"],
  ["04", "单对象证据区域", "表格、图表、文本区域分别调用；每次最多一张 crop"],
  ["05", "模型直接填表", "标准包动态字段合同；一个量一行、多个量多行"],
  ["06", "来源与合同校验", "字段、证据、同组坐标、类型与去重"],
  ["07", "成果包", "Core records + XLSX/CSV/JSON"],
];

function docBase() {
  return ($("#backend-url")?.value || "http://127.0.0.1:18080").replace(/\/$/, "");
}

async function txRequest(path, options = {}, timeoutMs = 0) {
  const controller = timeoutMs ? new AbortController() : null;
  const timeout = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(`${targetedState.backend}${path}`, controller ? {...options, signal: controller.signal} : options);
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
      } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error(`连接 ${targetedState.backend} 超时`);
    throw error;
  } finally {
    if (timeout !== null) window.clearTimeout(timeout);
  }
}

async function docRequest(path, options = {}) {
  const response = await fetch(`${docBase()}${path}`, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusLabel(value) {
  return {
    queued: "排队中", running: "运行中", completed: "已完成", partial: "部分完成",
    failed: "失败", cancelled: "已取消", interrupted: "已中断", found: "已找到",
    not_found: "未找到", ambiguous: "不确定", pending: "处理中", reported: "直接披露",
    not_found_after_complete_search: "完整检索后未找到", auto_verified: "来源与合同已核对",
    human_required: "需人工判断", system_contract_failed: "结果合同失败",
    passed: "通过", warning: "警告",
  }[value] || value || "未产生";
}

function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function elapsedSeconds(job) {
  const start = Date.parse(job?.created_at || "");
  const end = ["queued", "running"].includes(job?.status) ? Date.now() : Date.parse(job?.updated_at || "");
  return Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, Math.round((end - start) / 1000)) : 0;
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
  if (!metric.facts?.length) return metric.uncertainty_reason || (metric.status === "not_found" ? "完整检索后未发现可写入事实" : "尚未产生事实记录");
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
  return Promise.all([
    txRequest("/api/health", {}, 2500),
    txRequest("/api/config", {}, 2500),
    txRequest("/api/ir-runs", {}, 2500),
    txRequest("/api/standards", {}, 2500),
    txRequest("/api/standards/diagnostics", {}, 2500).catch((error) => ({error: error.message})),
    txRequest("/api/result-catalog", {}, 2500).catch((error) => ({error: error.message})),
  ]);
}

async function connectTargeted({allowDiscovery = false} = {}) {
  const requestedBackend = ($("#tx-backend-url")?.value || targetedState.backend).replace(/\/$/, "");
  targetedState.backend = requestedBackend;
  const pill = $("#tx-connection");
  pill.textContent = "连接中";
  try {
    let discovered = false;
    let bootstrap;
    try {
      bootstrap = await loadTargetedBootstrap();
    } catch (initialError) {
      if (!allowDiscovery) throw initialError;
      const status = await docRequest("/api/pipeline/status");
      const candidate = status.services?.targeted_backend?.url?.replace(/\/$/, "");
      if (!status.services?.targeted_backend?.available || !candidate || candidate === requestedBackend) {
        throw initialError;
      }
      targetedState.backend = candidate;
      bootstrap = await loadTargetedBootstrap();
      discovered = true;
    }
    const [health, config, irRuns, standards, standardCatalog, resultCatalog] = bootstrap;
    localStorage.setItem("esg-targeted-backend-url", targetedState.backend);
    $("#tx-backend-url").value = targetedState.backend;
    targetedState.config = config;
    targetedState.irRuns = irRuns;
    targetedState.standards = standards;
    targetedState.standardCatalog = standardCatalog;
    targetedState.resultCatalog = resultCatalog;
    pill.textContent = discovered ? "后端已连接 · 地址已自动恢复" : "后端已连接";
    pill.className = "status-pill success";
    renderTargetHealth(health);
    renderTargetProviderConfig();
    renderTargetInputs();
    await refreshSemanticIndexes();
    await refreshTargetJobs();
  } catch (error) {
    pill.textContent = "后端未连接";
    pill.className = "status-pill error";
    $("#tx-health-grid").innerHTML = `<div class="empty-state error">请确认定向抽取后端 18180 已启动：${esc(error.message)}</div>`;
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
      await refreshSemanticIndexes();
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
    [health.models?.qiniu_vlm?.configured ? "已配置" : "未配置", "七牛云 VLM"],
  ];
  $("#tx-health-grid").innerHTML = cards.map(([value, label]) => `<article><strong>${esc(value)}</strong><span>${label}</span></article>`).join("");
}

function selectedProviderConfig() {
  const providerId = $("#tx-semantic-provider")?.value || "local_nuextract";
  return (targetedState.config?.semantic_providers || []).find((item) => item.id === providerId) || {
    id: providerId,
    label: providerId === "qiniu_vlm" ? "七牛云视觉语言模型" : "本地 NuExtract3",
    profile: providerId === "qiniu_vlm" ? "direct_multi_row_semantic_fill_single_visual_object" : "direct_multi_row_semantic_fill",
    default_model: providerId === "qiniu_vlm" ? "qwen3.5-397b-a17b" : "NuExtract3-mlx-4bits",
  };
}

function renderTargetProviderConfig() {
  const select = $("#tx-semantic-provider");
  const modelInput = $("#tx-semantic-model");
  if (!select || !modelInput) return;
  const providers = targetedState.config?.semantic_providers || [];
  const savedProvider = localStorage.getItem("esg-targeted-semantic-provider") || "local_nuextract";
  if (providers.length) {
    select.innerHTML = providers.map((item) =>
      `<option value="${esc(item.id)}">${esc(item.label)}${item.id === "qiniu_vlm" && item.configured === false ? "（未配置密钥）" : ""}</option>`
    ).join("");
  }
  if ([...select.options].some((option) => option.value === savedProvider)) select.value = savedProvider;
  const provider = selectedProviderConfig();
  const topNControl = $("#tx-retrieval-object-top-n");
  if (topNControl) {
    const configuredDefault = Number(targetedState.config?.retrieval_objects?.default_top_n || 3);
    const savedTopN = Number(localStorage.getItem("esg-targeted-retrieval-object-top-n") || configuredDefault);
    topNControl.value = String(Math.min(5, Math.max(1, savedTopN)));
  }
  const savedCloudModel = localStorage.getItem("esg-targeted-qiniu-model") || "";
  modelInput.value = provider.id === "qiniu_vlm" ? (savedCloudModel || provider.default_model || "") : (provider.default_model || "NuExtract3-mlx-4bits");
  modelInput.readOnly = provider.id !== "qiniu_vlm";
  modelInput.classList.toggle("readonly", modelInput.readOnly);
  $("#tx-qiniu-key").disabled = provider.id !== "qiniu_vlm";
  $("#tx-qiniu-key-field").classList.toggle("muted", provider.id !== "qiniu_vlm");
  const cloud = provider.id === "qiniu_vlm";
  $("#tx-provider-profile").innerHTML = cloud
    ? `<strong>云端单对象直接填表</strong><span>按 Top N 选取独立证据对象；每次只发送一张表、一个图或一个文本区域及最多 ${esc(targetedState.config?.cloud_packet?.max_images || 1)} 张对应 crop，最后统一合并完全重复行。</span>`
    : "<strong>本地单对象分区填表</strong><span>按 Top N 选取独立证据对象；每次只处理一张表、一个图或一个文本区域。大表仅按连续行分区，全部调用完成后统一合并完全重复行。</span>";
}

function renderTargetInputs() {
  const selectedIr = targetedState.irRuns.find(
    (run) => run.run_id === targetedState.selectedIrRunId && run.can_build_evidence
  );
  if (!selectedIr) {
    targetedState.selectedIrRunId = "";
    localStorage.removeItem("esg-targeted-ir-run-id");
  }
  $("#tx-ir-select").innerHTML = '<option value="">未选择</option>' + targetedState.irRuns.map((run) =>
    `<option value="${esc(run.run_id)}">${esc(run.document_label || run.run_id)} · r${run.ir_revision}</option>`
  ).join("");
  $("#tx-ir-select").value = targetedState.selectedIrRunId;
  renderTargetIrPicker();

  const selectedStandard = localStorage.getItem("esg-targeted-standard-package") || "";
  $("#tx-standard-select").innerHTML = targetedState.standards.map((item) =>
    `<option value="${esc(`${item.package_id}@${item.package_version}`)}">${esc(item.disclosure_requirement || item.package_id)} · v${esc(item.package_version)} · ${item.metric_count} 个指标</option>`
  ).join("") || '<option value="">没有编译标准包</option>';
  if ([...$("#tx-standard-select").options].some((option) => option.value === selectedStandard)) {
    $("#tx-standard-select").value = selectedStandard;
  }
  const availableStandardKeys = new Set(
    targetedState.standards.map((item) => `${item.package_id}@${item.package_version}`)
  );
  for (const key of targetedState.metricSelections.keys()) {
    if (!availableStandardKeys.has(key)) targetedState.metricSelections.delete(key);
  }
  persistTargetMetricSelections();
  renderTargetStandardPicker();
  renderTargetBatchSelection();
  loadTargetStandard();
}

function standardKey(item) {
  return `${item.package_id}@${item.package_version}`;
}

function activeStandardKey() {
  return $("#tx-standard-select")?.value || "";
}

function selectedMetricsFor(key) {
  if (!targetedState.metricSelections.has(key)) {
    targetedState.metricSelections.set(key, new Set());
  }
  return targetedState.metricSelections.get(key);
}

function persistTargetMetricSelections() {
  const available = new Set(targetedState.standards.map(standardKey));
  const payload = {};
  for (const [key, values] of targetedState.metricSelections.entries()) {
    if (available.has(key) && values.size) payload[key] = [...values];
  }
  localStorage.setItem("esg-targeted-metric-selections", JSON.stringify(payload));
}

function renderTargetStandardPicker() {
  const root = $("#tx-standard-picker");
  if (!root) return;
  const selected = $("#tx-standard-select").value;
  const diagnostics = targetedState.standardCatalog || {};
  const invalidCount = Number(diagnostics.invalid_package_count || 0);
  $("#tx-standard-count").textContent = `${targetedState.standards.length} 个有效包${invalidCount ? ` · ${invalidCount} 个校验失败` : ""}`;
  root.innerHTML = targetedState.standards.map((item) => {
    const value = standardKey(item);
    const selectedCount = selectedMetricsFor(value).size;
    return `<button type="button" class="standard-package-card ${value === selected ? "selected" : ""} ${selectedCount ? "included" : ""}" data-tx-standard="${esc(value)}" aria-pressed="${value === selected}">
      <div><strong>${esc(item.disclosure_requirement || item.package_id)}</strong><span class="standard-selection-count">已选 ${selectedCount} / ${Number(item.metric_count || 0)}</span></div>
      <span>v${esc(item.package_version)} · ${Number(item.metric_count || 0)} 个指标 · ${Number(item.element_count || 0)} Elements</span>
      <code>${esc(item.package_id)}</code>
    </button>`;
  }).join("") || `<div class="empty-state error">没有通过校验的编译标准包${diagnostics.error ? `：${esc(diagnostics.error)}` : "。"}</div>`;
  document.querySelectorAll("[data-tx-standard]").forEach((button) => button.addEventListener("click", () => {
    $("#tx-standard-select").value = button.dataset.txStandard;
    loadTargetStandard();
  }));
}

function renderTargetBatchSelection() {
  const root = $("#tx-batch-selection-summary");
  const button = $("#tx-add-task");
  if (!root || !button) return;
  const selectedPackages = targetedState.standards.map((item) => ({
    item,
    key: standardKey(item),
    count: selectedMetricsFor(standardKey(item)).size,
  })).filter((entry) => entry.count > 0);
  const metricCount = selectedPackages.reduce((sum, entry) => sum + entry.count, 0);
  if (!selectedPackages.length) {
    root.className = "batch-selection-summary empty-state";
    root.textContent = "尚未从任何标准包选择指标。切换上方卡片后逐项勾选，选择会跨卡片保留。";
    button.textContent = "请先选择指标";
    button.disabled = true;
    return;
  }
  root.className = "batch-selection-summary";
  root.innerHTML = `<strong>将创建 ${selectedPackages.length} 个任务 · 共 ${metricCount} 个指标</strong>${selectedPackages.map(({item, key, count}) =>
    `<button type="button" data-tx-summary-standard="${esc(key)}">${esc(item.disclosure_requirement || item.package_id)} · ${count}</button>`
  ).join("")}`;
  document.querySelectorAll("[data-tx-summary-standard]").forEach((item) => item.addEventListener("click", () => {
    $("#tx-standard-select").value = item.dataset.txSummaryStandard;
    loadTargetStandard();
  }));
  button.textContent = `加入 ${selectedPackages.length} 个定向抽取任务`;
  button.disabled = false;
}

function selectTargetIr(runId) {
  const run = targetedState.irRuns.find((item) => item.run_id === runId);
  if (!run?.can_build_evidence) return;
  targetedState.selectedIrRunId = runId;
  localStorage.setItem("esg-targeted-ir-run-id", runId);
  $("#tx-ir-select").value = runId;
  renderTargetIrPicker();
}

function renderTargetIrPicker() {
  const root = $("#tx-ir-picker");
  const selectedRoot = $("#tx-ir-selected");
  if (!root || !selectedRoot) return;
  const query = $("#tx-ir-search").value.trim().toLocaleLowerCase("zh-CN");
  const filter = $("#tx-ir-filter").value;
  const readyCount = targetedState.irRuns.filter((run) => run.can_build_evidence).length;
  const visible = [...targetedState.irRuns].sort((a, b) =>
    Number(b.manifest_mtime || 0) - Number(a.manifest_mtime || 0)
  ).filter((run) => {
    const matchesFilter = filter === "all"
      || (filter === "ready" && run.can_build_evidence)
      || (filter === "unready" && !run.can_build_evidence);
    const haystack = searchableText(
      run.run_id,
      run.document_label,
      run.document_id,
      run.readiness,
      run.schema_version,
      run.ir_revision,
    );
    return matchesFilter && (!query || haystack.includes(query));
  });
  $("#tx-ir-count").textContent = `${visible.length} 条匹配 · ${readyCount} 个可抽取`;

  const selected = targetedState.irRuns.find((run) => run.run_id === targetedState.selectedIrRunId);
  if (!selected) {
    selectedRoot.className = "selected-input-summary empty-state";
    selectedRoot.textContent = "尚未选择可抽取的 Document IR。请从下方报告卡片中选择。";
  } else {
    const identity = reportIdentity(selected.document_label, selected.run_id);
    selectedRoot.className = "selected-input-summary selected";
    selectedRoot.innerHTML = `<div class="selected-input-title"><span class="year-badge">${esc(identity.year)}</span><strong title="${esc(identity.displayName)}">${esc(identity.displayName)}</strong><span class="status-pill success">已选输入</span></div>
      <div class="selected-input-facts"><span><strong>r${selected.ir_revision}</strong><small>IR Revision</small></span><span><strong>${selected.page_count} 页</strong><small>报告页数</small></span><span><strong>${esc(readinessName(selected.readiness))}</strong><small>准入状态</small></span><span><strong>${esc(selected.schema_version)}</strong><small>Schema</small></span></div>
      <code>${esc(selected.run_id)}</code>`;
  }

  root.innerHTML = visible.map((run) => {
    const identity = reportIdentity(run.document_label, run.run_id);
    const available = run.can_build_evidence;
    return `<button class="input-choice-card ${run.run_id === targetedState.selectedIrRunId ? "selected" : ""} ${available ? "" : "unavailable"}" data-tx-ir-run="${esc(run.run_id)}" type="button" ${available ? "" : "disabled"} title="${available ? "选择该 IR" : "该 IR 尚未通过 Evidence 准入"}">
      <div class="input-choice-head"><div class="input-choice-name"><span class="year-badge">${esc(identity.year)}</span><strong title="${esc(identity.displayName)}">${esc(identity.displayName)}</strong></div><span class="status-pill ${available ? "success" : "neutral"}">${esc(readinessName(run.readiness))}</span></div>
      <div class="input-choice-facts"><span>Revision r${run.ir_revision}</span><span>${run.page_count} 页</span><span>${esc(run.schema_version)}</span><span>${esc(runDate(run.run_id, run.manifest_mtime))}</span></div>
      <code>${esc(run.run_id)}</code>${available ? "" : '<small class="choice-block-reason">需先完成 IR 修复/复核，当前不可加入抽取任务</small>'}
    </button>`;
  }).join("") || '<div class="empty-state">没有符合搜索和准入筛选的 Document IR。</div>';
  document.querySelectorAll("[data-tx-ir-run]").forEach((button) => {
    if (!button.disabled) button.addEventListener("click", () => selectTargetIr(button.dataset.txIrRun));
  });
}

async function loadTargetStandard() {
  const value = $("#tx-standard-select").value;
  renderTargetStandardPicker();
  if (!value) {
    targetedState.standard = null;
    $("#tx-standard-detail").className = "selected-input-summary empty-state";
    $("#tx-standard-detail").textContent = "没有可选择的有效标准包。";
    $("#tx-metric-list").className = "metric-picker empty-state";
    $("#tx-metric-list").textContent = "等待有效标准包。";
    $("#tx-metric-count").textContent = "已选 0 / 0";
    return;
  }
  const split = value.lastIndexOf("@");
  try {
    const standard = targetedState.standardDetails.get(value)
      || await txRequest(`/api/standards/${encodeURIComponent(value.slice(0, split))}/${encodeURIComponent(value.slice(split + 1))}`);
    if ($("#tx-standard-select").value !== value) return;
    targetedState.standardDetails.set(value, standard);
    targetedState.standard = standard;
    localStorage.setItem("esg-targeted-standard-package", value);
    const catalog = targetedState.standards.find((item) => `${item.package_id}@${item.package_version}` === value) || {};
    const validMetricIds = new Set(standard.metrics.map((metric) => metric.metric_id));
    const selectedMetricIds = new Set(
      [...selectedMetricsFor(value)].filter((metricId) => validMetricIds.has(metricId))
    );
    targetedState.metricSelections.set(value, selectedMetricIds);
    persistTargetMetricSelections();
    renderTargetStandardPicker();
    renderTargetBatchSelection();
    $("#tx-standard-detail").className = "selected-input-summary selected standard-summary";
    const digest = String(targetedState.standard.compilation?.source_digest || catalog.source_digest || "unknown");
    $("#tx-standard-detail").innerHTML = `<div class="selected-input-title"><strong>${esc(catalog.disclosure_requirement || catalog.package_id || value)}</strong><span class="status-pill neutral">${esc(catalog.status || "unknown")}</span></div><div class="selected-input-facts"><span><strong id="tx-active-standard-selected-count">${selectedMetricIds.size} / ${targetedState.standard.metrics.length}</strong><small>已选指标</small></span><span><strong>${catalog.element_count || targetedState.standard.elements?.length || 0}</strong><small>Elements</small></span><span><strong>${catalog.concept_count || targetedState.standard.concepts?.length || 0}</strong><small>语义概念</small></span><span><strong>v${esc(catalog.package_version || value.slice(split + 1))}</strong><small>版本</small></span><span><strong>${esc(digest.slice(0, 12))}…</strong><small>Digest</small></span></div><code>${esc(catalog.package_id || value.slice(0, split))}</code>`;
    $("#tx-metric-list").className = "metric-picker";
    $("#tx-metric-list").innerHTML = targetedState.standard.metrics.map((metric) =>
      `<label class="metric-item" data-metric-search="${esc(searchableText(metric.metric_id, metric.source_datapoint_id, metric.labels?.zh, metric.labels?.en, metric.data_class))}"><input type="checkbox" ${selectedMetricIds.has(metric.metric_id) ? "checked" : ""} value="${esc(metric.metric_id)}" /><span><strong>${esc(metric.source_datapoint_id)}</strong>${esc(metric.labels?.zh || metric.labels?.en || metric.metric_id)}</span><small>${esc(metric.data_class)}</small></label>`
    ).join("");
    filterTargetMetrics();
    updateMetricSelectionCount();
  } catch (error) {
    $("#tx-metric-list").className = "metric-picker empty-state";
    $("#tx-metric-list").textContent = `标准包读取失败：${error.message}`;
    $("#tx-metric-count").textContent = "已选 0 / 0";
  }
}

function filterTargetMetrics() {
  const query = $("#tx-metric-search").value.trim().toLocaleLowerCase("zh-CN");
  document.querySelectorAll("#tx-metric-list .metric-item").forEach((item) => {
    item.classList.toggle("hidden", Boolean(query) && !item.dataset.metricSearch.includes(query));
  });
}

function updateMetricSelectionCount() {
  const all = [...document.querySelectorAll("#tx-metric-list input[type=checkbox]")];
  const selectedIds = all.filter((input) => input.checked).map((input) => input.value);
  const key = activeStandardKey();
  if (key) targetedState.metricSelections.set(key, new Set(selectedIds));
  persistTargetMetricSelections();
  $("#tx-metric-count").textContent = `当前标准已选 ${selectedIds.length} / ${all.length}`;
  if ($("#tx-active-standard-selected-count")) {
    $("#tx-active-standard-selected-count").textContent = `${selectedIds.length} / ${all.length}`;
  }
  renderTargetStandardPicker();
  renderTargetBatchSelection();
}

async function addTargetedTask() {
  updateMetricSelectionCount();
  const selections = targetedState.standards.map((item) => ({
    item,
    key: standardKey(item),
    metricIds: [...selectedMetricsFor(standardKey(item))],
  })).filter((entry) => entry.metricIds.length > 0);
  if (!$("#tx-ir-select").value || !selections.length) {
    $("#tx-launch-message").textContent = "请选择 IR，并从至少一个标准包勾选指标。";
    return;
  }
  const button = $("#tx-add-task");
  const requestedProvider = $("#tx-semantic-provider").value;
  const requestedModel = requestedProvider === "qiniu_vlm"
    ? ($("#tx-semantic-model").value.trim() || null)
    : null;
  const topNControl = $("#tx-retrieval-object-top-n");
  const retrievalObjectTopN = Number(topNControl.value);
  if (!Number.isInteger(retrievalObjectTopN) || retrievalObjectTopN < 1 || retrievalObjectTopN > 5) {
    $("#tx-launch-message").textContent = "检索证据对象 Top N 必须在 1–5 之间。";
    topNControl.reportValidity();
    return;
  }
  button.disabled = true;
  try {
    const sharedRequest = {
      ir_run_id: $("#tx-ir-select").value,
      semantic_search: $("#tx-semantic-search").checked,
      semantic_fill: $("#tx-semantic-fill").checked,
      semantic_provider: requestedProvider,
      semantic_model: requestedModel,
      retrieval_object_top_n: retrievalObjectTopN,
      qiniu_api_key: requestedProvider === "qiniu_vlm"
        ? ($("#tx-qiniu-key").value.trim() || $("#qiniu-key")?.value.trim() || null)
        : null,
      visual_fallback: $("#tx-visual-fallback").checked,
      force_unready_ir: false,
    };
    const requests = selections.map(({item, metricIds}) => ({
      ...sharedRequest,
      package_id: item.package_id,
      package_version: item.package_version,
      metric_ids: metricIds,
    }));
    const tasks = await docRequest("/api/pipeline/tasks/targeted-extraction/batch", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({requests}),
    });
    if (!Array.isArray(tasks) || tasks.length !== requests.length) {
      throw new Error(`批量入队返回 ${Array.isArray(tasks) ? tasks.length : 0} 个任务，预期 ${requests.length} 个。`);
    }
    for (let index = 0; index < tasks.length; index += 1) {
      const task = tasks[index];
      const request = requests[index];
      const returnedMetrics = task.payload?.metric_ids || [];
      if (task.payload?.semantic_provider !== requestedProvider) {
        throw new Error(`队列返回的模型通道不一致：请求 ${requestedProvider}，实际 ${task.payload?.semantic_provider || "缺失"}。`);
      }
      if (Number(task.payload?.retrieval_object_top_n) !== retrievalObjectTopN) {
        throw new Error(`队列返回的 Top N 不一致：请求 ${retrievalObjectTopN}，实际 ${task.payload?.retrieval_object_top_n || "缺失"}。`);
      }
      if (task.payload?.package_id !== request.package_id
          || task.payload?.package_version !== request.package_version
          || returnedMetrics.length !== request.metric_ids.length
          || request.metric_ids.some((metricId) => !returnedMetrics.includes(metricId))) {
        throw new Error(`队列返回的标准或指标集合不一致：${request.package_id}@${request.package_version}。`);
      }
      if (requestedProvider === "qiniu_vlm" && requestedModel && task.payload?.semantic_model !== requestedModel) {
        throw new Error(`队列返回的七牛模型不一致：请求 ${requestedModel}，实际 ${task.payload?.semantic_model || "默认模型"}。`);
      }
    }
    localStorage.setItem("esg-targeted-retrieval-object-top-n", String(retrievalObjectTopN));
    const acceptedProvider = requestedProvider === "qiniu_vlm"
      ? `七牛云 · ${tasks[0].payload.semantic_model || "后端默认模型"}`
      : "本地 NuExtract3";
    const metricCount = requests.reduce((sum, request) => sum + request.metric_ids.length, 0);
    $("#tx-launch-message").textContent = `已原子加入 ${tasks.length} 个任务、共 ${metricCount} 个指标 · ${acceptedProvider}`;
    window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskIds: tasks.map((task) => task.task_id)}}));
  } catch (error) {
    $("#tx-launch-message").textContent = `批量加入任务失败：${error.message}`;
  } finally {
    renderTargetBatchSelection();
  }
}

async function refreshTargetJobs() {
  try {
    const [jobs, resultCatalog] = await Promise.all([
      txRequest("/api/jobs?limit=500"),
      txRequest("/api/result-catalog").catch((error) => ({error: error.message})),
    ]);
    targetedState.jobs = jobs;
    targetedState.resultCatalog = resultCatalog;
    renderTargetJobs();
    if (targetedState.activeJob) {
      const updated = targetedState.jobs.find((item) => item.job_id === targetedState.activeJob.job_id);
      if (updated) await selectTargetJob(updated.job_id, false);
    }
  } catch (error) {
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
    const progress = job.progress_total ? Math.round(job.progress_current / job.progress_total * 100) : 0;
    const provider = job.request.semantic_provider === "qiniu_vlm" ? `七牛云 · ${job.request.semantic_model || "默认模型"}` : "本地 NuExtract3";
    const report = jobDocumentIdentity(job);
    return `<button class="job-card ${targetedState.activeJob?.job_id === job.job_id ? "active" : ""}" data-tx-job="${esc(job.job_id)}" type="button">
      <div class="job-card-head"><div class="job-card-report"><span class="year-badge">${esc(report.year)}</span><strong title="${esc(report.displayName)}">${esc(report.displayName)}</strong></div><span class="status-pill ${esc(job.status)}">${statusLabel(job.status)}</span></div>
      <p>${esc(job.request.package_id)} · ${job.request.metric_ids.length || "全部"} 指标</p>
      <small>${esc(provider)} · Top ${esc(job.request.retrieval_object_top_n || 3)} 对象 · ${esc(job.stage)} · ${job.progress_current}/${job.progress_total}</small>
      <code class="job-card-run">${esc(job.job_id)} · IR ${report.revision === null ? "?" : `r${report.revision}`}</code>
      <div class="progress"><span style="width:${progress}%"></span></div>
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

async function selectTargetJob(jobId, rerender = true) {
  const [job, inspection, events, artifacts] = await Promise.all([
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}`),
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}/inspection`).catch((error) => ({availability: "unavailable", message: error.message, metrics: [], overview: {}})),
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}/events`),
    txRequest(`/api/jobs/${encodeURIComponent(jobId)}/artifacts`),
  ]);
  targetedState.activeJob = job;
  targetedState.inspection = inspection;
  targetedState.events = events;
  targetedState.artifacts = artifacts;
  if (rerender) renderTargetJobs();
  renderTargetJobDetail();
}

function renderTargetJobDetail() {
  const job = targetedState.activeJob;
  const summary = job.summary || {};
  const execution = targetedState.inspection?.model_execution || summary.model_execution || {};
  const evidencePolicy = targetedState.inspection?.document?.evidence_policy || summary.document?.evidence_policy || {};
  const accountBlocked = (targetedState.inspection?.metrics || []).some((metric) =>
    (metric.decision?.attempts || []).some((attempt) =>
      attempt.provider_error_code === "account_billing_suspended"
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
  documentRoot.innerHTML = `<div class="result-document-primary"><span class="year-badge">${esc(report.year)}</span><div><small>当前成果对应报告</small><h3>${esc(report.displayName)}</h3><p>所有指标、事实和证据均来自该报告固定的 Document IR Revision。</p></div></div><div class="result-document-identifiers"><span><small>Document ID</small><code>${esc(report.documentId)}</code></span><span><small>Document IR</small><code>${esc(report.runId)}${report.revision === null ? "" : ` · r${report.revision}`}</code></span></div>`;
  $("#tx-job-summary").className = "job-summary";
  const cells = [
    ["状态", statusLabel(job.status)], ["阶段", job.stage], ["进度", `${job.progress_current}/${job.progress_total}`],
    ["已用时间", `${elapsedSeconds(job)} 秒`], ["模型调用", summary.model_call_count ?? modelCalls],
    ["Guard 通过", summary.guard_accepted_count ?? "处理中"], ["IR Revision", report.revision === null ? "未记录" : `r${report.revision}`],
    ["任务 ID", job.job_id], ["IR Schema", report.schemaVersion],
    ["标准包", `${job.request.package_id}@${job.request.package_version}`],
    ["语义检索", job.request.semantic_search ? "Qwen3 + BM25" : "仅 BM25"],
    ["证据对象上限", `Top ${job.request.retrieval_object_top_n || 3}`],
    ["请求模型", job.request.semantic_fill ? (job.request.semantic_provider === "qiniu_vlm" ? `七牛云 · ${job.request.semantic_model || "默认模型"}` : "本地 NuExtract3") : "关闭"],
    ["实际执行", (execution.actual_models || []).join("；") || (accountBlocked ? "七牛账户计费状态拒绝请求；未获得模型输出" : job.status === "completed" || job.status === "partial" ? "未记录有效模型响应" : "处理中")],
    ...(evidencePolicy.mode === "limited" ? [["证据范围", `受限：可用 ${evidencePolicy.available_page_count || 0} 页，排除 ${(evidencePolicy.excluded_page_indices || []).length} 页；排除页不代表未披露`]] : []),
  ];
  $("#tx-job-summary").innerHTML = `${cells.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}${job.error ? `<p class="error">${esc(job.error)}</p>` : ""}`;
  $("#tx-resume-job").textContent = repairableContractFailure
    ? "修复后重建结果（不调用模型）"
    : "加入断点续跑";
  $("#tx-resume-job").classList.toggle("hidden", !["interrupted", "failed", "cancelled", "partial"].includes(job.status));
  $("#tx-cancel-job").classList.toggle("hidden", !["queued", "running"].includes(job.status));
  $("#tx-delete-job").classList.toggle("hidden", ["queued", "running"].includes(job.status));
  renderTargetInspection();
  renderTargetOutcomes();
  renderTargetEvents();
  renderTargetArtifacts();
}

function renderTargetInspection() {
  const inspection = targetedState.inspection;
  if (!inspection || inspection.availability !== "ready") {
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
  $("#tx-inspection-summary").innerHTML = cards.map(([value, label, note]) => `<article><strong>${value || 0}</strong><span>${label}</span><small>${note}</small></article>`).join("");
  const execution = inspection.model_execution || {};
  const requested = execution.requested_provider === "qiniu_vlm"
    ? `七牛云 · ${execution.requested_model || "默认模型"}`
    : "本地 NuExtract3";
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
    : "<strong>输出合同校验通过</strong><span>记录数量、主键及事实 → 任务 → 证据关联完整；本页只读。</span>";
  const downloadLabels = {workbook: "下载机器表", fact_csv: "下载事实 CSV", task_csv: "下载任务 CSV", result_package: "下载完整 JSON"};
  $("#tx-result-downloads").innerHTML = Object.entries(inspection.downloads || {}).map(([key, path]) => `<a class="button-link" href="${targetedState.backend}/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/download?path=${encodeURIComponent(path)}">${esc(downloadLabels[key] || key)}</a>`).join("");
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
  return metric.guard_accepted === true ? "Guard 通过" : metric.guard_accepted === false ? "Guard 未通过" : "Guard 未执行";
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
    || String(item.error || "").includes("account_billing_suspended")
  );
}

function renderTargetMetricTable() {
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
      : blocked ? "未执行" : metric.guard_accepted === true ? "通过" : metric.guard_accepted === false ? "未通过" : "未执行";
    return `<tr class="metric-task-row ${selected ? "selected" : ""} ${metricQualityClass(metric)}" tabindex="0" data-tx-metric="${esc(metric.metric_id)}">
      <td class="metric-task-id"><strong>${esc(metric.source_datapoint_id)}</strong><code>${esc(metric.metric_id)}</code></td>
      <td><strong>${esc(metricName(metric))}</strong><small>${esc(metric.description || "")}</small></td>
      <td><span class="status-pill ${esc(metric.status)}">${esc(blocked ? "服务商账户阻塞" : statusLabel(metric.status))}</span></td>
      <td class="metric-count"><strong>${factCount}</strong><small>行事实</small></td>
      <td class="metric-count"><strong>${evidenceCount}</strong><small>条引用</small></td>
      <td><strong>${guard}</strong></td>
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
  const providers = [...new Set(attempts.map((item) => item.provider).filter(Boolean))];
  const models = [...new Set(attempts.map((item) => item.model_id).filter(Boolean))];
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
    <div class="diagnostic-grid"><section><h4>确定性来源与结果合同</h4><div class="guard-summary ${contractFailed || !metric.guard_accepted ? "failed" : "passed"}"><strong>${contractFailed ? "模型结果已保留，但结果物化合同失败" : accountBlocked ? "服务商拒绝请求；未获得模型输出" : metric.guard_accepted === true ? "来源 Guard 与结果合同均通过" : metric.guard_accepted === false ? "存在确定性来源合同错误" : "Guard 未执行"}</strong><span>${esc(contractFailed ? metric.contract_error || "该指标记录已隔离，未污染其他指标成果。" : accountBlocked ? "七牛账户计费状态异常。账户恢复后重新运行该任务；本次结果不能用于判断条款是否披露。" : metric.guard?.feedback || "无反馈；缺失字段允许为空。")}</span></div><pre class="json compact-json">${esc(JSON.stringify(metric.guard?.issues || [], null, 2))}</pre></section><section><h4>模型实际执行</h4><dl class="compact-facts"><div><dt>Provider</dt><dd>${esc(providers.join(" / ") || (accountBlocked ? "七牛云（请求被拒绝）" : "未调用模型"))}</dd></div><div><dt>模型</dt><dd>${esc(models.join(" / ") || "未记录")}</dd></div><div><dt>Evidence Regions</dt><dd>${regionCount || (attempts.length ? 1 : 0)}</dd></div><div><dt>本地充分性</dt><dd>${esc(sufficiency.status || "未产生")}</dd></div><div><dt>扫描证明</dt><dd>${esc((sufficiency.reason_codes || []).join(" / ") || "未产生")}</dd></div><div><dt>完整选择</dt><dd>${metric.retrieval?.packet_span_count || 0} spans · ${metric.retrieval?.packet_candidate_count || 0} candidates · ${metric.retrieval?.packet_image_count || 0} crops</dd></div><div><dt>模型调用/合并记录</dt><dd>${attempts.length}</dd></div></dl></section></div>
    <h4>Region / 模型调用明细</h4><div class="model-attempt-list">${attempts.map((item) => `<article><strong>${esc(item.provider || item.route || "本地步骤")} · ${esc(item.model_id || "无模型")}</strong><span>${item.region_index ? `Region ${item.region_index}/${item.region_count || "?"} · ` : ""}${Number(item.input_group_count || 0)} groups · ${Number(item.input_span_count || 0)} spans · ${Number(item.input_candidate_count || 0)} candidates · ${Number(item.image_count || 0)} images → ${Number(item.output_row_count || 0)} rows</span><small>${Number(item.input_chars || 0)} chars · prompt ${Number(item.prompt_tokens || 0)} tokens · output ${Number(item.generation_tokens || 0)} tokens · ${Number(item.elapsed_seconds || 0).toFixed(2)}s${item.finish_reason ? ` · ${esc(item.finish_reason)}` : ""}</small></article>`).join("") || '<p class="muted">本指标未调用语义模型。</p>'}</div>
    <h4>召回片段（非对象 Top N）· 前 ${hits.length}</h4><div class="retrieval-hits">${hits.map((hit, index) => `<article><span>#${index + 1}</span><div><strong>${hit.page_index == null ? "页码未记录" : `PDF 第 ${hit.page_index + 1} 页`}${hit.section_title ? ` · ${esc(hit.section_title)}` : ""}</strong><p>${esc(hit.text || "历史产物未保留该命中文本")}</p><small>final ${Number(hit.final_score || 0).toFixed(4)} · topic ${Math.round(Number(hit.topic_coverage || 0) * 100)}% · ${esc((hit.reasons || []).join(" / "))}</small></div></article>`).join("") || '<p class="muted">没有可展示的召回命中。</p>'}</div>
    <details><summary>完整 Guard、检索和模型决定 JSON</summary><pre class="json compact-json">${esc(JSON.stringify({guard: metric.guard, retrieval: metric.retrieval, decision: metric.decision}, null, 2))}</pre></details>`;
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

function renderTargetEvents() {
  $("#tx-tab-events").innerHTML = targetedState.events.map((event) => `<article class="event-row ${esc(event.level)}"><time>${new Date(event.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time><strong>${esc(event.stage)}</strong><span>${esc(event.message)}</span></article>`).join("") || '<div class="empty-state">没有事件。</div>';
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
  const response = await fetch(`${targetedState.backend}/api/jobs/${encodeURIComponent(targetedState.activeJob.job_id)}/artifact?path=${encodeURIComponent(path)}`);
  const contentType = response.headers.get("content-type") || "";
  $("#tx-artifact-preview").textContent = contentType.includes("application/json") ? JSON.stringify(await response.json(), null, 2) : await response.text();
}

async function resumeTargetJob() {
  const job = targetedState.activeJob;
  if (!job) return;
  try {
    const cloudKey = job.request.semantic_provider === "qiniu_vlm"
      ? ($("#tx-qiniu-key")?.value.trim() || $("#qiniu-key")?.value.trim() || null)
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
  if (!job || !window.confirm(`永久清除 ${job.job_id} 的定向抽取中间文件和成果吗？`)) return;
  try {
    await txRequest(`/api/jobs/${encodeURIComponent(job.job_id)}`, {method: "DELETE"});
    targetedState.activeJob = null;
    targetedState.inspection = null;
    $("#tx-job-title").textContent = "选择一个任务";
    $("#tx-result-document").className = "result-document-banner hidden";
    $("#tx-result-document").innerHTML = "";
    $("#tx-job-summary").className = "job-summary empty-state";
    $("#tx-job-summary").textContent = "选择任务后查看成果、证据和中间文件。";
    await refreshTargetJobs();
  } catch (error) {
    window.alert(`清除失败：${error.message}`);
  }
}

function bindTargetedEvents() {
  $("#tx-backend-url").value = targetedState.backend;
  $("#tx-reconnect").addEventListener("click", () => connectTargeted({allowDiscovery: false}));
  $("#tx-pipeline-stages").innerHTML = targetedStages.map(([index, title, text]) => `<article class="stage"><span class="stage-index">${index}</span><strong>${title}</strong><small>${text}</small></article>`).join("");
  $("#tx-ir-search").addEventListener("input", renderTargetIrPicker);
  $("#tx-ir-filter").addEventListener("change", renderTargetIrPicker);
  $("#tx-standard-select").addEventListener("change", loadTargetStandard);
  $("#tx-metric-search").addEventListener("input", filterTargetMetrics);
  $("#tx-metric-list").addEventListener("change", updateMetricSelectionCount);
  $("#tx-semantic-provider").addEventListener("change", () => {
    localStorage.setItem("esg-targeted-semantic-provider", $("#tx-semantic-provider").value);
    renderTargetProviderConfig();
  });
  $("#tx-semantic-model").addEventListener("input", () => {
    if ($("#tx-semantic-provider").value === "qiniu_vlm") {
      localStorage.setItem("esg-targeted-qiniu-model", $("#tx-semantic-model").value.trim());
    }
  });
  $("#tx-retrieval-object-top-n").addEventListener("input", () => {
    const control = $("#tx-retrieval-object-top-n");
    const value = Number(control.value);
    control.setCustomValidity(
      Number.isInteger(value) && value >= 1 && value <= 5
        ? ""
        : "请输入 1–5 之间的整数。"
    );
    if (!control.validationMessage) {
      localStorage.setItem("esg-targeted-retrieval-object-top-n", String(value));
    }
  });
  $("#tx-select-all").addEventListener("click", () => {
    document.querySelectorAll("#tx-metric-list input").forEach((item) => { item.checked = true; });
    updateMetricSelectionCount();
  });
  $("#tx-select-none").addEventListener("click", () => {
    document.querySelectorAll("#tx-metric-list input").forEach((item) => { item.checked = false; });
    updateMetricSelectionCount();
  });
  $("#tx-clear-batch").addEventListener("click", () => {
    targetedState.standards.forEach((item) => {
      targetedState.metricSelections.set(standardKey(item), new Set());
    });
    document.querySelectorAll("#tx-metric-list input").forEach((item) => { item.checked = false; });
    updateMetricSelectionCount();
  });
  $("#tx-add-task").addEventListener("click", addTargetedTask);
  $("#tx-refresh-jobs").addEventListener("click", refreshTargetJobs);
  $("#tx-result-search").addEventListener("input", renderTargetMetricTable);
  $("#tx-result-filter").addEventListener("change", renderTargetMetricTable);
  $("#tx-review-filter").addEventListener("change", renderTargetMetricTable);
  $("#tx-resume-job").addEventListener("click", resumeTargetJob);
  $("#tx-cancel-job").addEventListener("click", cancelTargetJob);
  $("#tx-delete-job").addEventListener("click", deleteTargetJob);
  $("#semantic-index-refresh")?.addEventListener("click", refreshSemanticIndexes);
  document.querySelectorAll("[data-tx-tab]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-tx-tab]").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll("#tx-result-tabs ~ .tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tx-tab-${button.dataset.txTab}`));
  }));
  window.addEventListener("esg:viewchange", (event) => {
    if (event.detail?.view === "targeted") connectTargeted({allowDiscovery: true});
    if (event.detail?.view === "results") refreshTargetJobs();
  });
}

bindTargetedEvents();
connectTargeted({allowDiscovery: true});
setInterval(() => {
  if (targetedState.activeJob && ["queued", "running"].includes(targetedState.activeJob.status)) refreshTargetJobs();
}, 2500);
