import {docRequest, serviceBase} from "./api.js";
import {subscribe} from "./subscriptions.js";
import {statusLabel} from "./status.js";
import {progressView, timingView, stageLabel} from "./progress.js";
import {watchExecutionEvents} from "./event-stream.js";
import {renderAvailableActions} from "./actions.js";
const $ = (selector) => document.querySelector(selector);

const platformState = {
  assets: [],
  tasks: [],
  selectedTaskId: null,
  draggedTaskId: null,
};

const backendBase = () => serviceBase("document");
const request = docRequest;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bytes(value = 0) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

const taskStatusLabel = statusLabel;

function taskTypeLabel(type, operation) {
  if (type === "ocr") return "OCR";
  if (type === "document_ir") {
    return {build: "Document IR", repair: "IR 定向修复", review_retry: "IR 复核重试"}[operation] || "Document IR";
  }
  return operation === "resume" ? "定向抽取续跑" : "定向抽取";
}

async function refreshAssets(preselect = null) {
  try {
    const payload = await request("/api/report-assets");
    platformState.assets = payload.assets || [];
    renderAssetSummary(payload);
    renderAssetList();
    populateOcrAssetSelect(preselect);
  } catch (error) {
    $("#report-asset-list").innerHTML = `<div class="empty-state error">报告目录读取失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderAssetSummary(payload) {
  const metrics = [
    [payload.asset_count || 0, "PDF 文件"],
    [platformState.assets.filter(asset => ["ready", "ready_with_limits"].includes(asset.processing_status)).length, "证据可用（含有限范围）"],
    [platformState.assets.filter(asset => !["ready", "ready_with_limits"].includes(asset.processing_status)).length, "待处理或复核"],
    [platformState.assets.reduce((sum, item) => sum + Number(item.size_bytes || 0), 0), "目录体积", true],
  ];
  $("#report-assets-summary").innerHTML = metrics.map(([value, label, isBytes]) =>
    `<div class="storage-metric"><strong>${escapeHtml(isBytes ? bytes(value) : value)}</strong><span>${label}</span></div>`
  ).join("");
  $("#report-assets-open-folder").dataset.path = payload.root || "";
}

function populateOcrAssetSelect(preselect = null) {
  const select = $("#ocr-asset-select");
  if (!select) return;
  const selected = preselect || select.value;
  select.innerHTML = '<option value="">选择 /pdf 中的 PDF</option>' + platformState.assets.map((asset) =>
    `<option value="${escapeHtml(asset.asset_id)}">${escapeHtml(asset.file_name)} · ${asset.ocr_status === "processed" ? "已 OCR" : "未处理"}</option>`
  ).join("");
  if (platformState.assets.some((item) => item.asset_id === selected)) select.value = selected;
}

function renderAssetList() {
  const query = $("#report-asset-search").value.trim().toLowerCase();
  const filter = $("#report-asset-filter").value;
  const items = platformState.assets.filter((asset) => {
    if (filter === "processed" && !["ready", "ready_with_limits"].includes(asset.processing_status)) return false;
    if (filter === "unprocessed" && ["ready", "ready_with_limits"].includes(asset.processing_status)) return false;
    return !query || `${asset.file_name} ${asset.sha256} ${asset.report_metadata?.publisher_name || ''} ${asset.report_metadata?.reporting_year || ''}`.toLowerCase().includes(query);
  });
  $("#report-asset-list").innerHTML = items.map((asset) => {
    const active = asset.pipeline_tasks || [];
    return `<article class="report-asset-row">
      <div class="report-asset-main"><strong>${escapeHtml(asset.report_metadata?.title || asset.file_name)}</strong><span>${escapeHtml(asset.report_metadata?.publisher_name || '发布者待登记')} · ${escapeHtml(asset.report_metadata?.reporting_year || '报告期待登记')} · ${bytes(asset.size_bytes)}</span>${asset.report_metadata?.title ? `<small>${escapeHtml(asset.file_name)}</small>` : ''}</div>
      <div class="report-asset-state"><span class="status-pill ${asset.processing_status === "ready" ? "success" : "neutral"}">${escapeHtml({ready:"证据可用", ready_with_limits:"部分证据可用", ocr_only:"已识别，待构建证据", needs_attention:"证据待检查", unprocessed:"待处理", queued:"处理已排队", running:"正在处理", interrupted:"处理中断", failed:"处理失败", cancelled:"已取消处理"}[asset.processing_status] || (asset.ocr_status === "processed" ? "有历史识别，需检查兼容性" : "待处理"))}</span>${active.map((item) => `<span class="status-pill running">${taskStatusLabel(item.status)}</span>`).join("")}</div>
      <div class="button-row"><a class="secondary button-link" target="_blank" href="${backendBase()}/api/report-assets/${encodeURIComponent(asset.asset_id)}/file">查看 PDF</a><button class="secondary asset-process" data-asset-id="${escapeHtml(asset.asset_id)}" type="button">处理报告</button><button class="secondary asset-use" data-asset-id="${escapeHtml(asset.asset_id)}" type="button">提取此报告</button><button class="secondary asset-detail" data-asset-id="${escapeHtml(asset.asset_id)}" type="button">文档与证据</button><button class="danger asset-delete" data-asset-id="${escapeHtml(asset.asset_id)}" data-file-name="${escapeHtml(asset.file_name)}" type="button">归档</button></div>
    </article>`;
  }).join("") || '<div class="empty-state">没有匹配的 PDF。</div>';
  document.querySelectorAll(".asset-process").forEach((button) => button.addEventListener("click", () => {
    window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "report-processing"}}));
    window.dispatchEvent(new CustomEvent("esg:process-report", {detail: {assetId: button.dataset.assetId}}));
  }));
  document.querySelectorAll(".asset-use").forEach((button) => button.addEventListener("click", () => {
    populateOcrAssetSelect(button.dataset.assetId);
    window.dispatchEvent(new CustomEvent("esg:select-report", {detail: {assetId: button.dataset.assetId}}));
    window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "extract-tasks"}}));
  }));
  document.querySelectorAll(".asset-detail").forEach((button) => button.addEventListener("click", () => window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "report-detail", assetId: button.dataset.assetId}}))));
  document.querySelectorAll(".asset-delete").forEach((button) => button.addEventListener("click", () => deleteAsset(button)));
}

async function deleteAsset(button) {
  const fileName = button.dataset.fileName;
  if (!window.confirm(`归档报告“${fileName}”吗？原文件、证据与成果保留，可在归档与回收区恢复。`)) return;
  try {
    await request(`/api/report-assets/${encodeURIComponent(button.dataset.assetId)}/archive`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({reason: "用户从报告库归档报告"}),
    });
    await refreshAssets();
  } catch (error) {
    window.alert(`归档失败：${error.message}`);
  }
}

async function uploadAsset(event) {
  event.preventDefault();
  const file = $("#report-asset-file").files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  const message = $("#report-asset-message");
  message.textContent = "上传中…";
  try {
    const asset = await request("/api/report-assets", {method: "POST", body: data});
    window.dispatchEvent(new CustomEvent("esg:assets-changed", {detail: {assetId: asset.asset_id}}));
    message.textContent = `已加入 ${asset.file_name}`;
    $("#report-asset-file").value = "";
    await refreshAssets(asset.asset_id);
  } catch (error) {
    message.textContent = `上传失败：${error.message}`;
  }
}

async function importFolder(event) {
  const files = [...event.target.files].filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  const failures = [];
  let imported = 0;
  for (const file of files) {
    const body = new FormData(); body.append("file", file);
    try { await request("/api/report-assets", {method: "POST", body}); imported++; }
    catch (error) { failures.push(`${file.name}：${error.message}`); }
    $("#report-asset-message").textContent = `已导入 ${imported}/${files.length} 份报告。`;
  }
  await refreshAssets();
  $("#report-asset-message").textContent = `已导入 ${imported} 份报告。${failures.join("；")}`;
  window.dispatchEvent(new CustomEvent("esg:assets-changed"));
  event.target.value = "";
}

async function refreshTasks() {
  try {
    const [tasksResult, statusResult] = await Promise.allSettled([
      request("/api/pipeline/tasks?limit=300"),
      request("/api/pipeline/status"),
    ]);
    if (tasksResult.status === "rejected") throw tasksResult.reason;
    platformState.tasks = tasksResult.value;
    renderTasks();
    if (statusResult.status === "fulfilled") {
      renderTaskDockSummary(statusResult.value.counts || {});
      renderSystemStatus(statusResult.value);
    } else {
      $("#task-dock-summary").textContent = "任务可读取；服务诊断暂不可用";
    }
    if (platformState.selectedTaskId) await loadTaskEvents(platformState.selectedTaskId);
  } catch (error) {
    $("#pipeline-task-list").innerHTML = `<div class="empty-state error">任务队列读取失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderTaskDockSummary(counts) {
  $("#task-dock-summary").textContent = `${counts.running || 0} 运行 · ${counts.queued || 0} 排队`;
  $("#platform-task-dock").classList.toggle("has-active", Boolean(counts.running || counts.queued));
}

function renderTasks() {
  const active = platformState.tasks.filter((item) => ["running", "queued"].includes(item.status));
  const history = platformState.tasks.filter((item) => !["running", "queued"].includes(item.status)).slice(0, 20);
  const render = (task) => {
    const draggable = task.status === "queued";
    return `<article class="pipeline-task ${task.status} ${task.task_id === platformState.selectedTaskId ? "selected" : ""}" data-task-id="${escapeHtml(task.task_id)}" draggable="${draggable}">
      <span class="drag-handle" title="拖动排队顺序">${draggable ? "⋮⋮" : ""}</span>
      <div class="pipeline-task-main"><strong>${escapeHtml(task.title)}</strong><span>${taskTypeLabel(task.task_type, task.operation)}</span>${progressView(task)}${timingView(task.timing)}${task.error_detail ? `<small class="error-text">${escapeHtml(task.error_detail.message)}</small>` : ""}</div>
      <div class="pipeline-task-stage"><span class="status-pill ${task.status}">${taskStatusLabel(task.status)}</span><small>${escapeHtml(stageLabel(task.stage))}</small></div>
      <div class="button-row">${["queued", "running"].includes(task.status) ? `<button class="danger pipeline-cancel" data-task-id="${escapeHtml(task.task_id)}" type="button">${task.status === "running" ? "终止" : "取消"}</button>` : (task.available_actions || []).some(a => a.action === "archive") ? `<button class="secondary pipeline-archive" data-task-id="${escapeHtml(task.task_id)}" type="button">归档记录</button>` : ""}<div data-queue-reference-actions="${escapeHtml(task.task_id)}"></div></div>
    </article>`;
  };
  $("#pipeline-task-list").innerHTML = [
    ...active.map(render),
    ...(history.length ? ['<div class="queue-history-label">最近完成</div>', ...history.map(render)] : []),
  ].join("") || '<div class="empty-state">队列为空。报告提取与文档工具提交的任务会出现在这里。</div>';
  document.querySelectorAll('[data-queue-reference-actions]').forEach(root => {
    const task = platformState.tasks.find(task => task.task_id === root.dataset.queueReferenceActions);
    renderAvailableActions(root, (task.available_actions || []).filter(action => action.action.endsWith('-references')),
      {onComplete: refreshTasks, onError: error => window.alert(error.message)});
  });
  document.querySelectorAll(".pipeline-task").forEach((node) => {
    node.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      platformState.selectedTaskId = node.dataset.taskId;
      renderTasks();
      selectDockTab("events");
      loadTaskEvents(node.dataset.taskId);
    });
    node.addEventListener("dragstart", () => { platformState.draggedTaskId = node.dataset.taskId; });
    node.addEventListener("dragend", () => { platformState.draggedTaskId = null; });
    node.addEventListener("dragover", (event) => {
      if (node.classList.contains("queued")) event.preventDefault();
    });
    node.addEventListener("drop", async (event) => {
      event.preventDefault();
      const sourceId = platformState.draggedTaskId;
      platformState.draggedTaskId = null;
      await moveQueuedTask(sourceId, node.dataset.taskId);
    });
  });
  document.querySelectorAll(".pipeline-cancel").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("确认终止这个任务吗？运行中的任务会终止整个隔离进程及其子进程。")) return;
    await request(`/api/pipeline/tasks/${encodeURIComponent(button.dataset.taskId)}/cancel`, {method: "POST"});
    await refreshTasks();
  }));
  document.querySelectorAll(".pipeline-archive").forEach(button => button.addEventListener("click", async () => {
    const task = platformState.tasks.find(t => t.task_id === button.dataset.taskId);
    try {
      await request(`/api/pipeline/tasks/${encodeURIComponent(button.dataset.taskId)}/archive`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({reason:"用户归档历史任务",expected_revision:task.revision})});
      window.dispatchEvent(new CustomEvent("esg:lifecycle-changed"));
      await refreshTasks();
    } catch (error) { window.alert(`归档失败：${error.message}`); }
  }));
}

