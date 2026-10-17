import {reportFactsTable, evidenceTable, stageSummaryView, gapSummary} from "./results.js";
import {docRequest, targetedRequest, postDocument} from "./api.js";
import {subscribe, unsubscribe} from "./subscriptions.js";
import {statusLabel, isExecuting, taskRevision} from "./status.js";
import {stageTimeline, timingView, taskErrorView} from "./progress.js";
import {publishTask} from "./task-store.js";
import {loadStandards, ensureStandardPackage, standardSelections, loadPlatformMajorProfile, profileMetricIds, packageCoreMetricIds} from "./standards.js";
import {disclosureGraph} from "./disclosure.js";
import {roleConfig, getSecret} from "./roles.js";
import {renderAvailableActions} from "./actions.js";

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
let standardEntries = [], assets = [], irRuns = [], sessions = [];
let sessionListSignature = "";
let selectedResultSessionId = null, selectedResultPerspective = "facts";
let chosenAssetId = null, selectedSessionId = null, previewRequest = null, activeView = document.body.dataset.activeView;
const selectedMetrics = new Set();
let platformMajorProfile = null, selectionSnapshot = null, selectionKind = 'platform_major';
let compatibilityReady = false;
let standardsAvailable = false, irCatalogAvailable = false, chosenIrRunId = null;
const selectionLabels = new Map();
const openedStandards = new Set();
const sessionId = (session) => session.session_id || session.report_session_id || session.plan_id;
const execution = (session) => session.cancellation_status || session.execution_status || session.status || "queued";
const resultState = (session) => session.result_status || "not_evaluated";
const reportName = (session) => session.document_label || session.report_name || session.file_name || session.input?.file_name || session.request?.name || session.name || "报告提取任务";
const listValue = (payload, name) => Array.isArray(payload) ? payload : payload?.[name] || [];
const errorSummary = (session) => taskErrorView(session, "session-errors");
const mergeGaps = (...collections) => [...new Map(collections.flat().map((gap) => [gap.gap_id || JSON.stringify(gap), gap])).values()];

const creator = document.createElement("section");
creator.dataset.appView = "extract-tasks";
creator.className = "card report-session-creator view-hidden";
creator.innerHTML = `<div class="section-heading"><div><p class="eyebrow">One report · shared facts</p><h2>创建报告提取任务</h2></div><button type="button" class="secondary" id="session-reload">刷新报告与标准</button></div>
  <form id="session-form">
    <label>报告<select id="session-report"><option value="">正在读取报告库…</option></select></label>
    <details class="advanced-input"><summary>批量报告：沿用同一标准与角色</summary><label>额外报告（可多选）<select id="session-batch-reports" multiple size="5"></select></label><p class="hint">主选报告和额外报告分别创建独立 Session，逐份预览，串行执行。已有 IR 输入只对应一份报告。</p></details>
    <details class="advanced-input"><summary>高级输入：使用已有文档证据版本</summary><label>固定 Document IR<select id="session-ir"><option value="">自动准备或复用报告文档</option></select></label><p class="hint">选择已有版本后，使用与 PDF 相同的任务请求与角色配置。版本限制会在计划预览中显示。</p></details>
    <div class="section-heading compact-heading"><h3>标准要求</h3><span id="session-standard-count" class="muted">尚未选择指标</span></div>
    <div class="button-row"><button type="button" class="secondary" id="session-select-major" disabled>仅选择平台主要条款（当前 E1–E3）</button><button type="button" class="text-button" id="session-clear-metrics">清空条款选择</button></div>
    <p id="session-profile-hint" class="hint">主要条款是平台当前选择，核心条款属于各标准包。主要条款必须属于包内核心，包内核心不自动纳入平台。</p>
    <details id="session-pending-profile" class="advanced-input hidden"><summary>后续主要条款建设范围</summary><p class="hint" id="session-pending-profile-ids"></p></details>
    <label>搜索标准指标<input id="session-metric-search" type="search" placeholder="指标名称或条款号" /></label>
    <div id="session-standards" class="session-standard-selector"><div class="empty-state">正在读取标准要求包…</div></div>
    <div class="form-grid">
      <label>语义主脑<select id="session-brain"><option value="local_qwen">本地 Qwen · 报告理解与映射</option><option value="qiniu_vlm">七牛云通用视觉模型</option></select></label>
      <label id="session-cloud-model-field" class="hidden">七牛模型 ID（可选）<input id="session-cloud-model" placeholder="留空使用后端配置" /></label>
      <label>完整度<select id="session-completeness"><option value="quick">快速 · 有限证据探索</option><option value="balanced" selected>均衡 · 发现与补漏</option><option value="thorough">深入 · 更多补漏预算</option></select></label>
      <label class="checkbox-line"><input id="session-cloud-allowed" type="checkbox" /> 允许本任务将相关证据发送到已选云服务</label>
    </div>
    <p class="hint">主脑负责理解与证据行动；NuExtract 保留成熟专用抽取，embedding 负责检索。具体分工以后台预览和实际调用记录为准。</p>
    <details class="advanced-input"><summary>高级预算与证据范围</summary><div class="form-grid">
      <label>检索候选 Top N<input id="session-top" type="number" min="1" max="5" placeholder="由完整度预设决定" /></label>
      <label>初始共享对象预算<input id="session-shared" type="number" min="1" max="8" placeholder="由完整度预设决定" /></label>
      <label>Agent 步骤预算<input id="session-steps" type="number" min="1" max="24" placeholder="由完整度预设决定" /></label>
      <label>总时限（秒）<input id="session-total-time" type="number" min="60" placeholder="由后端预设决定" /></label>
      <label>单调用时限（秒）<input id="session-call-time" type="number" min="10" placeholder="由后端预设决定" /></label>
      <label class="checkbox-line"><input id="session-ocr-cloud" type="checkbox" /> 允许 OCR 云解析或本地失败后 API 回退</label>
      <label class="checkbox-line"><input id="session-limited" type="checkbox" checked /> 允许可靠局部证据继续，保留缺口</label>
    </div></details>
    <div class="button-row"><button id="session-preview-button" class="secondary" type="button">预览实际计划</button><button id="session-create" type="submit" disabled>确认计划并加入队列</button></div>
  </form><p id="session-message" class="hint" role="status"></p><div id="session-preview" class="session-preview hidden"></div>`;
