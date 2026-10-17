// Read-only rendering of backend/package relationships; no fact sorting here.
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const roles = {anchor:"锚点条款", detail:"延伸披露", context:"方法/说明", derived:"直接披露优先/可派生", presentation:"结构/展示"};
const relations = {decomposes:"分解", method_variant:"并列方法", separate_disclosure:"独立披露", calculation_dependency:"计算依赖", explains:"方法/对账说明", presentation_of:"展示投影", alternative_group:"替代口径", conditional_activation:"条件适用", requires:"依赖", contextualizes:"背景关联"};
export function providerLabel(id, model) {
  return id === "qiniu_vlm" ? `七牛云 · ${model || "后端配置模型"}` : id === "local_qwen" ? "本地 Qwen3.5（通用视觉语义）" : id === "local_nuextract" ? "本地 NuExtract3" : id || "未记录";
}
export function topicBadge(metric) {
  return metric.disclosure_topic ? `<small title="${esc(metric.disclosure_topic)}">${esc(roles[metric.disclosure_role] || metric.disclosure_role)} · ${esc(metric.disclosure_topic.split(".topic.").pop())}</small>` : "";
}
export function disclosureGraph(standard) {
  if (!["2.0", "2.1"].includes(standard?.format_version)) return "";
  const names = new Map((standard.metrics || []).map(m => [m.metric_id, m.source_datapoint_id]));
  const label = ids => (ids || []).map(id => names.get(id) || id).join("、");
  return `<details><summary>条款关系 · ${(standard.relations || []).length} 组（各独立条款按所选范围执行）</summary><ul>${(standard.relations || []).map(r => `<li>${esc(relations[r.relation_type] || r.relation_type)}：${esc(label(r.source_metric_ids?.length ? r.source_metric_ids : r.member_metric_ids))}${r.target_metric_ids?.length ? " → " + esc(label(r.target_metric_ids)) : ""}<small> ${esc(r.description)}</small></li>`).join("")}</ul></details>`;
}
export function disclosureProgress(plan, artifactUrl) {
  if (!(plan?.topics || []).length) return "";
  const projections = plan.projections || [];
  const agentPlan = plan.version === "disclosure-plan-agent-v1";
  const sources = [...new Set(projections.map(p => p.source_artifact).filter(path => typeof path === "string" && path))];
  const links = agentPlan ? ["results/source-facts.json", "results/metric-source-mappings.json", "agent/state.json"] : sources;
  return `<details><summary>主题证据覆盖：${plan.topics.length} 个主题 · ${projections.length} 次指标映射</summary><p>每主题初始共享预算 ${esc(plan.shared_object_budget)}；每次指标检索 / 补漏候选 Top ${esc(plan.per_metric_gap_top_n)}。共享理解用于保持语义一致，不等于减少模型调用；有界覆盖不代表报告穷尽。</p>${plan.topics.map(t => `<p><strong>${esc(t.topic_id.split(".topic.").pop())}</strong> · ${t.metric_ids.length} 项 · ${["shared_source","source_agent","report_fact_agent"].includes(t.route) ? "共享源事实 / 语义理解" : "兼容逐指标"} · ${agentPlan ? `候选池 ${t.unique_candidate_objects || 0} 个对象` : `已选 ${t.shared_objects?.length || 0}/${t.unique_candidate_objects || 0} 个初始共享候选`}${t.unselected_dependencies?.length ? " · 未选依赖：" + esc(t.unselected_dependencies.join("、")) : ""}</p>`).join("")}<ul>${links.map(path => `<li><a href="${esc(artifactUrl(path))}">${esc(path.split("/").slice(-2).join("/"))}：源行、语义映射及执行记录</a></li>`).join("")}</ul><pre class="json compact-json">${esc(JSON.stringify(projections, null, 2))}</pre></details>`;
}

