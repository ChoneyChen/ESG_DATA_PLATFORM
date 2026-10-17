const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
export function valueText(value) {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.map(valueText).filter(Boolean).join(" / ");
  if (typeof value === "object") return value.raw || value.value_raw || value.label || value.value || JSON.stringify(value);
  return String(value);
}
const first = (...values) => values.find((value) => value != null && value !== "");
const cell = (value) => `<td>${esc(valueText(value)) || '<span class="empty-mark">—</span>'}</td>`;

export function stageSummaryView(summary = {}, overview = {}) {
  const counts = summary.counts || {};
  const selected = counts.selected_metrics ?? overview.metric_count ?? 0;
  const processed = counts.processed_metrics ?? overview.processed_count ?? 0;
  const core = counts.core_facts ?? ((overview.quantitative_fact_count || 0) + (overview.qualitative_fact_count || 0));
  const candidates = counts.candidate_source_facts ?? overview.candidate_source_fact_count ?? 0;
  const processing = counts.processing_metrics ?? overview.processing_count ?? 0;
  const unfinished = counts.unfinished_metrics ?? overview.unfinished_count ?? 0;
  const pending = counts.not_executed_metrics ?? overview.not_executed_count ?? 0;
  return `<p class="hint stage-summary">已处理 ${esc(processed)} / ${esc(selected)} 项 · 来源候选 ${esc(candidates)} 条 · 有效 Core 事实 ${esc(core)} 条 · 处理中 ${esc(processing)} 项 · 已进入未完成 ${esc(unfinished)} 项 · 未执行 ${esc(pending)} 项。处理进度不代表事实可用；未执行不表示未披露。</p>`;
}

export function gapSummary(gaps = []) {
  if (!gaps.length) return "";
  return `<details class="stage-gaps" open><summary>待解决 ${gaps.length} 个局部问题</summary><ul>${gaps.map((gap) => `<li>${esc(gap.reason || gap.message || gap.kind || "待确认")}${gap.object_id ? ` · ${esc(gap.object_id)}` : ""}</li>`).join("")}</ul></details>`;
}

function factQuality(fact, legacy) {
  const quality = {grounded: "来源已绑定", needs_confirmation: "来源身份待确认", unbound: "来源未绑定"}[fact.quality_status] || "来源质量未记录";
  const status = legacy ? "历史标准成果" : fact.source_fact_id ? "来源候选（未批准）" : "标准投影";
  const issues = (fact.quality_issues || []).map((issue) => issue.message || issue.code).filter(Boolean).join("；");
  const raw = Object.keys(fact.unverified_identity_raw || {}).length ? `<details><summary>未验证的身份声明</summary><pre>${esc(JSON.stringify(fact.unverified_identity_raw, null, 2))}</pre></details>` : "";
  return `<td>${esc([status, fact.quality_status ? quality : "", issues].filter(Boolean).join(" · "))}${raw}</td>`;
}

export function factCells(fact) {
  const identity = fact.identity_raw || fact.identity || fact.semantic_identity || {};
  const raw = fact.raw || {};
  const dimensions = fact.dimensions || fact.open_dimensions || [];
  const dimensional = Array.isArray(dimensions) ? dimensions.map((dimension) => valueText(dimension.value_raw ?? dimension.value ?? dimension.label)).filter(Boolean).join(" / ") : valueText(dimensions);
  const period = first(fact.reporting_period_raw, fact.period_raw, identity.reporting_period, identity.period, fact.reporting_year, [identity.period_start || fact.period_start, identity.period_end || fact.period_end].filter(Boolean).join(" 至 "));
  return [
    first(fact.source_row_label, fact.source_row_label_raw, raw.source_row_label, identity.source_row_label),
    period, first(fact.entity_raw, fact.reporting_entity_raw, fact.entity, identity.reporting_entity, identity.entity),
    first(fact.entity_role_raw, fact.entity_role, identity.entity_aggregation_role, identity.entity_role),
    [first(fact.scope_raw, fact.scope, identity.scope, identity.ghg_scope), first(fact.method_raw, fact.method, fact.accounting_method, identity.method, identity.scope_2_method), first(fact.category_raw, fact.category, identity.category, identity.scope3_category), ...["reporting_boundary", "aggregation_role", "measurement_kind", "carrier", "flow", "pollutant", "medium", "site", "business_segment", "denominator", "additional_breakdown"].map((key) => valueText(identity[key])), dimensional].filter(Boolean).join(" / "),
    first(fact.value_raw, raw.value, fact.statement_raw, fact.statement_summary, fact.display_value, fact.value),
    first(fact.unit_raw, raw.unit, fact.unit),
  ];
}

export function evidenceText(evidence) {
  return (evidence || []).map((item) => {
    const index = first(item.pdf_page_index, item.page_index);
    const page = first(item.pdf_page_number, index != null ? index + 1 : null);
    return [page != null ? `PDF 第 ${page} 页` : "", item.printed_page_label ? `报告页 ${item.printed_page_label}` : "", item.evidence_excerpt || item.excerpt || item.text || ""].filter(Boolean).join(" · ");
  }).join("；");
}

export function reportFactsTable(facts = [], {query = "", legacy = false} = {}) {
  const rows = facts.filter((fact) => !query || JSON.stringify(fact).toLowerCase().includes(query.toLowerCase())).map((fact) => {
    const evidence = fact.evidence || fact.evidence_references || fact.physical_sources || [];
    return `<tr>${factCells(fact).map(cell).join("")}${cell(evidenceText(evidence))}${factQuality(fact, legacy)}</tr>`;
  }).join("");
  if (!rows) return '<div class="empty-state">当前范围尚无可展示事实；执行停止不代表报告未披露。</div>';
  return `${legacy ? '<p class="hint">历史成果按标准投影保留原始行；没有报告级源事实合同，不推断跨指标重复关系。</p>' : ""}<table class="data-table report-facts-table"><thead><tr><th>源行主题</th><th>期间</th><th>实体</th><th>实体角色</th><th>Scope / 方法 / 类别</th><th>数值或陈述</th><th>单位</th><th>证据</th><th>阶段与质量</th></tr></thead><tbody>${rows}</tbody></table>`;
}

export function evidenceTable(evidence = [], query = "") {
  const rows = evidence.filter((item) => !query || JSON.stringify(item).toLowerCase().includes(query.toLowerCase())).map((item) => {
    const index = first(item.pdf_page_index, item.page_index);
    const page = first(item.pdf_page_number, index != null ? index + 1 : null);
    return `<tr>${cell(page != null ? `PDF 第 ${page} 页` : "")}${cell(item.printed_page_label)}${cell(item.evidence_excerpt || item.excerpt || item.text)}${cell(item.section_title || item.source_row_label)}<td><details><summary>来源定位</summary><pre>${esc(JSON.stringify({ir_object_id: item.ir_object_id || item.object_id, bbox: item.bbox || {x0:item.bbox_x0,y0:item.bbox_y0,x1:item.bbox_x1,y1:item.bbox_y1}, evidence_id: item.evidence_id, source_fact_id: item.source_fact_id, source_hash: item.source_hash}, null, 2))}</pre></details></td></tr>`;
  }).join("");
  return rows ? `<table class="data-table"><thead><tr><th>PDF 页</th><th>报告页</th><th>证据原文</th><th>位置描述</th><th>来源</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty-state">当前范围尚无证据引用。</div>';
}