$("main").appendChild(creator);

const sessionPanel = document.createElement("section");
sessionPanel.className = "card session-workspace view-hidden";
sessionPanel.dataset.appView = "extract-tasks";
sessionPanel.innerHTML = `<div class="section-heading"><div><p class="eyebrow">Report sessions</p><h2>报告任务与进度</h2></div><button type="button" id="session-refresh" class="secondary">刷新任务</button></div><div id="session-list" class="session-list"></div><div id="session-detail" class="session-detail"></div>`;
$("main").appendChild(sessionPanel);
const pendingPanel = document.createElement("section");
pendingPanel.className = "card session-pending view-hidden";
pendingPanel.dataset.appView = "pending";
pendingPanel.innerHTML = `<div class="section-heading"><div><p class="eyebrow">Recoverable work</p><h2>报告任务待处理</h2></div></div><p class="hint">执行状态与成果状态分别展示。已有事实可以查看，未完成动作按后端提供的范围继续。</p><div id="session-pending-list"></div>`;
$("main").appendChild(pendingPanel);
const sessionResults = document.createElement("section");
sessionResults.className = "card session-results view-hidden";
sessionResults.dataset.appView = "results";
sessionResults.innerHTML = `<div class="section-heading"><div><p class="eyebrow">Shared report result</p><h2>跨标准报告成果</h2></div></div><div id="session-result-list"></div><div id="session-report-results" class="session-report-results"></div>`;
$(".results-workspace").before(sessionResults);

function invalidatePreview() {
  previewRequest = null;
  $("#session-create").disabled = true;
  $("#session-preview").classList.add("hidden");
}

function updateMetricCount() {
  const unavailable = unavailableSelection();
  $("#session-standard-count").textContent = `已选 ${selectedMetrics.size} 个条款 · ${standardSelections(standardEntries, selectedMetrics).length} 个可读取标准包${unavailable.length ? ` · ${unavailable.length} 个所选条款暂不可用` : ''} · ${selectionSnapshot ? '平台主要条款配置' : '自定义选择'}`;
  updateChoiceAvailability();
}

function unavailableSelection() {
  const available = new Set(standardEntries.flatMap(entry => entry.package.metrics.map(metric => metric.metric_id)));
  return [...selectedMetrics].filter(id => !available.has(id));
}

function pinnedIrProblem() {
  if (!chosenIrRunId) return '';
  if (!irCatalogAvailable) return `无法核对已固定证据版本 ${chosenIrRunId}，请刷新证据列表；不会自动改用其他版本`;
  return irRuns.some(run => run.run_id === chosenIrRunId) ? '' : `已固定证据版本 ${chosenIrRunId} 不在可用目录中，请恢复该版本或明确更换输入`;
}

function restoreIrSelection() {
  if (!chosenIrRunId) return;
  if (![...$("#session-ir").options].some(option => option.value === chosenIrRunId)) {
    const option = document.createElement('option');
    option.value = chosenIrRunId; option.textContent = `固定版本待核对：${chosenIrRunId}`;
    $("#session-ir").appendChild(option);
  }
  $("#session-ir").value = chosenIrRunId;
}

function updateChoiceAvailability() {
  $("#session-preview-button").disabled = !compatibilityReady || !standardsAvailable ||
    unavailableSelection().length > 0 || !!pinnedIrProblem() || selectionKind === 'platform_major' && !platformMajorProfile;
}

function clearSelectionSnapshot() {
  selectionSnapshot = null;
  selectionKind = 'custom';
  $("#session-profile-hint").textContent = '当前为自定义条款选择。各包可独立选择核心条款；重新选择平台主要条款可恢复已固定版本的重点配置。';
}

function applyMajorSelection() {
  const ids = profileMetricIds(standardEntries, platformMajorProfile);
  selectedMetrics.clear(); ids.forEach((id) => selectedMetrics.add(id));
  selectionSnapshot = platformMajorProfile.selection_snapshot;
  selectionKind = 'platform_major';
  const all = platformMajorProfile.overall_source_datapoint_ids || [];
  const pending = platformMajorProfile.pending_source_datapoint_ids || [];
  $("#session-profile-hint").textContent = `平台主要清单保留全部 ${all.length} 个原编号。本次显式选择 E1–E3 的 ${ids.length} 个主要条款；其余 ${pending.length} 个等待后续标准包建设。包内其它核心条款不自动成为输出。`;
  renderStandards(); invalidatePreview();
}

