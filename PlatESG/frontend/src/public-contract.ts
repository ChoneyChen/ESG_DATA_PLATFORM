export function safePublicUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  if (
    /^\/(?:api\/)?v1\/sources\/[A-Za-z0-9_-]+\/document(?:\?dataset_version=[A-Za-z0-9_.%-]+)?$/.test(
      value,
    )
  ) {
    return value.startsWith("/api/") ? value : "/api" + value;
  }
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function publicCoverageLabel(status: string): string {
  return (
    (
      {
        published: "已发布",
        not_disclosed_in_reviewed_scope: "已审核范围内未披露",
        not_applicable: "不适用",
        source_unreadable: "来源无法读取",
        not_selected: "未纳入此版本范围",
        not_available: "此版本暂无数据",
      } as Record<string, string>
    )[status] || "此版本暂无数据"
  );
}
export function readStringArray(value: string | null): string[] {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed)
      ? ([
          ...new Set(
            parsed.filter((item: unknown) => typeof item === "string"),
          ),
        ] as string[])
      : [];
  } catch {
    return [];
  }
}
export function readDimensions(value: string | null): Record<string, string> {
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? Object.fromEntries(
          Object.entries(parsed).filter(
            (entry): entry is [string, string] => typeof entry[1] === "string",
          ),
        )
      : {};
  } catch {
    return {};
  }
}
