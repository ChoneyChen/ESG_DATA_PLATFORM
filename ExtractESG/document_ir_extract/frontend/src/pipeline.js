const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const base = () => ($("#backend-url")?.value || "http://127.0.0.1:18080").replace(/\/$/, "");
const targetBase = () => (localStorage.getItem("esg-targeted-backend-url") || "http://127.0.0.1:18180").replace(/\/$/, "");
let packages = [], assets = [], loaded = false;
const selectedAssets = new Set(), selectedMetrics = new Set();
const stateLabels = {active:"执行中",pending:"待执行",running:"执行中",queued:"排队中",completed:"已完成",failed:"失败",paused:"已暂停后续步骤",cancelled:"已取消",interrupted:"已中断",needs_attention:"部分步骤需处理",waiting_review:"等待 IR 复核"};

async function request(path, options = {}, target = false) {
  const response = await fetch(`${target ? targetBase() : base()}${path}`, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload));
  return payload;
}
const post = (path, payload) => request(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});

const panel = document.createElement("section");
panel.className = "card view-hidden";
panel.dataset.appView = "pipeline-plan";
panel.innerHTML = `<div class="section-heading"><h2>布置全链路计划</h2><button type="button" class="secondary" id="plan-refresh">刷新</button></div>
  <p class="muted">PDF → OCR → Document IR（含自动复核）→ 跨标准定向抽取 → 06 成果检查。复用成功产物；单份报告失败不影响其他报告。</p>
  <form id="plan-form">
    <label>计划名称<input id="plan-name" value="全链路提取" maxlength="120" required /></label>
    <div class="two-column">
      <section><h3>1 · 报告文件夹 / 资产</h3><label>导入文件夹中的 PDF<input id="plan-folder" type="file" webkitdirectory multiple /></label><small>导入至报告资产目录；不会删除或修改源文件夹。相同文件名不会覆盖。</small><div id="plan-assets" class="plan-choice-list"></div></section>
      <section><h3>2 · 标准与细分指标</h3><p class="muted">可同时勾选 E1-5、E1-6、E2-4 的任意细分条目。</p><div id="plan-standards" class="plan-choice-list"></div></section>
    </div>
    <h3>3 · 执行配置</h3><div class="form-grid">
      <label>OCR Provider<select id="plan-ocr"><option value="local_first">本地优先，API 回退</option><option value="local_paddleocr">仅本地 OCR</option><option value="paddle_api">Paddle API</option></select></label>
      <label>IR 复核模型<select id="plan-review"><option value="local_nuextract">本地 NuExtract3</option><option value="qiniu">七牛云（配置的复核模型）</option></select></label>
      <label>定向抽取模型<select id="plan-model"><option value="local_nuextract">本地 NuExtract3</option><option value="qiniu_vlm">七牛云 VLM</option></select></label>
      <label>七牛云抽取模型 ID（可选）<input id="plan-model-id" placeholder="留空使用后端配置" /></label>
      <label>主证据对象 Top N<select id="plan-top"><option>1</option><option selected>2</option><option>3</option><option>4</option><option>5</option></select></label>
      <label>本次七牛云 API Key<input id="plan-key" type="password" autocomplete="off" placeholder="不落盘；重启后使用环境变量" /></label>
      <label>本次 OCR API Token<input id="plan-token" type="password" autocomplete="off" /></label>
    </div>
    <p><label><input id="plan-reuse" type="checkbox" checked />复用已有成功 OCR / 可用 IR</label></p>
    <p><label><input id="plan-limited" type="checkbox" checked />允许隔离局部问题页继续抽取（明确保留缺漏，不绕过全局完整性错误）</label></p>
    <button id="plan-submit" type="submit">创建计划并加入统一队列</button>
  </form><p id="plan-message" role="status"></p><h3>已保存计划</h3><div id="plan-history"></div>`;
$("main").appendChild(panel);
const style = document.createElement("style");
style.textContent = ".plan-choice-list{max-height:360px;overflow:auto;margin:12px 0;padding:12px;border:1px solid var(--line,#ddd);border-radius:12px}.plan-choice-list label{display:flex;gap:8px;align-items:flex-start;padding:5px 0;font-size:13px}.plan-choice-list input[type=checkbox]{width:auto;flex:0 0 auto}.plan-choice-list details{padding:8px 0}.plan-record{padding:16px 0;border-top:1px solid #ddd}.plan-record pre{white-space:pre-wrap;font-size:12px}.plan-record .button-row{margin:8px 0}";
document.head.appendChild(style);

async function loadChoices() {
  const [catalog, standards] = await Promise.all([request("/api/report-assets"), request("/api/standards", {}, true)]);
  assets = catalog.assets || [];
  packages = await Promise.all(standards.map((s) => request(`/api/standards/${encodeURIComponent(s.package_id)}/${encodeURIComponent(s.package_version)}`, {}, true)));
  $("#plan-assets").innerHTML = assets.map((a) => `<label><input type="checkbox" data-plan-asset="${esc(a.asset_id)}" ${selectedAssets.has(a.asset_id)?"checked":""} />${esc(a.file_name)} · OCR ${esc(a.ocr_status)}</label>`).join("") || "请先导入 PDF。";
  $("#plan-standards").innerHTML = packages.map((p) => `<details open><summary>${esc(p.manifest.standard.disclosure_requirement)} · ${esc(p.manifest.package_version)}</summary>${p.metrics.map((m) => `<label><input type="checkbox" data-plan-metric="${esc(m.metric_id)}" ${selectedMetrics.has(m.metric_id)?"checked":""} />${esc(m.source_datapoint_id)} · ${esc(m.labels.zh || m.labels.en)}</label>`).join("")}</details>`).join("");
  panel.querySelectorAll("[data-plan-asset]").forEach((input) => input.onchange = () => input.checked ? selectedAssets.add(input.dataset.planAsset) : selectedAssets.delete(input.dataset.planAsset));
  panel.querySelectorAll("[data-plan-metric]").forEach((input) => input.onchange = () => input.checked ? selectedMetrics.add(input.dataset.planMetric) : selectedMetrics.delete(input.dataset.planMetric));
  loaded = true;
}