function renderStandards() {
  const diagnostic = standardEntries.diagnostics;
  const stateNotice = diagnostic ? `<p class="${diagnostic.invalid_package_count ? 'error-text' : 'hint'}">目录：${diagnostic.valid_package_count} 个可读取包${diagnostic.invalid_package_count ? `，${diagnostic.invalid_package_count} 个加载失败` : ''}。结构校验通过不代表已完成真实报告验收或业务批准。</p>` : '';
  const issueNotice = diagnostic?.issues?.length ? `<details><summary>包校验问题（${diagnostic.issues.length}）</summary><ul>${diagnostic.issues.map(issue => `<li>${esc(issue.path)}：${esc(issue.error)}</li>`).join('')}</ul></details>` : '';
  const pendingNotice = standardEntries.planned?.length ? `<details><summary>后续占位（${standardEntries.planned.length} 个包，不可执行）</summary><ul>${standardEntries.planned.map(item => `<li>${esc(item.disclosure_requirement)} · 已登记 ${item.source_datapoint_ids?.length || 0} 个原编号 · 待完整包分析与建设</li>`).join('')}</ul></details>` : '';
  const historicalNotice = standardEntries.historical?.length ? `<details><summary>历史版本（${standardEntries.historical.length} 个，仅供既有任务引用）</summary><ul>${standardEntries.historical.map(item => `<li>${esc(item.package_id)} · ${esc(item.package_version)}</li>`).join('')}</ul></details>` : '';
  const missing = unavailableSelection();
  const unavailableNotice = missing.length ? `<div class="empty-state error"><strong>保留 ${missing.length} 个暂不可用的所选条款</strong><p>不会缩减本次输出。恢复对应包，或明确移除／更换选择后才能预览。</p><ul>${missing.map(id => `<li>${esc(selectionLabels.get(id) || id)} <button type="button" class="text-button" data-remove-unavailable="${esc(id)}">移除此条款</button></li>`).join('')}</ul></div>` : '';
  const roleLabels = {anchor: '锚点条款', detail: '延伸披露', context: '方法／说明', derived: '可派生条款', presentation: '展示条款'};
  const obligationLabels = {mandatory: '强制披露', conditional: '条件披露', mandatory_if_material: '重大时披露', alternative_if_material: '重大时替代披露', voluntary_if_material: '重大时自愿披露', conditional_if_material: '重大且适用时披露', conditional_voluntary_if_material: '适用时自愿披露'};
  $("#session-standards").innerHTML = standardEntries.map(({catalog, package: standard}) => {
    const name = catalog.disclosure_requirement || standard.manifest.standard?.disclosure_requirement || catalog.package_id;
    const core = new Set(packageCoreMetricIds(standard));
    const major = new Set(platformMajorProfile?.overall_source_datapoint_ids || []);
    const opened = openedStandards.has(catalog.package_id) || standard.metrics.some(metric => selectedMetrics.has(metric.metric_id));
    return `<details data-standard-package="${esc(catalog.package_id)}" ${opened ? 'open' : ''}><summary><strong>${esc(name)}</strong> · ${catalog.metric_count ?? standard.metrics.length} 条 · v${esc(catalog.package_version)} · ${({draft: '已编译草稿', released: '已发布包', deprecated: '已退役'})[catalog.status] || esc(catalog.status || '草稿')}</summary><p class="hint">包内核心 ${core.size} 条；主要条款按平台清单另行标记。真实提取验收及业务批准：未评估。</p><div class="button-row"><button type="button" class="text-button" data-standard-choice="core" data-standard-id="${esc(catalog.package_id)}" ${core.size ? '' : 'disabled'}>选择本包核心条款（${core.size}）</button><button type="button" class="text-button" data-standard-choice="all" data-standard-id="${esc(catalog.package_id)}">选择本包全部条款</button><button type="button" class="text-button" data-standard-choice="none" data-standard-id="${esc(catalog.package_id)}">清空本包</button></div>${standard.metrics.map((metric) => `<label class="session-metric" data-search="${esc(`${metric.source_datapoint_id} ${metric.labels?.zh || metric.labels?.en || ""}`.toLowerCase())}"><input type="checkbox" value="${esc(metric.metric_id)}" ${selectedMetrics.has(metric.metric_id) ? "checked" : ""} /><span><strong>${esc(metric.source_datapoint_id)}</strong> ${esc(metric.labels?.zh || metric.labels?.en || metric.metric_id)}${core.has(metric.metric_id) ? ' <small class="muted">核心条款</small>' : ''}${major.has(metric.source_datapoint_id) ? ' <small class="muted">主要条款</small>' : ''}<small>${esc(roleLabels[metric.disclosure_role] || metric.disclosure_role || '')} · ${esc(obligationLabels[metric.obligation_class] || metric.obligation_class || '')}</small></span></label>`).join("")}<div data-standard-detail="${esc(catalog.package_id)}" class="hint">展开本包后读取关系与详细合同。</div></details>`;
  }).join("") || `<div class="empty-state ${diagnostic?.invalid_package_count ? 'error' : ''}">${diagnostic?.invalid_package_count ? '标准目录加载失败，请查看包校验问题；这不表示目录为空。' : '没有可执行的标准包。'}</div>`;
  $("#session-standards").insertAdjacentHTML('afterbegin', stateNotice + unavailableNotice + issueNotice);
  $("#session-standards").insertAdjacentHTML('beforeend', pendingNotice + historicalNotice);
  $("#session-standards").querySelectorAll('[data-standard-package]').forEach(node => {
    node.addEventListener('toggle', async () => {
      const id = node.dataset.standardPackage;
      if (!node.open) { openedStandards.delete(id); return; }
      openedStandards.add(id);
      const entry = standardEntries.find(item => item.catalog.package_id === id);
      const detail = node.querySelector('[data-standard-detail]');
      detail.textContent = '正在读取本包详细合同…';
      try {
        const standard = await ensureStandardPackage(entry);
        detail.innerHTML = disclosureGraph(standard) + `<p>格式 ${esc(standard.format_version)} · Core ${esc(standard.manifest.core_schema_version || entry.catalog.core_schema_version)} · 保留原条款编号</p>`;
      } catch (error) { detail.textContent = `本包详情读取失败：${error.message}。其他包仍可选择，重新展开可重试。`; }
    });
  });
  $("#session-standards").querySelectorAll("input[type=checkbox]").forEach((input) => input.addEventListener("change", () => {
    input.checked ? selectedMetrics.add(input.value) : selectedMetrics.delete(input.value);
    clearSelectionSnapshot();
    updateMetricCount(); invalidatePreview();
  }));
  $("#session-standards").querySelectorAll("[data-standard-choice]").forEach((button) => button.addEventListener("click", () => {
    const entry = standardEntries.find((item) => item.catalog.package_id === button.dataset.standardId);
    const core = new Set(packageCoreMetricIds(entry.package));
    for (const metric of entry.package.metrics) {
      const chosen = button.dataset.standardChoice === 'all' || button.dataset.standardChoice === 'core' && core.has(metric.metric_id);
      chosen ? selectedMetrics.add(metric.metric_id) : selectedMetrics.delete(metric.metric_id);
    }
    clearSelectionSnapshot();
    renderStandards(); invalidatePreview();
  }));
  $("#session-standards").querySelectorAll('[data-remove-unavailable]').forEach(button => button.onclick = () => {
    selectedMetrics.delete(button.dataset.removeUnavailable); clearSelectionSnapshot(); renderStandards(); invalidatePreview();
  });
  updateMetricCount();
}

