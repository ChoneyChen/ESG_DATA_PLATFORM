import {docRequest, postDocument, serviceBase} from "./api.js";
import {subscribe, unsubscribe} from "./subscriptions.js";
import {getSecret} from "./roles.js";
import {statusLabel} from "./status.js";
import {progressView, timingView} from "./progress.js";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const labels = {ready: "证据可用", ready_with_limits: "局部证据可用", needs_attention: "需要处理证据", reused: "复用兼容成果", blocked: "条件未满足", pending: "待开始", cancelling: "正在取消，等待执行退出"};
const label = (state) => labels[state] || statusLabel(state);
let assets = [], runs = [], selectedId = null, active = false, submission = null, renderRevision = null;
let currentPages = [], currentPage = 0;
const message = (text, error = false) => { $("processing-message").textContent = text; $("processing-message").classList.toggle("error-text", error); };

async function requireReady() {
  const snapshot = await docRequest("/health/compatibility");
  if (snapshot.protocol_version !== "workbench-compatibility-v1" || snapshot.readiness?.ready !== true || snapshot.restart_required) {
    throw new Error(`报告处理服务版本未就绪：${(snapshot.readiness?.blockers || ["缺少有效兼容握手"]).join("；")}。已有记录与成果保留。`);
  }
}

function renderAssets() {
  const selected = $("processing-asset").value;
  $("processing-asset").innerHTML = `<option value="">请选择报告</option>${assets.map(a => `<option value="${esc(a.asset_id)}">${esc(a.file_name)}</option>`).join("")}`;
  if (assets.some(a => a.asset_id === selected)) $("processing-asset").value = selected;
}

function renderList() {
  const query = $("processing-search").value.trim().toLowerCase();
  const visible = runs.filter(r => `${r.file_name} ${r.report?.metadata?.title || ""} ${r.report?.metadata?.publisher_name || ""} ${r.report?.metadata?.reporting_year || ""}`.toLowerCase().includes(query));
  $("processing-list").innerHTML = visible.map(r => {
    const metadata = r.report?.metadata || {};
    const stage = r.stages?.find(s => s.status === "running" || s.status === "queued") || r.stages?.find(s => ["failed", "blocked", "interrupted"].includes(s.status));
    const currentBlockers = (r.current_evidence?.blockers || []).map(b => b.message).join("；");
    return `<article class="processing-row"><div><strong>${esc(metadata.title || r.file_name)}</strong><p class="muted">${esc(metadata.publisher_name || "发布者待确认")} · ${esc(metadata.reporting_year || "报告期待确认")} · ${esc(label(r.status))}${stage ? ` · ${esc(stage.label)}` : ""}</p>${r.error ? `<p class="error-text">${esc(r.error)}</p>` : ""}${currentBlockers ? `<p class="error-text">当前证据不可用于提取：${esc(currentBlockers)}</p>` : ""}</div><button type="button" class="secondary" data-processing-id="${esc(r.processing_id)}">查看并操作</button></article>`;
  }).join("") || '<p class="hint">暂无符合条件的处理记录。选择报告后开始处理。</p>';
  $("processing-list").querySelectorAll("[data-processing-id]").forEach(button => button.onclick = async () => { selectedId = button.dataset.processingId; renderRevision = null; await showDetail(); });
}

async function refresh(signal) {
  const outcomes = await Promise.allSettled([
    docRequest("/api/report-assets", {signal}), docRequest(`/api/report-processing/runs?include_archived=${$("processing-show-archived").checked}`, {signal}),
  ]);
  if (signal?.aborted) return;
  if (outcomes[0].status === "fulfilled") { assets = outcomes[0].value.assets || []; renderAssets(); }
  if (outcomes[1].status === "fulfilled") { runs = outcomes[1].value.runs || []; renderList(); await showDetail(); }
  const errors = outcomes.filter(o => o.status === "rejected").map(o => o.reason.message);
  if (errors.length) message(`读取失败：${errors.join("；")}。已有记录保留，可刷新重试。`, true);
}

function stageView(stage) {
  // IR work units are deliberately indeterminate unless the backend provides
  // a typed unit; arbitrary pipeline stage counts are not completion ratios.
  const progress = stage.progress ? progressView(stage) + timingView(stage.timing) : stage.progress_total > 0 && stage.stage === "ocr"
    ? `<p>已识别 ${esc(stage.progress_current || 0)} / ${esc(stage.progress_total)} 页</p>` : "";
  return `<div class="processing-stage" data-status="${esc(stage.status)}"><strong>${esc(stage.label)}</strong><p>${esc(label(stage.status))}</p>${progress}${stage.status === "reused" ? '<p class="muted">PDF、配置、构建版本及完整性已核对</p>' : ""}${stage.error ? `<p class="error-text">${esc(stage.error)}</p>` : ""}</div>`;
}

