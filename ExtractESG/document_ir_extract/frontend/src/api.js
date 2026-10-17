/* The workbench has one transport contract. Failed reads stay failed; they never
 * become an empty catalogue. Only successful GETs with the same URL are shared. */
const inFlight = new Map();

export class ApiError extends Error {
  constructor(message, {status = 0, detail = null, url = "", cause} = {}) {
    super(message, {cause});
    this.name = "ApiError";
    Object.assign(this, {status, detail, url});
  }
}

export function serviceBase(service = "document") {
  const id = service === "targeted" ? "tx-backend-url" : "backend-url";
  const key = service === "targeted" ? "esg-targeted-backend-url" : "esg-v2-backend-url";
  const fallback = service === "targeted" ? "http://127.0.0.1:18180" : "http://127.0.0.1:18080";
  return (document.getElementById(id)?.value || localStorage.getItem(key) || fallback).replace(/\/$/, "");
}

function notifyError(error) {
  window.dispatchEvent(new CustomEvent("esg:api-error", {detail: error}));
}

export async function apiRequest(service, path, options = {}, timeoutMs = 15000) {
  const {responseType = "json", ...fetchOptions} = options;
  const url = /^https?:\/\//.test(path) ? path : `${serviceBase(service)}${path}`;
  const method = (fetchOptions.method || "GET").toUpperCase();
  const key = `${method}:${responseType}:${url}`;
  const share = method === "GET" && !fetchOptions.signal;
  if (share && inFlight.has(key)) return inFlight.get(key);
  const operation = (async () => {
    const controller = new AbortController();
    const abort = () => controller.abort(fetchOptions.signal?.reason);
    if (fetchOptions.signal?.aborted) abort();
    else fetchOptions.signal?.addEventListener("abort", abort, {once: true});
    const deadline = setTimeout(() => controller.abort("timeout"), timeoutMs || 15000);
    try {
      const response = await fetch(url, {...fetchOptions, signal: controller.signal});
      const raw = await response.text();
      let payload = null;
      try { payload = raw ? JSON.parse(raw) : null; } catch (_) {}
      if (!response.ok) {
        const detail = payload?.detail ?? payload;
        const message = typeof detail === "string" ? detail : detail?.message || `HTTP ${response.status} ${response.statusText}`;
        throw new ApiError(message, {status: response.status, detail, url});
      }
      if (responseType === "text") return raw;
      if (raw && payload === null) throw new ApiError("服务返回了无效 JSON", {status: response.status, url});
      return payload;
    } catch (cause) {
      const intentional = fetchOptions.signal?.aborted;
      const error = cause instanceof ApiError ? cause : new ApiError(
        cause.name === "AbortError" ? (intentional ? "请求已取消" : "服务请求超时") : "服务连接失败，请检查后端状态",
        {url, cause},
      );
      if (!intentional) notifyError(error);
      throw error;
    } finally {
      clearTimeout(deadline);
      fetchOptions.signal?.removeEventListener("abort", abort);
    }
  })();
  if (share) inFlight.set(key, operation);
  try { return await operation; } finally { if (inFlight.get(key) === operation) inFlight.delete(key); }
}

export const docRequest = (path, options = {}, timeoutMs) => apiRequest("document", path, options, timeoutMs);
export const targetedRequest = (path, options = {}, timeoutMs) => apiRequest("targeted", path, options, timeoutMs);
export const postDocument = (path, payload = {}) => docRequest(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}, 30000);

export function installApiFeedback() {
  const notice = document.createElement("div");
  notice.id = "api-error-notice";
  notice.className = "api-error-notice hidden";
  notice.setAttribute("role", "alert");
  document.querySelector(".hero")?.after(notice);
  window.addEventListener("esg:api-error", (event) => {
    notice.replaceChildren();
    const message = document.createElement("span");
    message.textContent = `${event.detail.message}。已有记录保留，刷新可重试。`;
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "secondary";
    dismiss.textContent = "关闭";
    dismiss.onclick = () => notice.classList.add("hidden");
    notice.append(message, dismiss);
    notice.classList.remove("hidden");
  });
}
