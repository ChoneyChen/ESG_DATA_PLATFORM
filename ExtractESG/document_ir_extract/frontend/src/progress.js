// One rendering contract for queue, report and document progress.
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
const stages = {
  queued:"等待执行", starting:"启动执行", running:"正在执行", stopping:"正在取消", completed:"执行完成",
  partial:"执行结束，有待处理成果", failed:"执行失败", interrupted:"执行中断", cancelled:"已取消",
  ocr:"文档识别", document_ir:"结构与证据构建", report_session:"报告定向提取", targeted_extraction:"条款提取",
  retrieval:"检索报告证据", source_understanding:"理解来源", interpret_source:"理解来源", mapping:"条款映射",
  map_metrics:"条款映射", materialization:"整理事实", export:"保存阶段成果", repair_format:"修复输出协议",
  reconnected:"恢复观测已有任务", resource_lifecycle:"资源与执行时间检查", archived:"已归档", restored:"已恢复记录",
  lifecycle:"记录与来源管理", quality:"检查证据可用范围", ir:"结构与证据构建",
};
export const irStageLabels = ["加载已固定的识别成果", "渲染报告页面", "检查原始页面与文本", "构建正文与表格结构",
  "整理阅读顺序与章节", "连接表格与跨页结构", "生成区域证据", "连接横向跨页证据", "按缺口复核与修复",
  "重建并核对修正结构", "验证并保存证据版本"];
export function stageLabel(code, fallback = "执行阶段待识别") {
  if (stages[code]) return stages[code];
  if (String(code || "").startsWith("ocr_")) return "文档识别";
  if (String(code || "").startsWith("ir.build.stage.")) return irStageLabels[Number(code.split(".").at(-1)) - 1] || "结构与证据构建";
  if (String(code || "").startsWith("ir.")) return `证据定向修复阶段 ${code.split(".").at(-1)}`;
  return /[\u4e00-\u9fff]/.test(code || "") ? code : fallback;
}
const stoppedStates = new Set(["completed", "finished", "partial", "failed", "interrupted", "cancelled", "archived", "discarded"]);
const activeCallPhases = new Set(["started", "loading", "prefill", "generation", "request", "evaluating"]);
export function historicalActiveCall(record, call) {
  return !!call && stoppedStates.has(record?.execution_status || record?.status || record?.progress?.status) && activeCallPhases.has(call.phase);
}
// Categories are supplied by the producer. English error strings are audit
// material, never inputs to a second browser-side classification algorithm.
export function taskErrorView(record, extraClass = "") {
  const detail = record?.error_detail;
  const raw = [...new Set([detail?.technical_detail, ...record?.errors || [], record?.error,
    detail?.message && !/[\u4e00-\u9fff]/.test(detail.message) ? detail.message : null]
    .filter(Boolean).map(value => typeof value === "string" ? value : JSON.stringify(value, null, 2)))];
  if (!detail && !raw.length) return "";
  const categories = {runtime:"执行失败，已有成果保留", protocol:"输出格式未满足合同，已有成果保留",
    source:"来源证据存在问题，请检查受影响对象", identity:"主体、期间或口径身份不完整",
    mapping:"条款映射尚未满足要求", publication:"正式发布条件尚未满足", dependency:"执行依赖尚未就绪"};
  const message = /[\u4e00-\u9fff]/.test(detail?.message || "") ? detail.message :
    categories[detail?.category] || "执行遇到问题，已有记录保留；请查看技术详情与可用操作";
  const actions = {refresh:"刷新服务状态", resume:"恢复未完成动作", inspect:"查看诊断", compatibility:"检查服务版本",
    configure_credentials:"配置执行凭据", inspect_identity:"核对主体、期间与口径", review_mapping:"审核条款映射",
    review_publication:"检查发布条件", inspect_source:"检查受影响来源"};
  return `<div class="task-error-summary error-text ${escape(extraClass)}"><strong class="task-error-message">${escape(message)}</strong>${detail?.next_action ? `<span> · 建议：${escape(actions[detail.next_action] || "查看受影响对象与可用动作")}</span>` : ""}${raw.length ? `<details class="task-error-technical technical-details"><summary>技术原因与原始记录</summary><pre>${escape(raw.join("\n\n"))}</pre></details>` : ""}</div>`;
}
export function progressView(record) {
  const progress = record?.progress;
  if (!progress) return `<div class="business-progress"><span>${escape(stageLabel(record?.stage || record?.name))}</span><small>当前没有可计算的工作总量</small></div>`;
  const unit = {pages:"页", steps:"已完成构建阶段", requirements:"已处理条款", objects:"对象"}[progress.unit] || "工作项";
  const determinate = progress.determinate === true && Number.isFinite(progress.percent) && progress.total > 0;
  const call = progress.current_call;
  const callStatus = {started:"调用开始", loading:"加载模型", prefill:"处理输入", generation:"生成输出", completed:"调用完成", failed:"调用失败", interrupted:"调用中断", cancelled:"调用取消"};
  const callTime = call && Number.isFinite(call.elapsed_seconds) ? ` · 已记录耗时 ${Math.round(call.elapsed_seconds)} 秒` : call ? " · 耗时尚未记录" : "";
  const callLabel = historicalActiveCall(record, call) ? "历史调用记录，任务已停止" : callStatus[call?.phase] || "调用状态待确认";
  return `<div class="business-progress"><span>${escape(progress.label || stageLabel(progress.stage_code))}</span>${determinate
    ? `<small>${escape(progress.current)} / ${escape(progress.total)} ${unit}</small><div class="progress" role="progressbar" aria-label="${escape(progress.label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.percent}"><span style="width:${Math.min(100, Math.max(0, progress.percent))}%"></span></div>`
    : `<small>${["running","queued"].includes(record.execution_status || record.status) ? "执行中；当前阶段没有可靠总量" : "此阶段未记录可计算的工作总量"}</small>`}${call ? `<small>模型动作：${escape(stageLabel(call.tool, "理解当前证据"))}${call.object_id ? " · " + escape(call.object_id) : ""} · ${escape(callLabel)}${escape(callTime)}</small>` : ""}${record.progress_error ? `<small class="error">${escape(record.progress_error.message)}</small>` : ""}</div>`;
}
export function timingView(timing) {
  if (!timing) return "";
  const seconds = value => `${Math.max(0, Math.round(Number(value) || 0))} 秒`;
  return `<div class="execution-timing"><span>排队 ${seconds(timing.queue_wait_seconds)}</span><span>执行墙钟 ${seconds(timing.wall_elapsed_seconds)}</span><span>已观测执行 ${seconds(timing.active_observed_seconds)}</span>${timing.unobserved_seconds ? `<span>未连续观测 ${seconds(timing.unobserved_seconds)}</span>` : ""}${timing.sleep_or_clock_adjustment_seconds ? `<span>睡眠或时钟差异 ${seconds(timing.sleep_or_clock_adjustment_seconds)}</span>` : ""}</div>`;
}
export function stageTimeline(stages = []) {
  return stages.map(stage => `<article class="session-stage ${escape(stage.status || stage.execution_status || "pending")}"><strong>${escape(/[\u4e00-\u9fff]/.test(stage.label || '') ? stage.label : stageLabel(stage.stage_code || stage.name || stage.stage))}</strong><span>${escape({running:"执行中", queued:"等待执行", completed:"执行完成", done:"执行完成", interrupted:"执行中断", failed:"执行失败", cancelled:"已取消", partial:"有待处理成果", pending:"待执行"}[stage.status || stage.execution_status] || "状态待确认")}</span>${progressView(stage)}${timingView(stage.timing)}</article>`).join("");
}
