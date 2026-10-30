export type Service = "document" | "extraction";
const prefixes: Record<Service, string> = {
  document: "/document",
  extraction: "/extraction",
};
export class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "RequestError";
  }
}
function message(value: unknown, status: number): string {
  const detail =
    value && typeof value === "object" && "detail" in value
      ? value.detail
      : value;
  if (
    status === 503 &&
    /restart|code changed|重启/i.test(JSON.stringify(detail))
  )
    return "服务代码已更新，请在运行页检查并重启服务。";
  if (status === 404) return "这个对象不存在或已被清理，请返回上一级刷新。";
  if (status === 409 && /changed|digest|reload/i.test(JSON.stringify(detail)))
    return "内容已变化，请重新载入并核对后提交。";
  if (status >= 500) return "服务暂时无法完成请求，请稍后重试。";
  if (Array.isArray(detail))
    return detail
      .map((x) =>
        x && typeof x === "object" && "msg" in x
          ? String(x.msg)
          : "输入不符合要求",
      )
      .join("；");
  const text = typeof detail === "string" ? detail : "请检查输入后重试。";
  return /traceback|authorization|api[_-]?key|bearer|\bfile\s+"/i.test(text)
    ? "请求未通过服务检查。"
    : text.slice(0, 400);
}
export async function request<T>(
  service: Service,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//") || /[\r\n]/.test(path))
    throw new Error("接口地址无效");
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(
    () => {
      timedOut = true;
      controller.abort();
    },
    options.method && options.method !== "GET" ? 45000 : 15000,
  );
  try {
    const response = await fetch(prefixes[service] + path, {
      ...options,
      headers,
      signal: controller.signal,
    });
    if (response.status === 204) return undefined as T;
    let body: unknown;
    try {
      body = await response.json();
    } catch (error) {
      if (controller.signal.aborted) throw error;
      throw new RequestError(
        "服务返回了无法识别的响应，请重试。",
        response.status,
      );
    }
    if (!response.ok)
      throw new RequestError(
        message(body, response.status),
        response.status,
        response.headers.get("X-Request-ID") || undefined,
      );
    return body as T;
  } catch (error) {
    if (options.signal?.aborted)
      throw new DOMException("Request cancelled", "AbortError");
    if (timedOut)
      throw new RequestError(
        "请求等待超时。修改操作请先核对结果；提取任务可用同一次提交安全重试。",
        0,
      );
    if (error instanceof RequestError) throw error;
    throw new RequestError("无法连接服务，请到运行页检查连接。", 0);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
export const write = <T>(
  service: Service,
  path: string,
  body: unknown,
  method = "POST",
) => request<T>(service, path, { method, body: JSON.stringify(body) });
export const segment = (value: string) => encodeURIComponent(value);
export const pdfUrl = (asset: string, page?: number) =>
  `/document/api/report-assets/${segment(asset)}/file${page ? `#page=${page}` : ""}`;
