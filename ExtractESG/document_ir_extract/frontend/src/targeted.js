const $ = (selector) => document.querySelector(selector);
const statusLabels = {
  found: "已找到",
  not_applicable: "条件不适用",
  not_found: "未找到",
  uncertain: "不确定",
  system_failed: "系统失败",
};

let activeRunId = "";
let activeState = null;
let results = [];
let selectedRequirementId = "";
let pollTimer = null;
let elapsedTimer = null;

function backendUrl() {
  return $("#backend-url").value.trim().replace(/\/$/, "");
}

function endpoint(path) {
  return `${backendUrl()}${path}`;
}

async function fetchJson(path, fallback = undefined) {
  try {
    const response = await fetch(endpoint(path));
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  } catch (error) {
    if (fallback !== undefined) return fallback;
    throw error;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"})[character]);
}

function metric(value, label) {
  return `<div class="targeted-metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
}

async function refreshHealth() {
  const payload = await fetchJson("/health", null);
  $("#health").textContent = payload ? "Backend: online" : "Backend: offline";
  $("#health").classList.toggle("online", Boolean(payload));
  $("#health").classList.toggle("offline", !payload);
}

async function refreshIrRuns() {
  const runs = await fetchJson("/api/document-ir/jobs", []);
  const select = $("#ir-run-id");
  const previous = select.value;
  select.innerHTML = '<option value="">选择 can_build_evidence=true 的 IR</option>';
  runs.forEach((run) => {
    const admitted = run.summary?.can_build_evidence === true;
    const option = document.createElement("option");
    option.value = run.run_id;
    option.disabled = !admitted;
    option.dataset.admitted = admitted ? "true" : "false";
    option.dataset.readiness = run.summary?.readiness || run.status;
    option.textContent = `${run.run_id} · ${admitted ? "可进入 Evidence" : "未准入"} · ${run.summary?.readiness || run.status}`;
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === previous && !option.disabled)) select.value = previous;
  renderAdmission();
}

function renderAdmission() {
  const option = $("#ir-run-id").selectedOptions[0];
  const note = $("#ir-admission");
  if (!option?.value) {
    note.className = "admission-note";
    note.textContent = "选择后显示 Evidence 准入状态。未准入 IR 不会被下游强行读取。";
    return;
  }
  const ready = option.dataset.admitted === "true";
  note.className = `admission-note ${ready ? "ready" : ""}`;
  note.textContent = ready ? `准入通过：${option.dataset.readiness}，可以构建或复用 Evidence Inventory。` : "准入未通过：请先完成 Document IR 阻塞复核。";
}

async function refreshRuns() {
  const runs = await fetchJson("/api/targeted-fill/jobs", []);
  const select = $("#run-history");
  const previous = activeRunId || select.value;
  select.innerHTML = '<option value="">选择 Targeted Run</option>';
  runs.forEach((run) => {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.run_id} · ${run.status} · ${run.mode}`;
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

async function refreshCapabilities() {
  const payload = await fetchJson("/api/targeted-fill/capabilities", null);
  if (!payload) {
    $("#local-capabilities").textContent = "本地能力读取失败。";
    return;
  }
  const retrieval = payload.retrieval || {};
  const semantic = payload.local_semantic || {};
  const deterministic = payload.deterministic_pipeline || {};
  $("#local-capabilities").textContent = `SQLite ${retrieval.sqlite_version || "?"} · FTS5 ${retrieval.fts5 ? "可用" : "不可用"} · trigram ${retrieval.trigram ? "可用" : "不可用"} · 披露目录 ${deterministic.disclosure_catalog ? "启用" : "未知"} · 槽位核验 ${deterministic.slot_verifier ? "启用" : "未知"} · 本地语义依赖 ${semantic.sentence_transformers_installed ? "已安装" : "未安装（将降级）"}`;
}

async function planTemplate() {
  const file = $("#task-file").files[0];
  if (!file) return alert("请先选择标准任务表。");
  const form = new FormData();
  form.append("file", file);
  if ($("#ir-run-id").value) form.append("ir_run_id", $("#ir-run-id").value);
  $("#plan-state").textContent = "正在编译任务表...";
  try {
    const response = await fetch(endpoint("/api/targeted-fill/plan"), {method: "POST", body: form});
    if (!response.ok) throw new Error(await response.text());
    renderPlan(await response.json());
  } catch (error) {
    $("#plan-state").textContent = `预检失败：${error.message}`;
  }
}

function renderPlan(plan) {
  const kinds = plan.counts_by_kind || {};
  $("#plan-state").textContent = `${plan.adapter_id} · 预检完成`;
  $("#plan-metrics").innerHTML = [
    metric(plan.requirement_count, "任务条数"),
    metric(kinds.conditional_absence || 0, "条件式条款"),
    metric(kinds.table_bundle || 0, "表格条款"),
    metric(plan.ir_admission?.can_build_evidence ? "通过" : "未通过", "IR 准入"),
    metric("0 / ¥0", "云调用 / 费用"),
  ].join("");
  $("#plan-warnings").innerHTML = (plan.warnings || []).length
    ? plan.warnings.map((warning) => `<div class="issue">${escapeHtml(warning)}</div>`).join("")
    : '<div class="issue">模板契约通过；正式运行仍需通过 IR 准入与结果 Guard。</div>';
  $("#plan-requirements").innerHTML = (plan.requirements || []).map((item) => `
    <div class="plan-row"><span>#${item.sequence}</span><strong>${escapeHtml(item.data_point_id)}</strong><span>${escapeHtml(item.official_text)}</span><span>${escapeHtml(item.concept_id || "unclassified")}</span><span>${escapeHtml(item.requirement_kind)}</span><span>${(item.required_slots || []).filter((slot) => slot.required).length} slots</span></div>
  `).join("");
}

async function startRun(event) {
  event.preventDefault();
  const file = $("#task-file").files[0];
  const irRunId = $("#ir-run-id").value;
  if (!file || !irRunId) return alert("请选择已准入 IR 和标准任务表。");
  const form = new FormData();
  form.append("file", file);
  form.append("ir_run_id", irRunId);
  form.append("mode", $("#run-mode").value);
  form.append("top_k", $("#top-k").value);
  form.append("local_embedding_model", $("#embedding-model").value);
  form.append("allow_local_model_download", $("#allow-model-download").checked ? "true" : "false");
  try {
    const response = await fetch(endpoint("/api/targeted-fill/jobs"), {method: "POST", body: form});
    if (!response.ok) throw new Error(await response.text());
    const state = await response.json();
    activeRunId = state.run_id;
    activeState = state;
    $("#current-run").textContent = activeRunId;
    renderRuntime(state);
    await refreshRuns();
    pollActiveRun();
  } catch (error) {
    alert(`启动失败：${error.message}`);
  }
}

async function pollActiveRun() {
  clearTimeout(pollTimer);
  if (!activeRunId) return;
  activeState = await fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(activeRunId)}`, null);
  if (!activeState) return;
  renderRuntime(activeState);
  if (["queued", "running"].includes(activeState.status)) {
    pollTimer = setTimeout(pollActiveRun, 1200);
  } else if (activeState.status === "done") {
    await loadRun(activeRunId);
    await refreshRuns();
  }
}

function renderRuntime(state) {
  $("#current-run").textContent = state.run_id;
  $("#run-logs").textContent = (state.logs || []).join("\n") || state.message || "等待任务。";
  const summary = state.summary || {};
  $("#runtime-metrics").innerHTML = [
    metric(state.status, "任务状态"),
    metric(formatDuration(state.started_at, state.finished_at), "运行耗时"),
    metric(summary.requirement_count || "-", "任务条数"),
    metric(summary.cloud_call_count ?? 0, "云调用次数"),
    metric(`¥${summary.cloud_cost_cny ?? 0}`, "云费用"),
    metric(summary.local_model_inference_count ?? 0, "本地模型批次"),
    metric(state.mode, "请求模式"),
  ].join("");
  clearInterval(elapsedTimer);
  if (state.status === "running") elapsedTimer = setInterval(() => renderRuntime({...state}), 1000);
}

function formatDuration(start, finish) {
  if (!start) return "-";
  const elapsed = Math.max(0, new Date(finish || Date.now()).getTime() - new Date(start).getTime());
  const seconds = Math.floor(elapsed / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

async function loadRun(runId) {
  activeRunId = runId;
  const [state, manifest, answerRows, validation, artifacts] = await Promise.all([
    fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}`),
    fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}/manifest`),
    fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}/results`, []),
    fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}/validation-report`, {}),
    fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}/artifacts`, {files: []}),
  ]);
  activeState = state;
  results = answerRows;
  $("#targeted-workbench").classList.remove("hidden");
  $("#targeted-manifest").textContent = JSON.stringify(manifest, null, 2);
  $("#download-export").href = endpoint(`/api/targeted-fill/jobs/${encodeURIComponent(runId)}/export`);
  renderRuntime(state);
  renderResultMetrics(validation.status_counts || manifest.status_counts || {});
  $("#validation-banner").className = `validation-banner ${validation.valid ? "" : "invalid"}`;
  $("#validation-banner").textContent = validation.valid
    ? "结果 Guard 已通过：正向结论的必填槽位完整、索引页未替代正文证据、事实实例可追溯，云调用为零，可以导出。"
    : `结果 Guard 未通过：${(validation.issues || []).map((item) => item.code).join("、")}`;
  renderResultList();
  renderArtifacts(artifacts.files || []);
}

