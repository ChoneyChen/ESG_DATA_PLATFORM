export function reportIdentity(label, fallback = "未命名报告") {
  const raw = String(label || fallback).trim();
  const displayName = raw.replace(/\.pdf$/i, "") || fallback;
  const year = displayName.match(/(?:19|20)\d{2}/)?.[0] || "年份未识别";
  return {displayName, year};
}

export function runDate(runId, fallback = null) {
  const match = String(runId || "").match(/-(\d{8})T(\d{6})Z-/);
  if (match) {
    const date = match[1];
    const time = match[2];
    return `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)} ${time.slice(0, 2)}:${time.slice(2, 4)}`;
  }
  const timestamp = typeof fallback === "number" ? fallback * 1000 : Date.parse(fallback || "");
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleString("zh-CN", {hour12: false})
    : "时间未知";
}

export function ocrProviderName(run) {
  const provider = run?.summary?.ocr_provider || run?.progress_detail?.provider;
  if (provider === "local_paddleocr") return "本地 PaddleOCR-VL";
  if (provider === "paddle_api") return "PaddleOCR-VL API";
  return provider || "Provider 未记录";
}

export function readinessName(value) {
  return {
    ready: "已准入",
    ready_with_warnings: "可用但有警告",
    auto_review_pending: "自动复核待处理",
    repair_required: "需要修复链路",
    review_required: "需要复核",
    building: "构建中",
  }[value] || value || "状态未知";
}

export function searchableText(...values) {
  return values.flat(Infinity).filter((value) => value !== null && value !== undefined)
    .join(" ").toLocaleLowerCase("zh-CN");
}