async function loadChoices() {
  $("#session-message").textContent = "正在读取报告与标准…";
  standardsAvailable = false; compatibilityReady = false;
  chosenIrRunId = chosenIrRunId || $("#session-ir").value || null;
  invalidatePreview(); updateChoiceAvailability();
  const responses = await Promise.allSettled([
      docRequest("/api/report-assets"), loadStandards(), targetedRequest("/api/ir-runs"),
      loadPlatformMajorProfile(), docRequest('/api/pipeline/compatibility'),
  ]);
  const [assetResult, standardsResult, irResult, profileResult, compatibilityResult] = responses;
  const failures = [];
  const previousReady = compatibilityReady;
  compatibilityReady = compatibilityResult.status === 'fulfilled' && compatibilityResult.value?.ready === true;
  if (!compatibilityReady) failures.push(`服务版本或业务未就绪：${compatibilityResult.status === 'rejected' ? compatibilityResult.reason.message : (compatibilityResult.value?.blockers || ['缺少兼容握手']).join('；')}`);
  if (!compatibilityReady || previousReady !== compatibilityReady) invalidatePreview();
  if (assetResult.status === 'fulfilled') {
    const catalog = assetResult.value;
    assets = catalog.assets || [];
    const currentReport = chosenAssetId || $("#session-report").value;
    $("#session-report").innerHTML = '<option value="">选择报告库中的 PDF</option>' + assets.map((asset) => `<option value="${esc(asset.asset_id)}">${esc(asset.file_name)} · ${asset.ocr_status === "processed" ? "已有文档解析" : "待准备文档"}</option>`).join("");
    if (assets.some((asset) => asset.asset_id === currentReport)) $("#session-report").value = currentReport;
    $("#session-batch-reports").innerHTML = assets.map((asset) => `<option value="${esc(asset.asset_id)}">${esc(asset.file_name)}</option>`).join("");
  } else failures.push(`报告库读取失败：${assetResult.reason.message}（标准仍可查看）`);
  if (irResult.status === 'fulfilled') {
    irCatalogAvailable = true;
    irRuns = listValue(irResult.value, "runs");
    $("#session-ir").innerHTML = '<option value="">自动准备或复用报告文档</option>' + irRuns.map((run) => `<option value="${esc(run.run_id)}">${esc(run.document_label || "报告")} · 证据版本 r${run.ir_revision ?? "?"} · ${run.can_build_evidence ? "可用" : "有证据限制"}</option>`).join("");
  } else { irCatalogAvailable = false; failures.push(`已有证据列表读取失败：${irResult.reason.message}（报告和标准仍可查看）`); }
  restoreIrSelection();
  if (pinnedIrProblem()) failures.push(pinnedIrProblem());
  if (standardsResult.status === 'fulfilled') {
    standardsAvailable = true;
    const entries = standardsResult.value;
    standardEntries = entries;
    entries.flatMap(entry => entry.package.metrics).forEach(metric => selectionLabels.set(metric.metric_id, `${metric.source_datapoint_id} · ${metric.labels?.zh || metric.labels?.en || ''}`));
    platformMajorProfile = profileResult.status === 'fulfilled' ? profileResult.value : null;
    $("#session-select-major").disabled = !platformMajorProfile || !compatibilityReady;
    const pending = platformMajorProfile?.pending_source_datapoint_ids || [];
    $("#session-pending-profile").classList.toggle('hidden', !pending.length);
    $("#session-pending-profile-ids").textContent = pending.join('、');
    let profileError = profileResult.status === 'rejected' ? profileResult.reason : null;
    if (platformMajorProfile && selectionKind === 'platform_major' && !unavailableSelection().length) {
      try { applyMajorSelection(); } catch (error) { profileError = error; }
    }
    if (profileError) {
      $("#session-select-major").disabled = true;
      $("#session-profile-hint").textContent = `平台主要条款配置暂不可用：${profileError.message}。可保留原选择或自定义条款，刷新后重试。`;
    }
    renderStandards();
    if (unavailableSelection().length) failures.push(`所选 ${unavailableSelection().length} 个条款暂不可用，原选择保留；请恢复包或明确更换选择`);
  } else {
    failures.push(`标准目录读取失败：${standardsResult.reason.message}`);
    $("#session-select-major").disabled = true;
    $("#session-standards").insertAdjacentHTML('afterbegin', `<div class="empty-state error">标准目录不可用：${esc(standardsResult.reason.message)}。已有选择保留，刷新可重试。</div>`);
  }
  updateChoiceAvailability();
  $("#session-message").textContent = failures.length ? failures.join('。') : assets.length ? "选择报告和要求后，预览后台真实计划。" : "报告库为空，请先上传 PDF 或选择已有证据版本。";
}