export function agentProgress(agent, request = {}) {
  if (!agent) return "";
  const states = {complete:"本次有界流程完成",topic_complete:"当前主题有界流程完成",completed:"本次有界流程完成",bounded:"达到预算，仍有覆盖缺口",budget_exhausted:"达到预算，仍有覆盖缺口",partial:"部分完成",running:"执行中",planning:"规划中",resuming:"恢复执行中",failed:"失败",interrupted:"已中断",cancelled:"已取消"};
  const tools = {agent_plan:"规划下一步证据动作",repair_format:"局部格式修复",search_report:"缺口检索",inspect_object:"理解证据对象与指标映射",map_object:"使用已理解对象补做指标映射",search:"检索候选",search_missing:"缺口检索",read_context:"补充上下文",interpret_source:"理解源表",source_understanding:"理解源表",map_metrics:"指标映射",metric_mapping:"指标映射",finish:"结束本次有界提取"};
  const phases = {started:"已开始",loading:"加载模型",prefill:"读取输入",generation:"生成输出",request:"请求云端模型",completed:"调用已结束",failed:"调用失败",interrupted:"调用已中断",cancelled:"调用已取消"};
  const stepStates = {running:"执行中",completed:"已完成",failed:"失败",duplicate_action:"重复动作，未执行"};
  const steps = agent.steps || [], gaps = agent.unresolved_metrics || [];
  const listValue = value => Array.isArray(value) ? value.join("、") : value || "—";
  const countValue = value => Array.isArray(value) ? value.length : value ?? "未记录";
  const call = agent.current_call;
  return `<section class="agent-progress-card">
    <h3>源表语义 Agent · ${esc(states[agent.state] || agent.state || "状态未记录")}</h3>
    <div class="agent-progress-metrics">
      <span>已记录动作 <strong>${steps.length}</strong></span><span>每主题步骤预算 <strong>${esc(request.agent_max_steps ?? 8)}</strong></span>
      <span>Agent 实际模型调用 <strong>${esc(agent.model_calls ?? "未记录")}</strong></span>
      <span>已理解对象 <strong>${esc(countValue(agent.interpreted_objects))}</strong></span><span>已记录映射关联 <strong>${esc(countValue(agent.mapped_rows))}</strong></span>
    </div>
    ${call ? `<p class="${call.phase === "failed" ? "error-text" : "muted"}">最近模型调用 ${esc(call.attempt ?? "—")}：${esc(tools[call.tool] || call.tool || "未记录任务")} · ${esc(phases[call.phase] || call.phase || "阶段未记录")} · ${esc(providerLabel(call.provider, call.model_id))}${call.object_id ? ` · 对象 ${esc(call.object_id)}` : ""}${call.generation_tokens != null ? ` · 已生成 ${esc(call.generation_tokens)} token` : ""}${call.elapsed_seconds != null ? ` · ${esc(call.elapsed_seconds)} 秒` : ""}${call.error ? ` · ${esc(call.error)}` : ""}</p>` : ""}
    <p class="${gaps.length ? "warning-text" : "muted"}">${gaps.length ? `尚未明确覆盖：${esc(listValue(gaps))}。有限搜索未找到不等于报告未披露。` : "本次执行范围按预算受限；完成状态不是对整份报告无遗漏的证明。"}</p>
    <p class="muted">调用数包含规划、理解、映射及失败调用；映射关联数不是独立事实数量。</p>
    ${agent.error ? `<p class="error-text">${esc(agent.error)}</p>` : ""}
    <details ${["running","planning","resuming"].includes(agent.state) ? "open" : ""}><summary>执行步骤、证据对象与上下文缺口</summary><div class="agent-step-list">${steps.map(s => `<article>
      <strong>${esc(s.step)} · ${esc(tools[s.tool] || s.tool || "步骤")} · ${esc(stepStates[s.status] || s.status || "未记录")}</strong>
      <small>对象 ${esc(s.object_id || "—")} · 指标 ${esc(listValue(s.metric_ids))}${s.evidence_phase ? ` · ${s.evidence_phase === "initial" ? "初始证据" : s.evidence_phase === "gap" ? "补漏证据" : esc(s.evidence_phase)}` : ""}${s.elapsed_seconds != null ? ` · ${esc(s.elapsed_seconds)} 秒` : ""}</small>
      ${s.query ? `<p>检索问题：${esc(s.query)}</p>` : ""}${s.found_objects ? `<p>检索后的证据对象池：${esc(listValue(s.found_objects))}</p>` : ""}
      ${s.reason ? `<p>${esc(s.reason)}</p>` : ""}${s.missing_context?.length ? `<p>缺少上下文：${esc(listValue(s.missing_context))}</p>` : ""}${s.error ? `<p class="error-text">${esc(s.error)}</p>` : ""}
      ${s.coverage_summary?.length ? `<details><summary>此对象的逐指标覆盖</summary><ul>${s.coverage_summary.map(c => `<li>${esc(c.metric_id)} · ${c.object_reviewed ? "已审阅此对象" : "仍不明确"} · 匹配行 ${esc(c.matched_rows ?? 0)} · 不明确行 ${esc(c.uncertain_rows ?? 0)}${c.missing_context ? ` · ${esc(c.missing_context)}` : ""}</li>`).join("")}</ul></details>` : ""}
    </article>`).join("") || '<p class="muted">尚无已记录步骤。</p>'}</div></details>
  </section>`;
}
