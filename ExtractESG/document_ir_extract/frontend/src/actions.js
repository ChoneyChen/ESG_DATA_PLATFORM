import {docRequest} from "./api.js";
import {getSecret} from "./roles.js";

function escape(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

/* An action exists only when the backend offers it. A page cannot infer an
 * expensive recovery operation from an error message or a status label. */
export function renderAvailableActions(root, actions = [], {sessionId, onComplete, onError} = {}) {
  root.innerHTML = actions.map((action, index) => {
    const label = action.label || action.title || action.action_type || action.type || action.action;
    const scope = typeof action.scope === "string" ? action.scope : action.description || (action.scope?.job_ids?.length ? `${action.scope.job_ids.length} 个未完成标准成果` : action.scope?.target_ids?.length ? `${action.scope.target_ids.length} 个受影响证据对象` : "本报告任务");
    return `<div class="available-action"><button type="button" class="secondary" data-available-action="${index}" ${action.enabled === false ? "disabled" : ""}>${escape(label)}</button><small>${escape(scope)}${action.calls_model || action.requires_model ? " · 调用模型" : ""}${action.incurs_cost || action.additional_cost || action.cost_hint ? ` · ${escape(action.cost_hint || "可能新增费用")}` : ""}</small></div>`;
  }).join("");
  root.querySelectorAll("[data-available-action]").forEach((button) => button.addEventListener("click", async () => {
    const action = actions[Number(button.dataset.availableAction)];
    const kind = action.action_type || action.type || action.action || action.id;
    const endpoint = action.endpoint || action.href || action.api_path || (sessionId && ["resume", "cancel"].includes(kind) ? `/api/report-sessions/${encodeURIComponent(sessionId)}/${kind}` : null);
    if (!endpoint || !endpoint.startsWith("/api/")) { onError?.(new Error("后端动作缺少可执行接口")); return; }
    if ((kind === "cancel" || action.requires_confirmation || action.requires_approval) && !window.confirm(action.confirmation_message || `${action.label || "执行此动作"}？已有成果会保留。`)) return;
    button.disabled = true;
    try {
      if (action.requires_reference_check) {
        if (!action.preflight_endpoint) throw new Error("来源检查动作缺少接口");
        const checked = await docRequest(action.preflight_endpoint);
        if (!(checked.available_actions || []).some((item) => (item.action || item.action_type) === kind))
          throw new Error((checked.blockers || []).map((item) => item.message).join("；") || "当前来源不允许此操作");
      }
      const needsCloudCredential = kind === "resume" || action.payload?.review_provider === "qiniu" || (action.calls_model && action.incurs_cost);
      const payload = {...(action.payload || {}), ...(needsCloudCredential ? {qiniu_api_key: getSecret("qiniu_api_key")} : {})};
      if (action.requires_reason) {
        const reason = window.prompt("请填写操作原因；归档保留来源和成果，丢弃进入可恢复回收区。", action.reason || "");
        if (reason === null) return;
        if (!reason.trim()) throw new Error("操作原因不能为空");
        payload.reason = reason.trim();
      }
      const method = (action.method || "POST").toUpperCase();
      const result = method === "GET" ? await docRequest(endpoint) : await docRequest(endpoint, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}, 30000);
      if (kind === "review") window.dispatchEvent(new CustomEvent("esg:navigate", {detail: {view: "pending"}}));
      await onComplete?.(result);
      window.dispatchEvent(new CustomEvent("esg:queue-changed", {detail: {taskId: result?.task_id}}));
    } catch (error) { onError?.(error); }
    finally { button.disabled = false; }
  }));
}