async function moveQueuedTask(sourceId, targetId) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  const queued = platformState.tasks.filter((item) => item.status === "queued").map((item) => item.task_id);
  const from = queued.indexOf(sourceId);
  const to = queued.indexOf(targetId);
  if (from < 0 || to < 0) return;
  queued.splice(to, 0, queued.splice(from, 1)[0]);
  await request("/api/pipeline/tasks/order", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({task_ids: queued}),
  });
  await refreshTasks();
}

async function clearTaskHistory() {
  const historyCount = platformState.tasks.filter(
    (item) => !["running", "queued"].includes(item.status)
  ).length;
  if (!historyCount) return;
  if (!window.confirm(`归档全部已结束的队列记录吗？当前载入 ${historyCount} 条；依赖日志、证据与成果保留，可在归档区恢复。`)) return;
  try {
    const result = await request("/api/pipeline/tasks/history", {method: "DELETE"});
    if ((result.task_ids || []).includes(platformState.selectedTaskId)) {
      platformState.selectedTaskId = null;
      $("#pipeline-task-events").innerHTML = '<div class="empty-state">选择任务后显示日志。</div>';
    }
    await refreshTasks();
    window.dispatchEvent(new CustomEvent("esg:lifecycle-changed"));
    $("#task-dock-summary").textContent = `已归档 ${result.archived_count || 0} 条记录；成果和依赖保留`;
  } catch (error) {
    window.alert(`历史任务清除失败：${error.message}`);
  }
}