function metadataForm(report) {
  const m = report.metadata || {};
  const field = (name, text, type = "text") => `<label>${text}<input name="${name}" type="${type}" value="${esc(m[name] || "")}" ${type === "number" ? 'min="1900" max="2200"' : ''}/></label>`;
  return `<details class="processing-metadata"><summary>报告身份与版本</summary><p class="hint">发布者用于描述报告，不能自动视为每条指标的测量主体。不同 PDF 的报告版本须显式关联。</p><form id="processing-metadata-form"><div class="form-grid">${field("title", "报告名称")}${field("publisher_name", "发布者")}${field("reporting_year", "报告年", "number")}${field("period_start", "期间起始", "date")}${field("period_end", "期间结束", "date")}${field("language", "语言")}${field("report_kind", "报告类型")}</div><button type="submit" class="secondary">保存报告信息</button></form><p id="processing-metadata-message" class="hint" role="status"></p><p class="muted">已关联 ${report.asset_versions.length} 个 PDF 版本 · 报告信息修订 ${report.revision}</p><form id="processing-version-form"><label>关联已有报告 PDF 为新版本<select name="asset_id"><option value="">请选择另一份 PDF</option>${assets.filter(a => !report.asset_versions.some(v => v.source_sha256 === a.sha256)).map(a => `<option value="${esc(a.asset_id)}">${esc(a.file_name)}</option>`).join("")}</select></label><button type="submit" class="secondary">确认关联新版本</button></form></details>`;
}

