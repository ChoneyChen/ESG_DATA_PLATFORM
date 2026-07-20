const $ = (selector) => document.querySelector(selector);

const pipelineDefinitions = [
  ["01", "PaddleOCR 主解析", "Markdown + Raw JSONL"],
  ["02", "页图与本地取证", "Render + PDF cross-check"],
  ["03", "Layout 转换", "Raw objects + coordinates"],
  ["04", "确定性融合", "Dedupe + local observations"],
  ["05", "结构与视觉区域", "Sections + tables + crops"],
  ["06", "复合质量路由", "One page · scoped risks"],
  ["07", "Reviewer", "Confirm / correct / abstain"],
  ["08", "Patch Guard", "Atomic candidate + invariants"],
  ["09", "独立 Verifier", "Different model family"],
  ["10", "验证与版本冻结", "Readiness gate"],
];

let activeRunId = null;
let activeIrRunId = null;
let irDocument = null;
let irArtifacts = [];
let currentManifest = null;
let reviewState = {};
let pollTimer = null;
let irPollTimer = null;
let backendContextUrl = "";

const artifactGroupLabels = {
  manifest: "00 · Package 入口",
  source: "01 · 输入请求",
  provider: "02 · OCR供应商原始响应",
  content: "03 · 可读内容",
  observations: "04 · 解析观察",
  canonical: "05 · 正式Document IR",
  artifacts: "06 · 页图与区域图",
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
  activeRunId = null;
  activeIrRunId = null;
  irDocument = null;
  irArtifacts = [];
  currentManifest = null;
  reviewState = {};
  $("#current-run").textContent = "尚未启动";
  $("#current-ir-run").textContent = "尚未启动";
  $("#logs").textContent = "";
  $("#ir-logs").textContent = "";
  $("#artifacts").replaceChildren();
  $("#ir-artifacts").replaceChildren();
  $("#ir-workbench").classList.add("hidden");
  renderPipeline();
  return true;
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
  if (!response.ok) throw new Error(await response.text());
  return await response.json();
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
  const last = [...logs].reverse().find((line) => /^\d+\/10/.test(line));
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
  (payload.approved_vision_models || []).forEach((modelId) => {
    const health = (payload.health || []).find((item) => item.model_id === modelId);
    const card = documentNode("div", `model-health ${health?.last_status || "unknown"}`);
    card.append(
      documentNode("strong", "", modelId),
      documentNode("span", "", health ? `${health.last_status} · success ${health.successes} · failure ${health.failures}` : "approved · not called in this process"),
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
  if (!state) return;
  renderLogs($("#ir-logs"), state.logs || []);
  $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
  renderPipeline(progressFromLogs(state.logs || []));
  if (state.status === "done") {
    $("#ir-form button[type=submit]").disabled = false;
    await loadIrRun(state.run_id);
    await refreshHistories();
    return;
  }
  if (state.status === "failed") {
    $("#ir-form button[type=submit]").disabled = false;
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
    modelCalls, reviewerResults, guards, verifierResults, decisions, candidates] = await Promise.all([
    fetchJson(base), fetchJson(`${base}/manifest`), fetchJson(`${base}/quality-report`, {}),
    fetchJson(`${base}/validation-report`, {}), fetchJson(`${base}/document`),
    fetchJson(`${base}/review-tasks`, []), fetchJson(`${base}/atomic-patches`, []),
    fetchJson(`${base}/conflict-groups`, []), fetchJson(`${base}/artifacts`, {files: []}),
    fetchJson(`${base}/model-calls`, []), fetchJson(`${base}/reviewer-results`, []),
    fetchJson(`${base}/guard-results`, []), fetchJson(`${base}/verifier-results`, []),
    fetchJson(`${base}/final-decisions`, []), fetchJson(`${base}/candidate-revisions`, []),
  ]);
  if (requestBackend !== backendUrl()) return;
  if (state) {
    $("#current-ir-run").textContent = `${state.run_id} · ${state.status}`;
    renderLogs($("#ir-logs"), state.logs || []);
  }
  currentManifest = manifest;
  irDocument = document;
  irArtifacts = artifacts.files || [];
  reviewState = {tasks, patches, conflicts, modelCalls, reviewerResults, guards, verifierResults, decisions, candidates};
  setJson($("#ir-manifest"), manifest);
  setJson($("#quality-report"), quality);
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
  renderReviewList($("#review-tasks"), tasks, "没有需要模型复核的风险对象。", (item) => [item.task_id, `${item.priority} · ${item.task_type}`, `${item.status} · page ${item.page_index + 1} · ${item.blocking ? "blocking" : "optional"}`, (item.reason_codes || []).join(", ")], showReviewTask);
  renderReviewList($("#correction-patches"), patches, "尚无原子修正补丁。", (item) => [item.patch_id, `${item.operation} · risk ${item.risk_level}`, `${item.status} · ${item.target_id}`, item.rationale || ""], (item) => showPatch(item));
  renderReviewList($("#conflict-groups"), conflicts, "当前没有冲突组。", (item) => [item.conflict_id, item.conflict_type, `${item.status} · ${item.target_id}`, item.resolution || "未裁决"]);
  renderReviewSummary(reviewState);
  populateHumanPatches(patches);
  if (tasks.length) showReviewTask(tasks[0]);
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
    [manifest?.figure_count ?? document.figures?.length ?? 0, "Figures"],
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
    return;
  }
  tables.forEach((table) => select.add(new Option(`${table.table_id} · ${table.row_count}×${table.column_count}`, table.table_id)));
  renderTable(select.value);
}

function renderTable(tableId) {
  const table = irDocument.tables.find((item) => item.table_id === tableId);
  if (!table) return;
  const meta = $("#table-meta"); meta.replaceChildren();
  [`page ${table.page_index + 1}`, `${table.row_count} rows`, `${table.column_count} columns`, `${table.graph_edges?.length || 0} graph edges`, table.continuation_group_id || "single page", ...(table.quality_flags || [])].forEach((text) => meta.appendChild(documentNode("span", "chip", text)));
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
    card.append(documentNode("strong", "", figure.figure_id), documentNode("span", "", `page ${figure.page_index + 1} · ${figure.visual_status}`), documentNode("span", "", (figure.quality_flags || []).join(", ")));
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
  const values = [
    [(state.tasks || []).length, "复合任务"],
    [(state.modelCalls || []).length, "模型调用"],
    [(state.decisions || []).filter((item) => item.outcome === "auto_confirmed").length, "自动确认"],
    [(state.decisions || []).filter((item) => item.outcome === "auto_corrected").length, "自动修正"],
    [(state.decisions || []).filter((item) => item.outcome === "deferred").length, "自动待处理"],
    [(state.tasks || []).filter((item) => item.status === "human_required").length, "需人工"],
  ];
  values.forEach(([value, label]) => {
    const card = documentNode("div", "review-stat");
    card.append(documentNode("strong", "", String(value)), documentNode("span", "", label));
    root.appendChild(card);
  });
}

function showReviewTask(task) {
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
  calls.forEach((call) => root.appendChild(timelineCard(
    `${call.role === "reviewer" ? "Reviewer" : "Verifier"} · round ${call.round_index}`,
    `${call.model_id} · ${call.status} · ${Math.round(call.latency_ms || 0)} ms`,
    call,
    call.status === "succeeded" ? "success" : "error",
  )));
  reviewers.forEach((item) => root.appendChild(timelineCard(`Reviewer result · round ${item.attempt}`, `${item.verdict} · confidence ${formatConfidence(item.confidence)}`, item)));
  guards.forEach((item) => root.appendChild(timelineCard("Local Patch Guard", item.passed ? "PASS" : "FAIL", item, item.passed ? "success" : "error")));
  verifiers.forEach((item) => root.appendChild(timelineCard("Independent verifier", `${item.verdict} · ${item.model_id} · confidence ${formatConfidence(item.confidence)}`, item, item.verdict === "accept" ? "success" : "warning")));
  decisions.forEach((item) => root.appendChild(timelineCard("Final decision", `${item.outcome} · ${item.decided_by}`, item, item.blocking_resolved ? "success" : "warning")));
  if (!calls.length && !decisions.length) root.appendChild(documentNode("div", "muted", "该任务尚未执行模型复核。"));
  const humanPatches = (reviewState.patches || []).filter((item) => item.source_task_id === task.task_id && item.status === "human_required");
  if (task.status === "human_required" && !humanPatches.length) {
    const button = documentNode("button", "secondary", "人工确认保留当前 IR，并建立子版本");
    button.type = "button";
    button.addEventListener("click", () => resolveHumanTask(task));
    root.appendChild(button);
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
  patches.filter((item) => item.status === "human_required").forEach((item) => {
    select.add(new Option(`${item.patch_id} · ${item.operation} · ${item.target_id}`, item.patch_id));
  });
}

async function decideHumanPatch(action) {
  const patchId = $("#human-patch").value;
  if (!activeIrRunId || !patchId) return alert("请选择一个 human_required patch。");
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

async function resolveHumanTask(task) {
  if (!activeIrRunId) return;
  try {
    const manifest = await postJson(`/api/document-ir/jobs/${encodeURIComponent(activeIrRunId)}/review-tasks/${encodeURIComponent(task.task_id)}/decision`, {
      action: "accept",
      decided_by: $("#human-operator").value.trim() || "local-operator",
      notes: $("#human-notes").value.trim() || "Human confirmed the current Document IR candidate.",
    });
    await refreshHistories();
    await loadIrRun(manifest.run_id);
  } catch (error) {
    alert(`任务裁决失败：${error.message}`);
  }
}

function formatConfidence(value) {
  return Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : "?";
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