function renderResultMetrics(counts) {
  $("#result-metrics").innerHTML = [
    metric(counts.found || 0, "已找到"),
    metric(counts.not_applicable || 0, "条件不适用"),
    metric(counts.not_found || 0, "未找到"),
    metric(counts.uncertain || 0, "不确定"),
    metric(counts.system_failed || 0, "系统失败"),
  ].join("");
}

function renderResultList() {
  const query = $("#result-search").value.trim().toLowerCase();
  const status = $("#status-filter").value;
  const filtered = results.filter((item) => {
    const matchesStatus = !status || item.status === status;
    const haystack = `${item.data_point_id} ${item.status} ${item.quote} ${item.reasoning}`.toLowerCase();
    return matchesStatus && (!query || haystack.includes(query));
  });
  $("#result-list").innerHTML = filtered.length ? filtered.map((item) => `
    <button type="button" class="targeted-result-card ${item.requirement_id === selectedRequirementId ? "selected" : ""}" data-requirement-id="${escapeHtml(item.requirement_id)}">
      <span class="row-id">Row ${item.source_row}</span>
      <span class="data-point">${escapeHtml(item.data_point_id)}</span>
      <span class="summary">${escapeHtml(item.reasoning)}</span>
      <span class="status-chip ${item.status}">${statusLabels[item.status] || item.status}</span>
    </button>
  `).join("") : '<div class="empty-review-queue">没有符合筛选条件的结果。</div>';
  document.querySelectorAll(".targeted-result-card").forEach((button) => button.addEventListener("click", () => loadRequirement(button.dataset.requirementId)));
}

