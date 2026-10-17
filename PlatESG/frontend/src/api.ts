const API_PREFIX = "/api";

export function apiUrl(
  path: string,
  params: Record<string, string | number | undefined | null> = {},
) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "")
      search.set(key, String(value));
  }
  return `${API_PREFIX}${path}${search.size ? `?${search}` : ""}`;
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    throw new Error(
      response.ok
        ? "数据服务未返回 JSON，请检查 API 服务与代理配置。"
        : `数据服务暂时无法访问（${response.status}）。`,
    );
  }
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail ?? body.message;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `请求未完成（${response.status}）。`,
    );
  }
  return body as T;
}