async function showDetail() {
  const state = runs.find(r => r.processing_id === selectedId);
  if (!state) return;
  const detail = $("processing-detail");
  detail.classList.remove("hidden");
  const renderKey = `${state.processing_id}:${state.revision}:${JSON.stringify(state.current_evidence || {})}`;
  if (renderRevision === renderKey) return;
  if (detail.querySelector("form")?.contains(document.activeElement)) return;
  renderRevision = renderKey;
  const evidence = state.current_evidence || {status: state.evidence_status, blockers: []};
  const currentBlockers = (evidence.blockers || []).map(b => b.message).join("；");
  detail.innerHTML = `<div class="section-heading"><div><h2>${esc(state.report.metadata.title || state.file_name)}</h2><p class="hint">${currentBlockers ? "历史处理结果：" : ""}${esc(label(state.status))} · ${evidence.status === "full" ? "完整证据能力" : evidence.status === "limited" ? "只使用可靠局部证据，提取仍需核对指标所需能力" : "尚未具备提取证据条件"}</p></div><span class="processing-badge">${esc(state.report.metadata.reporting_year || "报告期待确认")}</span></div>${state.error ? `<p class="error-text">${esc(state.error)}</p>` : ""}${currentBlockers ? `<p class="error-text">当前证据不可用于提取：${esc(currentBlockers)}</p>` : ""}<div class="processing-stage-list">${state.stages.map(stageView).join("")}</div><div id="processing-actions" class="processing-actions"></div><div class="processing-detail-grid"><aside id="processing-pages" class="processing-pages"><p class="hint">原文页面</p></aside><iframe id="processing-pdf" class="processing-pdf" title="报告 PDF 原文" src="${esc(`${serviceBase()}/api/report-assets/${encodeURIComponent(state.asset_id)}/file#page=1`)}"></iframe><aside id="processing-evidence" class="processing-evidence"><p class="hint">${state.ir_run_id ? "正在读取页面与结构证据…" : "结构与证据构建完成后，可在这里核对正文和表格。"}</p></aside></div>${metadataForm(state.report)}<details><summary>运行与复用诊断</summary><p>识别成果 ${esc(state.ocr_run_id || "尚未产生")} · 结构成果 ${esc(state.ir_run_id || "尚未产生")}</p><ul>${(state.reuse_diagnostics || []).map(d => `<li>${esc(d.reason)}</li>`).join("")}</ul><p>${esc(state.evidence_policy?.mode || "证据策略待检查")}</p></details>`;
  const actions = state.available_actions || [];
  $("processing-actions").innerHTML = actions.map((a, i) => `<button type="button" class="secondary" data-processing-action="${i}">${esc(a.label)}</button>`).join("");
  $("processing-actions").querySelectorAll("[data-processing-action]").forEach(button => button.onclick = () => runAction(state, actions[Number(button.dataset.processingAction)], button));
  $("processing-metadata-form").onsubmit = async event => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.target));
    for (const key of Object.keys(fields)) if (!fields[key]) fields[key] = null;
    if (fields.reporting_year) fields.reporting_year = Number(fields.reporting_year);
    fields.publisher_entity_ref = state.report.metadata.publisher_entity_ref;
    try { await docRequest(`/api/reports/${encodeURIComponent(state.report_id)}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({metadata: fields, expected_revision: state.report.revision})}); renderRevision = null; await refresh(); message("报告信息已保存。"); }
    catch (error) { $("processing-metadata-message").textContent = error.message; }
  };
  $("processing-version-form").onsubmit = async event => {
    event.preventDefault(); const assetId = new FormData(event.target).get("asset_id");
    if (!assetId) return;
    try { await postDocument(`/api/reports/${encodeURIComponent(state.report_id)}/versions`, {asset_id: assetId, expected_revision: state.report.revision}); renderRevision = null; await refresh(); message("新的 PDF 版本已关联，旧来源及处理成果保留。"); }
    catch (error) { $("processing-metadata-message").textContent = error.message; }
  };
  if (state.ir_run_id) await loadPages(state);
}

async function loadPages(state) {
  try {
    currentPages = await docRequest(`/api/report-processing/runs/${encodeURIComponent(state.processing_id)}/pages`);
    if (selectedId !== state.processing_id) return;
    $("processing-pages").innerHTML = currentPages.map(page => `<button type="button" class="secondary" data-processing-page="${esc(page.page_index)}">第 ${esc(page.page_number || page.page_index + 1)} 页</button>`).join("");
    $("processing-pages").querySelectorAll("[data-processing-page]").forEach(button => button.onclick = () => selectPage(state, Number(button.dataset.processingPage)));
    await selectPage(state, currentPages.some(p => p.page_index === currentPage) ? currentPage : currentPages[0]?.page_index || 0);
  } catch (error) { if (selectedId === state.processing_id) $("processing-evidence").textContent = `页面证据读取失败：${error.message}。原文及处理成果保留。`; }
}

async function selectPage(state, pageIndex) {
  currentPage = pageIndex;
  $("processing-pdf").src = `${serviceBase()}/api/report-assets/${encodeURIComponent(state.asset_id)}/file#page=${pageIndex + 1}`;
  try {
    const page = await docRequest(`/api/report-processing/runs/${encodeURIComponent(state.processing_id)}/pages/${pageIndex}`);
    if (selectedId !== state.processing_id || currentPage !== pageIndex) return;
    const objects = page.layout_objects || [];
    const tableView = (table) => {
      const cells = table.cells || [];
      const rows = [...new Set(cells.map(c => c.row_index ?? c.row ?? 0))].sort((a, b) => a - b);
      return `<div class="processing-object"><strong>表格 ${esc(table.title || table.table_id)}</strong><table>${rows.map(r => `<tr>${cells.filter(c => (c.row_index ?? c.row ?? 0) === r).sort((a, b) => (a.col_index ?? a.column_index ?? 0) - (b.col_index ?? b.column_index ?? 0)).map(c => `<td>${esc(c.text ?? c.value ?? "")}</td>`).join("")}</tr>`).join("")}</table></div>`;
    };
    const excluded = new Set(state.evidence_policy?.excluded_target_ids || []);
    $("processing-evidence").innerHTML = `<h3>第 ${pageIndex + 1} 页证据</h3>${objects.map(o => `<div class="processing-object"><strong>${esc(o.label || "正文对象")}</strong>${excluded.has(o.layout_object_id) || excluded.has(o.table_id) ? '<p class="error-text">此对象被证据策略排除，需核对或修复</p>' : ''}<p>${esc(o.text)}</p>${o.bbox ? `<small class="muted">原文位置：${esc([o.bbox.x0, o.bbox.y0, o.bbox.x1, o.bbox.y1].join("，"))}</small>` : ""}</div>`).join("")}${(page.tables || []).map(tableView).join("")}${!objects.length && !(page.tables || []).length ? '<p class="hint">这一页没有可展示的结构对象，请核对原文与处理限制。</p>' : ''}`;
  } catch (error) { if (selectedId === state.processing_id) $("processing-evidence").textContent = error.message; }
}

async function runAction(state, action, button) {
  if (action.action === "inspect_evidence") { await loadPages(state); return; }
  if (action.action === "review_evidence") {
    window.dispatchEvent(new CustomEvent("esg:inspect-ir", {detail: {irRunId: state.ir_run_id, view: "pending"}}));
    return;
  }
  if (action.action === "select_evidence_version") {
    button.disabled = true;
    try {
      const result = await docRequest(action.endpoint);
      const versions = (result.versions || []).filter(v => v.usable && !v.current);
      let panel = $("processing-evidence-version");
      if (!panel) { panel = document.createElement("div"); panel.id = "processing-evidence-version"; $("processing-actions").after(panel); }
      if (!versions.length) { panel.innerHTML = '<p class="hint">尚无同一 PDF、同一识别来源的可用修复版本。先在文档证据复核中处理缺口，原版本仍保留。</p>'; return; }
      panel.innerHTML = `<form id="processing-evidence-version-form"><label>显式使用修复后的证据<select name="ir_run_id">${versions.map(v => `<option value="${esc(v.ir_run_id)}">结构修订 ${esc(v.revision || v.ir_run_id)} · ${v.evidence_status === "full" ? "完整证据" : "局部证据"}</option>`).join("")}</select></label><p class="hint">版本切换保留原引用，不自动推断修复配置。后续提取固定使用所选版本。</p><button type="submit" class="secondary">确认使用此证据版本</button></form>`;
      $("processing-evidence-version-form").onsubmit = async event => {
        event.preventDefault();
        try {
          await postDocument(`/api/report-processing/runs/${encodeURIComponent(state.processing_id)}/evidence`, {ir_run_id: new FormData(event.target).get("ir_run_id"), expected_revision: state.revision});
          renderRevision = null; await refresh(); message("已显式固定新的证据版本；旧版本和历史成果保留。");
        } catch (error) { message(error.message, true); }
      };
    } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
    return;
  }
  if (action.action === "start_extraction") {
    window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "extract-tasks"}}));
    window.dispatchEvent(new CustomEvent("esg:select-report", {detail: {assetId: state.asset_id, irRunId: state.ir_run_id}}));
    return;
  }
  if (!action.endpoint?.startsWith("/api/")) { message("操作接口不可用，请刷新。", true); return; }
  if (action.requires_reference_check) {
    try {
      const preflight = await docRequest(action.preflight_endpoint);
      if (!(preflight.available_actions || []).some(a => a.action === action.action && a.enabled !== false)) {
        message((preflight.blockers || []).map(b => b.message).join("；") || "来源依赖尚未允许此操作。", true);
        return;
      }
    } catch (error) { message(error.message, true); return; }
  }
  const payload = {...action.payload};
  if (action.requires_reason) {
    const reason = window.prompt(`${action.label}：请填写操作理由${action.requires_confirmation ? "（提交即确认执行，原始来源文件保留）" : ""}`)?.trim();
    if (!reason) return;
    payload.reason = reason;
  } else if (action.requires_confirmation && !window.confirm(`${action.label}？`)) return;
  button.disabled = true;
  try {
    if (action.action === "resume") await requireReady();
    if (action.action === "resume") Object.assign(payload, {qiniu_api_key: getSecret("qiniu_api_key"), ocr_token: getSecret("ocr_token")});
    await postDocument(action.endpoint, payload);
    await refresh(); window.dispatchEvent(new CustomEvent("esg:queue-changed"));
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
}