async function refreshPlans() {
  const plans = await request("/api/pipeline/plans");
  $("#plan-history").innerHTML = plans.map((p) => `<article class="plan-record"><strong>${esc(p.name)} · ${esc(stateLabels[p.status] || p.status)}</strong><small> · ${esc(p.plan_id)}</small><div class="button-row">${p.status === "active" ? `<button type="button" data-plan-action="pause" data-plan-id="${esc(p.plan_id)}">暂停后续步骤</button>` : !["completed","cancelled"].includes(p.status) ? `<button type="button" data-plan-action="resume" data-plan-id="${esc(p.plan_id)}">继续 / 重试失败步骤</button>` : ""}${!["completed","cancelled"].includes(p.status) ? `<button type="button" class="secondary" data-plan-action="cancel" data-plan-id="${esc(p.plan_id)}">取消</button>` : ""}</div>${p.documents.map((d) => `<details><summary>${esc(d.file_name)} · ${esc(stateLabels[d.status] || d.status)}${d.ir_reused?" · 已复用 IR":""}</summary><p>${esc(d.error || "")}</p><pre>${esc(JSON.stringify({ocr_run_id:d.ocr_run_id,ir_run_id:d.ir_run_id,steps:d.steps,evidence_policy:d.evidence_policy},null,2))}</pre></details>`).join("")}</article>`).join("") || '<p class="muted">尚无全链路计划。配置完成后点击创建。</p>';
  panel.querySelectorAll("[data-plan-action]").forEach((b) => b.onclick = async () => {
    if (b.dataset.planAction === "cancel" && !confirm("取消计划及未完成的子任务？已有成果保留。")) return;
    try { await post(`/api/pipeline/plans/${b.dataset.planId}/${b.dataset.planAction}`, {}); await refreshPlans(); }
    catch (e) { $("#plan-message").textContent = e.message; }
  });
}
$("#plan-folder").onchange = async (event) => {
  const files = [...event.target.files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  let added = 0, conflicts = [];
  for (const file of files) {
    const data = new FormData(); data.append("file", file);
    try { const asset = await request("/api/report-assets", {method:"POST",body:data}); if (asset.asset_id) selectedAssets.add(asset.asset_id); added++; }
    catch (e) { conflicts.push(`${file.name}: ${e.message}`); }
    $("#plan-message").textContent = `已导入 ${added}/${files.length} 个 PDF。`;
  }
  await loadChoices();
  $("#plan-message").textContent = `已导入 ${added} 个 PDF。${conflicts.join("；")}`;
};
$("#plan-form").onsubmit = async (event) => {
  event.preventDefault();
  const standards = packages.map((p) => ({package_id:p.manifest.package_id,package_version:p.manifest.package_version,metric_ids:p.metrics.filter((m) => selectedMetrics.has(m.metric_id)).map((m) => m.metric_id)})).filter((s) => s.metric_ids.length);
  const assetIds = assets.filter((a) => selectedAssets.has(a.asset_id)).map((a) => a.asset_id);
  if (!assetIds.length || !standards.length) { $("#plan-message").textContent = "请至少选择一份 PDF 和一个指标。"; return; }
  $("#plan-submit").disabled = true;
  try {
    const plan = await post("/api/pipeline/plans", {name:$("#plan-name").value.trim(),asset_ids:assetIds,standards,ocr_provider:$("#plan-ocr").value,review_provider:$("#plan-review").value,semantic_provider:$("#plan-model").value,semantic_model:$("#plan-model-id").value.trim() || null,retrieval_object_top_n:Number($("#plan-top").value),reuse_existing:$("#plan-reuse").checked,allow_limited_evidence:$("#plan-limited").checked,qiniu_api_key:$("#plan-key").value.trim() || null,ocr_token:$("#plan-token").value.trim() || null});
    $("#plan-key").value = ""; $("#plan-token").value = "";
    $("#plan-message").textContent = `计划 ${plan.plan_id} 已保存，后端自动推进依赖步骤。结果见 06 抽取结果。`;
    await refreshPlans();
  } catch (e) { $("#plan-message").textContent = e.message; }
  finally { $("#plan-submit").disabled = false; }
};
$("#plan-refresh").onclick = async () => { try { await loadChoices(); await refreshPlans(); } catch(e) { $("#plan-message").textContent=e.message; } };
$("[data-app-view-target=pipeline-plan]").addEventListener("click", async () => { try { if(!loaded) await loadChoices(); await refreshPlans(); } catch(e) { $("#plan-message").textContent=e.message; } });
setInterval(() => { if (!panel.classList.contains("view-hidden")) refreshPlans().catch((e) => { $("#plan-message").textContent=e.message; }); }, 5000);
if (localStorage.getItem("esg-v2-active-view") === "pipeline-plan") {
  panel.classList.remove("view-hidden");
  loadChoices().then(refreshPlans).catch((e) => { $("#plan-message").textContent=e.message; });
}