async function loadRequirement(requirementId) {
  selectedRequirementId = requirementId;
  renderResultList();
  const bundle = await fetchJson(`/api/targeted-fill/jobs/${encodeURIComponent(activeRunId)}/requirements/${encodeURIComponent(requirementId)}`);
  const {
    profile,
    answer,
    hits = [],
    selected_evidence: selectedEvidence,
    disclosure_groups: disclosureGroups = [],
    verifications = [],
    search_coverage: searchCoverage = {},
  } = bundle;
  const atoms = selectedEvidence?.atoms || [];
  const slotCoverage = answer.slot_coverage || [];
  const facts = answer.facts || [];
  $("#result-detail").innerHTML = `
    <p class="eyebrow">Requirement evidence</p><h3>${escapeHtml(answer.data_point_id)}</h3>
    <div class="detail-grid">
      <div><strong>最终状态</strong><span class="status-chip ${answer.status}">${statusLabels[answer.status]}</span></div>
      <div><strong>内部结论</strong><span>${escapeHtml(answer.internal_decision || "-")}</span></div>
      <div><strong>判断难度</strong><span>${answer.easy_to_judge ? "容易" : "不容易"} · ${Math.round(answer.confidence * 100)}%</span></div>
      <div><strong>任务类型</strong><span>${escapeHtml(profile.requirement_kind)}</span></div>
      <div><strong>披露类型</strong><span>${escapeHtml(profile.disclosure_type || "-")}</span></div>
      <div><strong>概念 ID</strong><span>${escapeHtml(profile.concept_id || "-")}</span></div>
      <div><strong>编译策略</strong><span>${escapeHtml(profile.execution_strategy || "-")}</span></div>
      <div><strong>多值策略</strong><span>${escapeHtml(profile.multiplicity || "-")}</span></div>
      <div><strong>位置</strong><span>${escapeHtml(answer.location || "无")}</span></div>
      <div><strong>来源节点</strong><span>${escapeHtml((answer.selected_source_node_ids || []).join(", ") || "无")}</span></div>
    </div>
    <div class="decision-box"><strong>官方 Data Point</strong><blockquote>${escapeHtml(profile.official_text)}</blockquote></div>
    ${(profile.compiler_notes || []).length ? `<div class="decision-box"><strong>编译说明</strong><p>${escapeHtml(profile.compiler_notes.join("；"))}</p></div>` : ""}
    <div class="decision-box"><strong>系统判断</strong><blockquote>${escapeHtml(answer.value)}</blockquote><p>${escapeHtml(answer.reasoning)}</p><div class="route-row">${(answer.decision_routes || []).map((route) => `<span>${escapeHtml(route)}</span>`).join("")}</div></div>
    <h4>任务槽位覆盖</h4>
    ${renderSlotCoverage(slotCoverage, profile.required_slots || [])}
    <h4>检索覆盖账本</h4>
    ${renderSearchCoverage(searchCoverage)}
    <h4>最终事实实例</h4>
    ${renderFacts(facts)}
    <h4>选中的披露组</h4>
    ${renderDisclosureGroups(disclosureGroups)}
    <h4>选中证据</h4>
    ${atoms.length ? atoms.map((atom) => `<div class="evidence-box"><strong>${escapeHtml(atom.atom_type)} · ${escapeHtml(atom.atom_id)}</strong><div class="meta-strip"><span class="chip">PDF ${escapeHtml((atom.location?.page_numbers || []).join(", "))}</span><span class="chip">${escapeHtml((atom.location?.section_path || []).join(" > "))}</span></div><pre>${escapeHtml(atom.source_text)}</pre></div>`).join("") : '<div class="empty-review-queue">该状态没有选中证据；请查看完整检索轨迹。</div>'}
    <h4>候选披露组 Top 15</h4>
    ${renderCandidateHits(hits)}
    <h4>独立候选核验</h4>
    ${renderVerifications(verifications)}
  `;
}