$("processing-form").onsubmit = async event => {
  event.preventDefault(); const asset = assets.find(a => a.asset_id === $("processing-asset").value);
  if (!asset) { message("请选择报告。", true); return; }
  if (!submission) submission = globalThis.crypto?.randomUUID?.() || `processing-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const button = $("processing-submit"); button.disabled = true;
  try {
    await requireReady();
    const state = await postDocument("/api/report-processing/runs", {asset_id: asset.asset_id, source_sha256: asset.sha256, submission_key: submission, mode: $("processing-mode").value,
      config: {ocr_provider: $("processing-ocr").value, allow_cloud_ocr: $("processing-cloud").checked, execute_optional_reviews: $("processing-reviews").checked, review_provider: $("processing-review-provider").value, allow_limited_evidence: $("processing-limited").checked},
      qiniu_api_key: getSecret("qiniu_api_key"), ocr_token: getSecret("ocr_token")});
    selectedId = state.processing_id; renderRevision = null; submission = null;
    message(state.status.startsWith("ready") ? "已复用兼容证据，可开始选择条款提取。" : "报告处理已加入统一队列；识别完成后会自动构建结构与证据。");
    await refresh(); window.dispatchEvent(new CustomEvent("esg:queue-changed"));
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
};
$("processing-form").onchange = () => { submission = null; };
$("processing-refresh").onclick = () => refresh();
$("processing-search").oninput = renderList;
$("processing-show-archived").onchange = () => { renderRevision = null; refresh(); };
function enter(view) { active = view === "report-processing"; unsubscribe("report-processing"); if (active) subscribe("report-processing", refresh, {interval: () => runs.some(r => ["queued", "running", "cancelling"].includes(r.status)) ? 3000 : 15000}); }
window.addEventListener("esg:viewchange", e => enter(e.detail.view));
window.addEventListener("esg:process-report", async e => { await refresh(); $("processing-asset").value = e.detail.assetId; window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "report-processing"}})); });
window.addEventListener("esg:assets-changed", () => { if (active) refresh(); });
window.addEventListener("esg:queue-changed", () => { if (active) refresh(); });
enter(document.body.dataset.activeView);