export function buildReportRequest(assetOverride = null) {
  if (!compatibilityReady) throw new Error('服务版本或标准合同未就绪，请刷新并查看兼容诊断；已有记录可查看');
  if (!standardsAvailable) throw new Error('标准目录尚未核对，原选择保留，请刷新后重试');
  if (unavailableSelection().length) throw new Error('所选条款暂不可用，不能静默缩减输出；请恢复包或明确更换选择');
  if (pinnedIrProblem()) throw new Error(pinnedIrProblem());
  if (selectionKind === 'platform_major' && !platformMajorProfile) throw new Error('主要条款配置尚未核对，原配置保留，请刷新后重试');
  const irRunId = assetOverride ? "" : $("#session-ir").value;
  const assetId = assetOverride || $("#session-report").value;
  const standards = standardSelections(standardEntries, selectedMetrics);
  const provider = $("#session-brain").value;
  const allowed = $("#session-cloud-allowed").checked;
  if ((!irRunId && !assetId) || !standards.length) throw new Error("请选择报告或已有证据版本，以及至少一个标准指标。");
  if ((provider === "qiniu_vlm" || $("#session-ocr-cloud").checked) && !allowed) throw new Error("七牛主脑需要明确允许本任务发送相关证据至云服务。");
  const budget = {};
  for (const [id, key] of [["session-top", "retrieval_object_top_n"], ["session-shared", "shared_object_budget"], ["session-steps", "agent_max_steps"], ["session-total-time", "total_timeout_seconds"], ["session-call-time", "call_timeout_seconds"]]) {
    const input = document.getElementById(id);
    if (!input.value) continue;
    if (!input.checkValidity()) throw new Error(`${input.closest("label").firstChild.textContent}超出允许范围。`);
    budget[key] = Number(input.value);
  }
  return {
    input: irRunId ? {kind: "document_ir", ir_run_id: irRunId} : {kind: "pdf", asset_id: assetId},
    name: irRunId ? irRuns.find((run) => run.run_id === irRunId)?.document_label || "报告提取" : assets.find((asset) => asset.asset_id === assetId)?.file_name || "报告提取",
    standards, role_config: roleConfig(provider, $("#session-cloud-model").value.trim(), allowed, $("#session-ocr-cloud").checked),
    completeness: $("#session-completeness").value,
    allow_limited_evidence: $("#session-limited").checked, semantic_search: true,
    ...(Object.keys(budget).length ? {budget} : {}),
    ...(selectionSnapshot ? {selection_snapshot: selectionSnapshot} : {}),
  };
}