function renderSlotCoverage(coverage, requiredSlots) {
  const rows = coverage.length ? coverage : requiredSlots.map((slot) => ({
    slot_id: slot.slot_id,
    required: slot.required,
    satisfied: false,
    matched_values: [],
    reason: "未生成覆盖结果",
  }));
  if (!rows.length) return '<div class="empty-review-queue">该任务没有声明结构化槽位。</div>';
  return `<div class="slot-grid">${rows.map((slot) => `
    <div class="slot-card ${slot.satisfied ? "" : "missing"}">
      <strong>${slot.satisfied ? "已覆盖" : "未覆盖"} · ${escapeHtml(slot.slot_id)}${slot.required ? " · 必填" : " · 可选"}</strong>
      <span>${escapeHtml((slot.matched_values || []).join("、") || "无匹配值")}</span>
      <small>${escapeHtml(slot.reason || "-")}</small>
    </div>`).join("")}</div>`;
}

function renderSearchCoverage(coverage) {
  if (!coverage || !Object.keys(coverage).length) return '<div class="empty-review-queue">历史结果包没有检索覆盖账本。</div>';
  const entries = [
    [coverage.candidate_group_count ?? 0, "候选披露组"],
    [coverage.prelimit_candidate_group_count ?? coverage.candidate_group_count ?? 0, "截断前候选组"],
    [coverage.candidate_limit ?? "-", "本次候选上限"],
    [coverage.candidate_limit_reached ? "是" : "否", "召回被上限截断"],
    [coverage.verified_group_count ?? 0, "已核验组"],
    [coverage.complete_group_count ?? 0, "完整组"],
    [coverage.partial_group_count ?? 0, "部分组"],
    [coverage.irrelevant_group_count ?? 0, "无关组"],
    [coverage.not_applicable_group_count ?? 0, "不适用组"],
    [coverage.ambiguous_group_count ?? 0, "歧义组"],
    [coverage.index_rejected_count ?? 0, "索引组淘汰"],
    [(coverage.selected_group_ids || []).length, "最终选中组"],
    [coverage.all_retrieved_candidates_verified ? "是" : "否", "召回候选已判完"],
    [coverage.search_exhausted ? "是" : "否", "无充分候选后收敛"],
  ];
  return `<div class="coverage-ledger">${entries.map(([value, label]) => `<div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("")}</div>`;
}

function renderFacts(facts) {
  if (!facts.length) return '<div class="empty-review-queue">该结论没有形成可发布事实实例；这对“未找到/不确定”属于正常结果。</div>';
  return `<div class="fact-list">${facts.map((fact) => `<div class="fact-card"><strong>${escapeHtml(fact.concept_id)}</strong><dl>
    <div><dt>Value</dt><dd>${escapeHtml(fact.value || "-")}</dd></div>
    <div><dt>Numeric</dt><dd>${escapeHtml((fact.numeric_values || []).join("、") || "-")}</dd></div>
    <div><dt>Unit</dt><dd>${escapeHtml(fact.unit_family || "-")}</dd></div>
    <div><dt>Period</dt><dd>${escapeHtml(fact.period || "-")}</dd></div>
    <div><dt>Dimensions</dt><dd>${escapeHtml(formatDimensions(fact.dimensions))}</dd></div>
    <div><dt>Source group</dt><dd>${escapeHtml(fact.source_group_id || "-")}</dd></div>
    <div><dt>Fact ID</dt><dd>${escapeHtml(fact.fact_id || "-")}</dd></div>
    <div><dt>Evidence</dt><dd>${escapeHtml((fact.evidence_atom_ids || []).join(", ") || "-")}</dd></div>
  </dl></div>`).join("")}</div>`;
}

function renderDisclosureGroups(groups) {
  if (!groups.length) return '<div class="empty-review-queue">没有选中披露组。</div>';
  return `<div class="group-list">${groups.map((group) => `<div class="group-card"><strong>${escapeHtml(group.group_id)} · ${escapeHtml(group.group_type)}</strong>
    <div class="route-row"><span>PDF ${(group.page_numbers || []).join(", ") || "-"}</span><span>${group.features?.table_like ? "表格型" : "叙述型"}</span><span>${group.features?.index_like ? "索引型" : "正文型"}</span></div>
    <p class="diagnostic-line">数值：${escapeHtml((group.features?.numeric_values || []).join("、") || "无")} · 单位族：${escapeHtml((group.features?.unit_families || []).join("、") || "无")} · 年份：${escapeHtml((group.features?.years || []).join("、") || "无")}</p>
    <p class="diagnostic-line">Atoms：${escapeHtml((group.atom_ids || []).join(", "))}</p>
  </div>`).join("")}</div>`;
}

function renderCandidateHits(hits) {
  if (!hits.length) return '<div class="empty-review-queue">没有召回候选披露组。</div>';
  return `<div class="hit-box"><table class="hit-table"><thead><tr><th>披露组 / 代表 Atom</th><th>分数</th><th>粗覆盖</th><th>评分组成</th><th>惩罚 / 命中</th></tr></thead><tbody>${hits.slice(0, 15).map((hit) => `<tr>
    <td><strong>${escapeHtml(hit.group_id || "-")}</strong><br>${escapeHtml(hit.atom_id || "-")}</td>
    <td>${Number(hit.fused_score || 0).toFixed(4)}</td>
    <td>${Math.round(Number(hit.coverage_ratio || 0) * 100)}%</td>
    <td class="score-components">${escapeHtml(formatScoreComponents(hit.score_components || {}))}</td>
    <td>${escapeHtml((hit.penalties || []).join("、") || (hit.matched_terms || []).join("、") || "-")}</td>
  </tr>`).join("")}</tbody></table></div>`;
}

function renderVerifications(verifications) {
  if (!verifications.length) return '<div class="empty-review-queue">历史结果包没有独立候选核验记录。</div>';
  const sorted = [...verifications].sort((a, b) => Number(b.verifier_score || 0) - Number(a.verifier_score || 0));
  return `<div class="hit-box"><table class="hit-table"><thead><tr><th>披露组</th><th>结论</th><th>槽位覆盖</th><th>事实数</th><th>核验说明</th></tr></thead><tbody>${sorted.map((item) => `<tr>
    <td>${escapeHtml(item.group_id)}</td>
    <td class="verdict-${escapeHtml(item.decision)}">${escapeHtml(item.decision)} · ${Number(item.verifier_score || 0).toFixed(3)}</td>
    <td>${Math.round(Number(item.coverage_ratio || 0) * 100)}%</td>
    <td>${(item.facts || []).length}</td>
    <td>${escapeHtml((item.reasons || []).join("；") || "全部声明槽位已通过")}</td>
  </tr>`).join("")}</tbody></table></div>`;
}

function formatDimensions(dimensions = {}) {
  return Object.entries(dimensions).map(([key, values]) => `${key}: ${(values || []).join("/")}`).join(" · ") || "-";
}

function formatScoreComponents(components = {}) {
  return Object.entries(components).map(([key, value]) => `${key}=${Number(value).toFixed(2)}`).join(" · ") || "-";
}

function renderArtifacts(files) {
  $("#targeted-artifacts").innerHTML = files.map((file) => `<a class="artifact-link" href="${endpoint(file.url)}" target="_blank" rel="noreferrer">${escapeHtml(file.path)} · ${file.size} B</a>`).join("");
}

async function refreshAll() {
  await Promise.all([refreshHealth(), refreshIrRuns(), refreshRuns(), refreshCapabilities()]);
}

$("#backend-url").addEventListener("change", () => {
  localStorage.setItem("esg-v2-backend-url", backendUrl());
  refreshAll();
});
$("#refresh-all").addEventListener("click", refreshAll);
$("#ir-run-id").addEventListener("change", renderAdmission);
$("#run-mode").addEventListener("change", () => $("#semantic-options").classList.toggle("hidden", $("#run-mode").value !== "local_semantic"));
$("#plan-button").addEventListener("click", planTemplate);
$("#targeted-form").addEventListener("submit", startRun);
$("#run-history").addEventListener("change", (event) => event.target.value && loadRun(event.target.value));
$("#result-search").addEventListener("input", renderResultList);
$("#status-filter").addEventListener("change", renderResultList);

const storedBackend = localStorage.getItem("esg-v2-backend-url");
if (storedBackend) $("#backend-url").value = storedBackend;
refreshAll();
