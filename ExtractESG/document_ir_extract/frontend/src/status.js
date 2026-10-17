const labels = {
  queued: "排队中", running: "执行中", stopping: "正在取消，等待执行退出", cancelling: "正在取消，等待执行退出", interrupted: "已中断", failed: "失败", cancelled: "已取消", finished: "执行结束",
  completed: "执行结束", done: "执行结束", partial: "部分成果", usable: "事实可用", needs_confirmation: "待确认",
  no_evidence_in_bound: "预算范围内无证据", not_evaluated: "尚未评估", active: "执行中", pending: "待执行", paused: "暂停后续步骤",
  needs_attention: "需要处理", waiting_review: "等待文档复核", found: "已找到", not_found: "未找到", ambiguous: "不确定", reported: "直接披露",
  not_found_after_complete_search: "完整检索后未找到", not_found_after_bounded_search: "有限证据中未找到", retrieval_uncertain: "召回充分性不确定",
  auto_verified: "历史自动通过", human_required: "需人工判断", system_contract_failed: "结果合同失败", passed: "通过", warning: "警告",
  decided: "模型已裁决", unresolved: "语义未收敛", ready: "展示身份完整", needs_semantic_completion: "语义身份待补全", not_applicable: "不适用", not_run: "未执行",
  processing: "处理中", unfinished: "已进入未完成", not_executed: "未执行", evaluated: "已处理", candidate: "来源候选（未批准）", grounded: "来源已绑定",
  unbound: "来源未绑定", source_candidates: "仅有来源候选", core_partial: "部分 Core 成果", no_facts: "尚无事实",
  protocol_invalid: "输出协议失败", generation_incomplete: "输出被截断", no_fact_records: "无事实记录可验证",
};
export const statusLabel = (status) => labels[status] || status || "未产生";
export const isExecuting = (status) => ["queued", "running", "active", "pending", "stopping", "cancelling"].includes(status);
export const isTerminal = (status) => ["finished", "completed", "done", "partial", "failed", "cancelled", "interrupted"].includes(status);
export const progressStages = ["准备文档", "理解披露", "补充证据", "整理事实", "形成结果"];

export function taskRevision(task) {
  // Queue heartbeats change revisions/timestamps without changing the business
  // view. Counts, gaps, errors, call phases and durable outcomes still refresh.
  const transient = new Set(["created_at", "updated_at", "heartbeat_at", "last_heartbeat_at", "elapsed_seconds", "wall_time_seconds"]);
  const stable = (value, depth = 0) => Array.isArray(value) ? value.map((item) => stable(item, depth + 1)) : value && typeof value === "object"
    ? Object.fromEntries(Object.keys(value).sort().filter((key) => !transient.has(key) && !(depth === 0 && ["revision", "state_revision"].includes(key)))
      .map((key) => [key, stable(value[key], depth + 1)])) : value;
  return JSON.stringify(stable(task || {}));
}