function renderPreview(preview, root = $("#session-preview")) {
  root.classList.remove("hidden");
  const roles = preview.roles || {};
  const roleNames = {semantic_brain: "语义主脑", specialist: "成熟专用抽取", retrieval: "语义检索", embedding: "语义检索", ocr: "文档解析"};
  const providerNames = {local_qwen: "本地 Qwen", qiniu_vlm: "七牛云视觉模型", local_nuextract: "本地 NuExtract", local_paddleocr: "本地 OCR", paddle_api: "OCR API", local_first: "本地优先，可回退 API"};
  const roleRows = Object.entries(roles).filter(([key, value]) => roleNames[key] && value?.provider).map(([key, value]) => `${roleNames[key]}：${providerNames[value.provider] || value.provider}${value.model ? ` · ${value.model}` : ""}`);
  const preparation = preview.document_preparation || {};
  const reused = [preparation.ocr_reused ? "复用已有文档解析" : "", preparation.ir_reused ? "复用已有文档证据" : "", ...(preview.reused_inputs || []).map((item) => item.kind === "document_ir" ? "固定已选文档证据版本" : item.kind === "ocr" ? "复用已有文档解析" : item.label || item.kind)].filter(Boolean);
  const dependencies = (preview.internal_dependencies || []).flatMap((item) => item.metric_ids || []);
  const dependencyNames = dependencies.map((id) => {
    const metric = standardEntries.flatMap((entry) => entry.package.metrics).find((item) => item.metric_id === id);
    return metric ? `${metric.source_datapoint_id} · ${metric.labels?.zh || metric.labels?.en || id}` : id;
  });
  const clauseName = clause => `${clause.source_datapoint_id || clause.metric_id} · ${clause.label || ''}`;
  const outputs = (preview.selected_outputs || []).map(clauseName);
  const purposeNames = {report_identity: '报告编制与主体范围', measurement_method: '测量方法', governance_context: '治理背景', business_context: '业务背景', materiality_context: '重要性背景', financial_context: '财务背景', adopted_policy: '已采用政策的共用要求', absent_policy: '未采用政策的说明要求', adopted_action: '已采取行动的共用要求', absent_action: '未采取行动的说明要求', adopted_target: '已设目标的共用要求', absent_target: '未设目标的说明要求'};
  const contexts = (preview.context_requirements || []).map(ref => `${purposeNames[ref.purpose] || '声明的共用背景'}：${(ref.clauses || []).map(clauseName).join('；')}${ref.activation_state === 'awaiting_report_context' ? '（待报告事实确认适用分支，不自动激活）' : ''}。${ref.description || ''}`);
  const axisNames = {source_row_topic: '来源主题', reporting_period: '报告期间', reporting_entity: '数据主体', entity_aggregation_role: '主体层级', reporting_boundary: '统计边界', ghg_scope: '排放范围', ghg_method: '排放计算方法', scope2_method: '范围2方法', method: '计算方法', energy_carrier: '能源载体', energy_type: '能源种类', water_measure: '水量定义', water_risk_class: '水风险区域', pollutant: '污染物', pollution_medium: '排放介质', measurement_kind: '测量定义', sustainability_topic: '可持续主题', actual_or_target: '实际或目标'};
  const identities = (preview.identity_requirements || []).map(item => `${clauseName(item)}：${(item.identity_axes || []).map(axis => axisNames[axis] || '其他声明身份维度').join('、')}${item.required_dimensions?.length ? `；必要维度：${item.required_dimensions.map(d => d.label).join('、')}` : ''}`);
  const formulas = (preview.formula_operands || []).map(rule => `${clauseName(rule.target)}：${rule.operation === 'ratio' ? '比值' : '求和'}；${(rule.operands || []).map(operand => `${operand.source_clause ? clauseName(operand.source_clause) : operand.description}${operand.scheduled_as_internal_dependency ? '（内部依赖）' : operand.already_selected_output ? '（已在所选输出中）' : '（不会自动加入执行范围）'}`).join('；')}${rule.reported_value_priority ? '。直接披露优先，计算须满足方法、身份和单位匹配条件。' : ''}`);
  const excluded = (preview.exclusions || []).map(item => `${clauseName(item)}：${(item.measurement_exclusions || []).join('；')}`);
  const budget = preview.budget || {};
  const budgetRows = [["检索候选", budget.retrieval_object_top_n], ["初始共享对象", budget.shared_object_budget], ["Agent 步骤", budget.agent_max_steps], ["模型总调用上限", budget.max_model_calls], ["协议修复调用上限", budget.max_format_repairs], ["总输入 token 上限", budget.max_input_tokens], ["总输出 token 上限", budget.max_output_tokens], ["总时限（秒）", budget.total_timeout_seconds], ["单调用时限（秒）", budget.call_timeout_seconds]].filter(([, value]) => value != null).map(([label, value]) => `${label}：${value}`);
  const limitationNames = {"Document capabilities will be determined after OCR and basic IR preparation.": "文档可用范围将在解析与基础证据准备后确定。", "Only declared usable evidence can be consumed; coverage remains partial.": "只消费明确可用的局部证据，成果保留有限覆盖状态。"};
  const limitations = (preview.evidence_limitations || []).map((item) => limitationNames[item.message] || item.message || item.reason || (typeof item === "string" ? item : "后端报告了证据范围限制"));
  const sections = [[`所选输出（${preview.output_count ?? outputs.length} 个，保留原编号）`, outputs.length ? outputs : ['后台未提供条款名称，详见完整选择合同']], ["必要身份及测量口径", identities.length ? identities : ['按所选条款合同核对主体、期间、边界及方法']], ["共用条款与背景（不增加输出）", contexts.length ? contexts : ['所选条款未声明额外共用引用']], ["计算操作数（与背景条款分开）", formulas.length ? formulas : ['所选条款暂无声明的计算规则']], ["内部执行依赖（不会增加所选输出）", dependencyNames.length ? dependencyNames : ["所选要求暂无额外计算依赖"]], ["实际角色", roleRows], ["复用输入", reused.length ? reused : ["准备新文档证据"]], ["实际预算", budgetRows], ["已知证据限制", limitations.length ? limitations : ["后台未报告额外证据限制"]]];
  root.innerHTML = `<h3>后台执行计划</h3>${sections.map(([label, values]) => `<div class="preview-plan-section"><strong>${label}</strong><ul>${values.map((value) => `<li>${esc(value)}</li>`).join("")}</ul></div>`).join("")}${excluded.length ? `<details><summary>易混定义及排除条件</summary><ul>${excluded.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''}<details class="technical-details"><summary>完整计划与输入身份</summary><pre>${esc(JSON.stringify(preview, null, 2))}</pre></details><p class="hint">计划与请求固定后入队。只有明确选择的条款构成输出及覆盖分母，背景与操作数不会自动扩大范围。执行结束与覆盖完整度分别记录。</p>`;
}

function buildReportRequests() {
  if ($("#session-ir").value) return [buildReportRequest()];
  const selected = [...new Set([$("#session-report").value, ...[...$("#session-batch-reports").selectedOptions].map((option) => option.value)].filter(Boolean))];
  return selected.length ? selected.map((assetId) => buildReportRequest(assetId)) : [buildReportRequest()];
}

async function preview() {
  try {
    const requests = buildReportRequests();
    $("#session-preview-button").disabled = true;
    const plans = await Promise.all(requests.map((request) => postDocument("/api/report-sessions/preview", request)));
    previewRequest = JSON.stringify(requests);
    if (plans.length === 1) renderPreview(plans[0]);
    else {
      const root = $("#session-preview"); root.replaceChildren(); root.classList.remove("hidden");
      plans.forEach((plan, index) => { const section = document.createElement("section"); const name = document.createElement("h3"); name.textContent = requests[index].name; root.append(name, section); renderPreview(plan, section); });
    }
    $("#session-create").disabled = false;
    $("#session-message").textContent = "计划已由后端校验，可确认加入统一队列。";
  } catch (error) {
    invalidatePreview();
    $("#session-message").textContent = `计划预览失败：${error.message}`;
  } finally { $("#session-preview-button").disabled = false; }
}

async function createSession(event) {
  event.preventDefault();
  try {
    const requests = buildReportRequests();
    if (JSON.stringify(requests) !== previewRequest) throw new Error("配置已改变，请重新预览实际计划。");
    $("#session-create").disabled = true;
    let accepted = 0;
    const failed = [];
    for (const request of requests) {
      try {
        const result = await postDocument("/api/report-sessions", {...request, ...(request.role_config.cloud_authorization.allowed ? {qiniu_api_key: getSecret("qiniu_api_key"), ocr_token: getSecret("ocr_token")} : {})});
        const created = Array.isArray(result) ? result[0] : result.session || result;
        selectedSessionId = sessionId(created); accepted++;
        window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: created.task_id}}));
      } catch (error) { failed.push(`${request.name}：${error.message}`); }
    }
    $("#session-message").textContent = `已创建 ${accepted}/${requests.length} 个报告任务。${failed.length ? `未创建：${failed.join("；")}` : "有效事实形成后即可查看，后端继续推进未完成工作。"}`;
    invalidatePreview();
    await refreshSessions();
  } catch (error) { $("#session-message").textContent = `创建失败：${error.message}`; }
  finally { $("#session-create").disabled = !previewRequest; }
}

function sessionCard(session, includeResults = false) {
  const id = sessionId(session);
  return `<article class="session-card"><div class="section-heading"><strong>${esc(reportName(session))}</strong><span class="status-pill ${esc(execution(session))}">${esc(statusLabel(execution(session)))}</span></div><p>成果：${esc(statusLabel(resultState(session)))}</p>${session.counts ? stageSummaryView(session) : ""}${errorSummary(session)}<button type="button" class="secondary" data-session-open="${esc(id)}">${includeResults ? "查看成果与覆盖" : "查看进度与可用动作"}</button><div data-session-actions="${esc(id)}" class="button-row"></div></article>`;
}

function renderSessions() {
  $("#session-list").innerHTML = sessions.map((session) => sessionCard(session)).join("") || '<div class="empty-state">尚无报告任务。</div>';
  const pending = sessions.filter((session) => ["partial", "needs_confirmation", "not_evaluated"].includes(resultState(session)) && !isExecuting(execution(session)) || ["failed", "interrupted"].includes(execution(session)));
  $("#session-pending-list").innerHTML = pending.map((session) => sessionCard(session)).join("") || '<div class="empty-state">没有待处理报告任务。</div>';
  const results = sessions.filter((session) => resultState(session) !== "not_evaluated" || (session.job_ids || []).length);
  $("#session-result-list").innerHTML = results.map((session) => sessionCard(session, true)).join("") || '<div class="empty-state">尚无报告级成果；历史单包成果可在下方查看。</div>';
  document.querySelectorAll("[data-session-open]").forEach((button) => button.addEventListener("click", async () => {
    selectedSessionId = button.dataset.sessionOpen;
    if (activeView === "results") { selectedResultPerspective = "facts"; await showSessionFacts(selectedSessionId); }
    else {
      if (activeView !== "extract-tasks") window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "extract-tasks"}}));
      await refreshSessionDetail(true);
    }
  }));
  document.querySelectorAll("[data-session-actions]").forEach((root) => {
    const session = sessions.find((item) => sessionId(item) === root.dataset.sessionActions);
    renderAvailableActions(root, session?.available_actions || [], {sessionId: sessionId(session), onComplete: refreshSessions, onError: (error) => { $("#session-message").textContent = error.message; }});
  });
}

function renderSessionDetail(session) {
  const id = sessionId(session);
  const stages = Array.isArray(session.stages) ? session.stages : Object.entries(session.stages || {}).map(([key, value]) => ({name: key, ...value}));
  const jobs = Array.isArray(session.job_ids) ? session.job_ids : Object.values(session.job_ids || {}).flat();
  const actualStages = [...(session.preparation_stages || []), ...stages.filter(stage => !['report_session', 'targeted_extraction'].includes(stage.name))];
  const budget = session.execution_budget;
  const budgetView = budget ? `<p class="execution-budget">执行预算：已用 ${Math.round(budget.used_seconds || 0)} 秒 · 剩余 ${Math.round(budget.remaining_seconds || 0)} 秒 · 第 ${budget.attempt || 1} 次授权执行。排队与人工等待不计入执行预算。</p>` : '';
  const structuredError = errorSummary(session);
  $("#session-detail").innerHTML = `<div class="section-heading"><h3>${esc(reportName(session))}</h3><span>执行：${esc(statusLabel(execution(session)))} · 成果：${esc(statusLabel(resultState(session)))}</span></div>${session.counts ? stageSummaryView(session) : ""}${structuredError}${gapSummary(session.gaps || [])}${budgetView}<div class="session-stages">${actualStages.length ? stageTimeline(actualStages) : '<p class="muted">此历史记录未保存可恢复的阶段计数。</p>'}</div><div id="session-detail-actions" class="button-row"></div>${jobs.length ? `<h4>各标准成果</h4><div class="button-row">${jobs.map((job, index) => `<button type="button" class="secondary" data-session-job="${esc(typeof job === "string" ? job : job.job_id)}">查看标准成果 ${index + 1}</button>`).join("")}</div>` : ""}<details class="technical-details"><summary>角色、缺口与完整状态记录</summary><pre class="json">${esc(JSON.stringify(session, null, 2))}</pre></details>`;
  renderAvailableActions($("#session-detail-actions"), session.available_actions || [], {sessionId: id, onComplete: async () => { await refreshSessions(); await refreshSessionDetail(true); }, onError: (error) => { $("#session-message").textContent = error.message; }});
  $("#session-detail").querySelectorAll("[data-session-job]").forEach((button) => button.addEventListener("click", () => {
    window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "results"}}));
    window.dispatchEvent(new CustomEvent("esg:open-result", {detail: {jobId: button.dataset.sessionJob}}));
  }));
}

async function showSessionFacts(id) {
  selectedResultSessionId = id;
  const root = $("#session-report-results");
  root.innerHTML = '<div class="empty-state">正在读取报告事实…</div>';
  try {
    const payload = await docRequest(`/api/report-sessions/${encodeURIComponent(id)}/facts`);
    const detail = await docRequest(`/api/report-sessions/${encodeURIComponent(id)}`);
    publishTask(`session-result:${id}`, detail);
    const facts = payload.source_facts || payload.facts || [];
    const evidence = payload.evidence_references || facts.flatMap((fact) => fact.evidence || fact.evidence_references || fact.physical_sources || []);
    const gaps = mergeGaps(payload.gaps || [], detail.gaps || []);
    root.innerHTML = `<h3>${esc(reportName(detail))} · 报告成果</h3><div class="result-perspectives"><button class="secondary active" type="button" data-session-perspective="facts">报告事实</button><button class="secondary" type="button" data-session-perspective="coverage">标准覆盖</button><button class="secondary" type="button" data-session-perspective="evidence">证据</button></div><p class="hint">${esc(statusLabel(detail.result_status))} · ${detail.database_status === "not_imported" ? detail.counts?.core_facts === 0 ? "仅有来源候选，尚无可入库 Core 成果" : "合规 Core 成果尚未入库" : esc(detail.database_status || "数据库状态未记录")}</p>${detail.counts ? stageSummaryView(detail) : ""}${errorSummary(detail)}${gapSummary(gaps)}<div id="session-result-perspective-content"></div>`;
    const show = (view) => {
      const content = $("#session-result-perspective-content");
      if (view === "facts") content.innerHTML = reportFactsTable(facts);
      if (view === "evidence") content.innerHTML = evidenceTable(evidence);
      if (view === "coverage") {
        const projections = payload.projections || detail.projections || [];
        content.innerHTML = projections.map((projection) => `<article class="session-card"><strong>${esc(standardEntries.find((entry) => entry.catalog.package_id === projection.package_id)?.catalog.disclosure_requirement || projection.package_id?.split(".").at(-1).toUpperCase())}</strong><p>已选 ${projection.selected_metric_ids?.length || 0} 项要求。${projection.result_status ? `成果：${esc(statusLabel(projection.result_status))}。` : ""}具体覆盖以各标准成果为准。</p><button type="button" class="secondary" data-session-job="${esc(projection.job_id)}">查看覆盖与具体事实</button></article>`).join("") || '<div class="empty-state">标准投影尚未形成。</div>';
        content.querySelectorAll("[data-session-job]").forEach((button) => button.onclick = () => window.dispatchEvent(new CustomEvent("esg:open-result", {detail: {jobId: button.dataset.sessionJob}})));
      }
    };
    root.querySelectorAll("[data-session-perspective]").forEach((button) => button.onclick = () => { selectedResultPerspective = button.dataset.sessionPerspective; root.querySelectorAll("[data-session-perspective]").forEach((item) => item.classList.toggle("active", item === button)); show(selectedResultPerspective); });
    root.querySelectorAll("[data-session-perspective]").forEach((item) => item.classList.toggle("active", item.dataset.sessionPerspective === selectedResultPerspective));
    show(selectedResultPerspective);
  } catch (error) { root.innerHTML = `<div class="empty-state error">报告成果读取失败：${esc(error.message)}</div>`; }
}

async function refreshSessionDetail(force = false) {
  if (!selectedSessionId) return;
  const detail = await docRequest(`/api/report-sessions/${encodeURIComponent(selectedSessionId)}`);
  const value = detail.session || detail;
  if (publishTask(`session:${selectedSessionId}`, value) || force) renderSessionDetail(value);
}

async function refreshSessions(signal) {
  signal = signal instanceof AbortSignal ? signal : undefined;
  try {
    const payload = await docRequest("/api/report-sessions", {signal});
    sessions = listValue(payload, "sessions");
    const signature = JSON.stringify(sessions.map((session) => [sessionId(session), taskRevision(session)]));
    if (signature !== sessionListSignature) {
      sessionListSignature = signature; renderSessions();
      if (selectedResultSessionId && activeView === "results") {
        const current = sessions.find((session) => sessionId(session) === selectedResultSessionId);
        if (current && publishTask(`session-result:${selectedResultSessionId}`, current)) await showSessionFacts(selectedResultSessionId);
      }
    }
    if (selectedSessionId && activeView === "extract-tasks") {
      const current = sessions.find((session) => sessionId(session) === selectedSessionId);
      if (current && publishTask(`session:${selectedSessionId}`, current)) renderSessionDetail(current);
    }
  } catch (error) {
    if (signal?.aborted) return;
    const root = activeView === "pending" ? $("#session-pending-list") : activeView === "results" ? $("#session-result-list") : $("#session-list");
    root.innerHTML = `<div class="empty-state error">报告任务读取失败：${esc(error.message)}。已有成果未被删除。</div>`;
  }
}

function enterView(view) {
  activeView = view;
  unsubscribe("report-sessions");
  for (const panel of [creator, sessionPanel, pendingPanel, sessionResults]) panel.classList.toggle("view-hidden", panel.dataset.appView !== view);
  if (["extract-tasks", "pending", "results"].includes(view)) subscribe("report-sessions", refreshSessions, {interval: () => sessions.some((session) => isExecuting(execution(session))) ? 3000 : 15000});
  if (view === "extract-tasks" && !standardEntries.length) loadChoices();
}

$("#session-form").addEventListener("input", invalidatePreview);
$("#session-form").addEventListener("change", invalidatePreview);
$("#session-brain").addEventListener("change", () => $("#session-cloud-model-field").classList.toggle("hidden", $("#session-brain").value !== "qiniu_vlm"));
$("#session-report").addEventListener("change", () => { chosenAssetId = $("#session-report").value; chosenIrRunId = null; $("#session-ir").value = ""; updateChoiceAvailability(); });
$("#session-ir").addEventListener('change', () => { chosenIrRunId = $("#session-ir").value || null; updateChoiceAvailability(); });
$("#session-metric-search").addEventListener("input", () => {
  const query = $("#session-metric-search").value.toLowerCase().trim();
  $("#session-standards").querySelectorAll("[data-search]").forEach((node) => node.classList.toggle("hidden", !!query && !node.dataset.search.includes(query)));
});
$("#session-reload").addEventListener("click", loadChoices);
$("#session-select-major").addEventListener('click', () => {
  try { applyMajorSelection(); } catch (error) { $("#session-message").textContent = error.message; }
});
$("#session-clear-metrics").addEventListener('click', () => {
  selectedMetrics.clear(); clearSelectionSnapshot(); renderStandards(); invalidatePreview();
});
$("#session-refresh").addEventListener("click", refreshSessions);
$("#session-preview-button").addEventListener("click", preview);
$("#session-form").addEventListener("submit", createSession);
window.addEventListener("esg:viewchange", (event) => enterView(event.detail.view));
window.addEventListener("esg:select-report", async (event) => {
  chosenAssetId = event.detail.assetId;
  chosenIrRunId = event.detail.irRunId || null;
  if (assets.some((asset) => asset.asset_id === chosenAssetId)) $("#session-report").value = chosenAssetId;
  $("#session-ir").value = chosenIrRunId || '';
  restoreIrSelection(); updateChoiceAvailability();
  invalidatePreview();
  await loadChoices();
});
window.addEventListener('esg:select-session', async event => {
  selectedSessionId = event.detail.sessionId;
  window.dispatchEvent(new CustomEvent('esg:navigate', {detail: {view: 'extract-tasks'}}));
  try { await refreshSessionDetail(true); }
  catch (error) { $("#session-message").textContent = `所属报告会话读取失败：${error.message}。子成果及来源保留。`; }
});
window.addEventListener("esg:assets-changed", () => { if (activeView === "extract-tasks") loadChoices(); });
window.addEventListener("esg:queue-changed", () => { if (["extract-tasks", "pending", "results"].includes(activeView)) refreshSessions(); });
enterView(activeView);
