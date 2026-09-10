const $ = (selector) => document.querySelector(selector);

const platformState = {
  assets: [],
  tasks: [],
  selectedTaskId: null,
  draggedTaskId: null,
  taskTimer: null,
};

function backendBase() {
  return ($("#backend-url")?.value || "http://127.0.0.1:18080").replace(/\/$/, "");
}

async function request(path, options = {}) {
  const response = await fetch(`${backendBase()}${path}`, options);
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

function taskStatusLabel(status) {
  return {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  }[status] || status;
}

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
    [payload.unprocessed_count || 0, "未 OCR"],
    [payload.processed_count || 0, "已 OCR"],
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
    if (filter !== "all" && asset.ocr_status !== filter) return false;
    return !query || `${asset.file_name} ${asset.sha256}`.toLowerCase().includes(query);
  });
  $("#report-asset-list").innerHTML = items.map((asset) => {
    const active = asset.pipeline_tasks || [];
    return `<article class="report-asset-row">
      <div class="report-asset-main"><strong>${escapeHtml(asset.file_name)}</strong><span>${bytes(asset.size_bytes)} · SHA ${escapeHtml(asset.sha256.slice(0, 12))}…</span></div>
      <div class="report-asset-state"><span class="status-pill ${asset.ocr_status === "processed" ? "success" : "neutral"}">${asset.ocr_status === "processed" ? `已 OCR · ${asset.ocr_run_count}` : "未处理"}</span>${active.map((item) => `<span class="status-pill running">${taskStatusLabel(item.status)}</span>`).join("")}</div>
      <div class="button-row"><a class="secondary button-link" target="_blank" href="${backendBase()}/api/report-assets/${encodeURIComponent(asset.asset_id)}/file">查看 PDF</a><button class="secondary asset-use" data-asset-id="${escapeHtml(asset.asset_id)}" type="button">用于 OCR</button><button class="danger asset-delete" data-asset-id="${escapeHtml(asset.asset_id)}" data-file-name="${escapeHtml(asset.file_name)}" type="button">删除</button></div>
    </article>`;
  }).join("") || '<div class="empty-state">没有匹配的 PDF。</div>';
  document.querySelectorAll(".asset-use").forEach((button) => button.addEventListener("click", () => {
    populateOcrAssetSelect(button.dataset.assetId);
    document.querySelector('[data-app-view-target="ocr"]')?.click();
  }));
  document.querySelectorAll(".asset-delete").forEach((button) => button.addEventListener("click", () => deleteAsset(button)));
}

async function deleteAsset(button) {
  const fileName = button.dataset.fileName;
  if (!window.confirm(`确认删除 PDF 原文件“${fileName}”吗？既有 OCR/IR 不会被删除。`)) return;
  try {
    await request(`/api/report-assets/${encodeURIComponent(button.dataset.assetId)}`, {
      method: "DELETE",
    });
    await refreshAssets();
  } catch (error) {
    window.alert(`删除失败：${error.message}`);
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
    const response = await fetch(`${backendBase()}/api/report-assets`, {method: "POST", body: data});
    if (!response.ok) throw new Error(await response.text());
    const asset = await response.json();
    message.textContent = `已加入 ${asset.file_name}`;
    $("#report-asset-file").value = "";
    await refreshAssets(asset.asset_id);
  } catch (error) {
    message.textContent = `上传失败：${error.message}`;
  }
}

async function refreshTasks() {
  try {
    const [tasks, status] = await Promise.all([
      request("/api/pipeline/tasks?limit=300"),
      request("/api/pipeline/status"),
    ]);
    platformState.tasks = tasks;
    renderTasks();
    renderTaskDockSummary(status.counts || {});
    renderSystemStatus(status);
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
    const progress = task.progress_total ? Math.round(task.progress_current / task.progress_total * 100) : 0;
    const draggable = task.status === "queued";
    return `<article class="pipeline-task ${task.status} ${task.task_id === platformState.selectedTaskId ? "selected" : ""}" data-task-id="${escapeHtml(task.task_id)}" draggable="${draggable}">
      <span class="drag-handle" title="拖动排队顺序">${draggable ? "⋮⋮" : ""}</span>
      <div class="pipeline-task-main"><strong>${escapeHtml(task.title)}</strong><span>${taskTypeLabel(task.task_type, task.operation)} · ${escapeHtml(task.native_job_id || task.task_id)}</span><small>${escapeHtml(task.message || task.stage || "等待执行")}</small><div class="progress"><span style="width:${progress}%"></span></div></div>
      <div class="pipeline-task-stage"><span class="status-pill ${task.status}">${taskStatusLabel(task.status)}</span><small>${escapeHtml(task.message || task.stage)}</small></div>
      <div class="button-row">${["queued", "running"].includes(task.status) ? `<button class="danger pipeline-cancel" data-task-id="${escapeHtml(task.task_id)}" type="button">${task.status === "running" ? "终止" : "取消"}</button>` : ""}</div>
    </article>`;
  };
  $("#pipeline-task-list").innerHTML = [
    ...active.map(render),
    ...(history.length ? ['<div class="queue-history-label">最近完成</div>', ...history.map(render)] : []),
  ].join("") || '<div class="empty-state">队列为空。三个处理页面提交的任务都会出现在这里。</div>';
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
  if (!window.confirm(`确认清除 ${historyCount} 条历史任务记录吗？OCR、Document IR、抽取结果和语义索引都将保留。`)) return;
  try {
    const result = await request("/api/pipeline/tasks/history", {method: "DELETE"});
    if ((result.task_ids || []).includes(platformState.selectedTaskId)) {
      platformState.selectedTaskId = null;
      $("#pipeline-task-events").innerHTML = '<div class="empty-state">选择任务后显示日志。</div>';
    }
    await refreshTasks();
  } catch (error) {
    window.alert(`历史任务清除失败：${error.message}`);
  }
}

async function loadTaskEvents(taskId) {
  try {
    const events = await request(`/api/pipeline/tasks/${encodeURIComponent(taskId)}/events`);
    $("#pipeline-task-events").innerHTML = events.map((event) =>
      `<article class="event-row ${escapeHtml(event.level)}"><time>${new Date(event.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time><strong>${escapeHtml(event.stage)}</strong><span>${escapeHtml(event.message)}</span></article>`
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
    $("#platform-task-dock").classList.remove("collapsed");
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
platformState.taskTimer = setInterval(() => {
  if (!platformState.draggedTaskId) refreshTasks();
}, 2500);