async function loadTaskEvents(taskId) {
  try {
    const events = await request(`/api/pipeline/tasks/${encodeURIComponent(taskId)}/events`);
    $("#pipeline-task-events").innerHTML = events.map((event) =>
      `<article class="event-row ${escapeHtml(event.level)}"><time>${new Date(event.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time><strong>${escapeHtml(event.stage_label || stageLabel(event.stage))}</strong><span>${escapeHtml(event.event_type === "raw_log" ? "原始日志：" + event.message : /[\u4e00-\u9fff]/.test(event.message) ? event.message : event.stage_label || stageLabel(event.stage))}</span>${event.event_type !== "raw_log" && !/[\u4e00-\u9fff]/.test(event.message) ? `<details><summary>技术详情</summary>${escapeHtml(event.message)}</details>` : ""}</article>`
    ).join("") || '<div class="empty-state">该任务尚无日志。</div>';
  } catch (error) {
    $("#pipeline-task-events").innerHTML = `<div class="empty-state error">${escapeHtml(error.message)}</div>`;
  }
}

function renderSystemStatus(status) {
  const services = status.services || {};
  $("#pipeline-system-status").innerHTML = `
    <article><span>调度模式</span><strong>单 Worker · 隔离进程</strong><small>OCR、IR、定向抽取不会并发占用统一内存</small></article>
    <article><span>Document 后端</span><strong>${services.document_backend?.available ? "正常" : "异常"}</strong><small>${escapeHtml(services.document_backend?.url || "-")}</small></article>
    <article><span>定向抽取后端</span><strong>${services.targeted_backend?.available ? "正常" : "未连接"}</strong><small>${escapeHtml(services.targeted_backend?.url || "-")}</small></article>
    <article><span>报告目录</span><strong>${platformState.assets.length} PDFs</strong><small>${escapeHtml(status.paths?.report_assets || "-")}</small></article>`;
}

