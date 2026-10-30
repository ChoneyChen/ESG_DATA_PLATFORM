export const labels: Record<string, string> = {
  targeted_extraction: "条款提取",
  document_ir: "文档证据",
  ocr: "页面识别",
  queued: "等待执行",
  running: "执行中",
  completed: "执行完成",
  reused: "已复用",
  partial: "已结束 · 部分结果",
  failed: "失败",
  cancelled: "已取消",
  cancelling: "正在取消",
  interrupted: "中断",
  ready: "可提取",
  blocked: "待完善",
  quantitative: "定量",
  qualitative: "定性",
  assertion: "定性",
  covered: "已覆盖",
  unconfirmed: "待确认",
  not_found: "未找到证据",
  not_covered: "未覆盖",
  match: "符合",
  uncertain: "待确认",
  different: "其他目标",
  unmapped: "未匹配",
  organized: "已组织",
  needs_confirmation: "待确认",
  pending: "等待结果",
  empty: "无数据",
  not_stated: "未陈述",
  not_read: "未读取",
  conflict: "冲突",
  available: "有值",
  not_applicable: "不适用",
  stated: "原文明示",
  inherited: "上下文继承",
  grounded: "已定位",
  recognized: "已识别",
  unlisted: "词典未收录",
  unproven: "缺少证据",
  template: "模板",
  sample: "组织示例",
};
export const label = (key: string | null | undefined) =>
  key ? labels[key] || key : "—";
export const tone = (
  status: string | undefined,
): "neutral" | "success" | "warning" | "error" =>
  ["failed", "interrupted", "conflict"].includes(status || "")
    ? "error"
    : [
          "completed",
          "ready",
          "covered",
          "match",
          "organized",
          "grounded",
        ].includes(status || "")
      ? "success"
      : [
            "partial",
            "blocked",
            "unconfirmed",
            "needs_confirmation",
            "uncertain",
            "cancelling",
          ].includes(status || "")
        ? "warning"
        : "neutral";
export const text = (value: unknown): string =>
  value == null
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
export function periodText(value: unknown): string {
  if (value == null) return "期间未知";
  if (typeof value !== "object") return String(value);
  const p = value as Record<string, unknown>;
  if (p.kind === "quarter" && p.year && p.quarter) return `${p.year} Q${p.quarter}`;
  if (p.kind === "annual" && p.year) return String(p.year);
  return typeof p.raw === "string" ? p.raw : "期间未知";
}
export const date = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
export function download(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