function selectDockTab(name) {
  document.querySelectorAll("[data-dock-tab]").forEach((button) => button.classList.toggle("active", button.dataset.dockTab === name));
  document.querySelectorAll(".dock-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `dock-tab-${name}`));
}

function bindPlatformEvents() {
  $("#report-folder-import").addEventListener("change", importFolder);
  $("#report-asset-upload").addEventListener("submit", uploadAsset);
  $("#report-assets-refresh").addEventListener("click", () => refreshAssets());
  $("#report-asset-search").addEventListener("input", renderAssetList);
  $("#report-asset-filter").addEventListener("change", renderAssetList);
  $("#report-assets-open-folder").addEventListener("click", async (event) => {
    const path = event.currentTarget.dataset.path;
    try { await navigator.clipboard.writeText(path); } catch (_) {}
    $("#report-asset-message").textContent = `目录：${path}（已尝试复制）`;
  });
  $("#task-dock-toggle").addEventListener("click", () => {
    const dock = $("#platform-task-dock");
    dock.classList.toggle("collapsed");
    localStorage.setItem("esg-task-dock-collapsed", String(dock.classList.contains("collapsed")));
  });
  document.querySelectorAll("[data-dock-tab]").forEach((button) => button.addEventListener("click", () => selectDockTab(button.dataset.dockTab)));
  $("#pipeline-refresh").addEventListener("click", refreshTasks);
  $("#pipeline-clear-history").addEventListener("click", clearTaskHistory);
  window.addEventListener("esg:queue-changed", async (event) => {
    platformState.selectedTaskId = event.detail?.taskId || platformState.selectedTaskId;
    if (event.detail?.taskId) $("#platform-task-dock").classList.remove("collapsed");
    await refreshTasks();
  });
  window.addEventListener("esg:assets-changed", (event) => refreshAssets(event.detail?.assetId));
  $("#backend-url").addEventListener("change", () => {
    refreshAssets();
    refreshTasks();
  });
}

bindPlatformEvents();
if (localStorage.getItem("esg-task-dock-collapsed") === "false") $("#platform-task-dock").classList.remove("collapsed");
refreshAssets();
refreshTasks();
let eventsConnected = false, refreshTimer = null;
watchExecutionEvents(event => {
  if (event.event_type === "raw_log") return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { if (!platformState.draggedTaskId) refreshTasks(); }, 150);
}, connected => { eventsConnected = connected; });
subscribe("platform-queue", () => { if (!platformState.draggedTaskId) return refreshTasks(); }, {interval: () => eventsConnected ? 15000 : 2500, immediate: false});
