import type { ReactNode } from "react";
import type { Entity } from "../types";
import { contextLabel, dimensionLabels } from "../format";

export type IconName =
  | "grid"
  | "dashboard"
  | "building"
  | "book"
  | "compare"
  | "arrow"
  | "search"
  | "download"
  | "check"
  | "close"
  | "layers"
  | "file"
  | "clock"
  | "refresh"
  | "code"
  | "shield"
  | "copy"
  | "external"
  | "chevronLeft"
  | "chevronRight"
  | "spark"
  | "terminal";
const iconPaths: Record<IconName, ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.4" />
      <rect x="14" y="3" width="7" height="7" rx="1.4" />
      <rect x="3" y="14" width="7" height="7" rx="1.4" />
      <rect x="14" y="14" width="7" height="7" rx="1.4" />
    </>
  ),
  dashboard: (
    <>
      <rect x="3" y="3" width="8" height="10" rx="2" />
      <rect x="13" y="3" width="8" height="6" rx="2" />
      <rect x="13" y="11" width="8" height="10" rx="2" />
      <rect x="3" y="15" width="8" height="6" rx="2" />
    </>
  ),
  building: (
    <>
      <path d="M4 21V5l8-3 8 3v16M2 21h20M9 21v-5h6v5" />
      <path d="M8 7h1m6 0h1M8 11h1m6 0h1" />
    </>
  ),
  book: (
    <>
      <path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1v15" />
    </>
  ),
  compare: (
    <>
      <path d="M4 17V7m8 10V3m8 14v-6M2 21h20" />
      <path d="m2 8 2-2 2 2m4-4 2-2 2 2m4 8 2-2 2 2" />
    </>
  ),
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  layers: (
    <>
      <path d="m12 2 10 6-10 6L2 8l10-6Zm-9 11 9 5 9-5M3 18l9 5 9-5" />
    </>
  ),
  file: (
    <>
      <path d="M14 2H5v20h14V7l-5-5Zm0 0v5h5M8 12h8m-8 4h6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" />
    </>
  ),
  code: (
    <>
      <path d="m8 6-6 6 6 6m8-12 6 6-6 6" />
    </>
  ),
  shield: (
    <>
      <path d="M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5l-8-3Z" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="13" height="13" rx="2.5" />
      <path d="M16 8V5.5A2.5 2.5 0 0 0 13.5 3H5.5A2.5 2.5 0 0 0 3 5.5v8A2.5 2.5 0 0 0 5.5 16H8" />
    </>
  ),
  external: <path d="M7 17 17 7m0 0H8m9 0v9" />,
  chevronLeft: <path d="m14 6-6 6 6 6" />,
  chevronRight: <path d="m10 6 6 6-6 6" />,
  spark: (
    <>
      <path d="M12 3v4m0 10v4M3 12h4m10 0h4M5.6 5.6l2.8 2.8m7.2 7.2 2.8 2.8m0-12.8-2.8 2.8M8.4 15.6l-2.8 2.8" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <path d="m7 9 3 3-3 3m6 0h4" />
    </>
  ),
};
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {iconPaths[name]}
    </svg>
  );
}

export function Notice({
  title,
  children,
  icon = "file",
  action,
}: {
  title: string;
  children: ReactNode;
  icon?: IconName;
  action?: ReactNode;
}) {
  return (
    <div className="notice">
      <span className="notice-icon">
        <Icon name={icon} size={26} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: string;
  retry: () => void;
}) {
  return (
    <Notice
      title="暂时无法读取数据"
      icon="refresh"
      action={
        <button className="button secondary" onClick={retry}>
          重新读取
        </button>
      }
    >
      {error}
    </Notice>
  );
}
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skeleton-list" aria-label="正在读取数据" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton" key={index} />
      ))}
    </div>
  );
}
export function DemoBadge({ entity }: { entity: Entity }) {
  return entity.is_demo || /^demo[.:_-]/i.test(entity.entity_ref) ? (
    <span className="badge amber">演示数据</span>
  ) : null;
}
export function Kind({ value }: { value: string }) {
  return (
    <>
      {(
        {
          consolidated_group: "合并集团",
          group: "集团",
          listed_company: "上市公司",
          subsidiary: "子公司",
          legal_entity: "法人主体",
          company: "企业",
          demo: "演示主体",
        } as Record<string, string>
      )[value] || value}
    </>
  );
}
export function Eligibility({ value }: { value: string }) {
  const comparable = ["comparable", "eligible"].includes(value);
  return (
    <span
      className={`badge ${comparable ? "green" : value === "conditional" ? "amber" : "neutral"}`}
    >
      {comparable
        ? "口径符合"
        : value === "conditional"
          ? "有条件比较"
          : "展示数据"}
    </span>
  );
}
export function Dimensions({ values }: { values: Record<string, unknown> }) {
  return (
    <div className="dimension-tags">
      {Object.entries(values || {}).map(([key, value]) => {
        const label = Object.hasOwn(dimensionLabels, key)
          ? dimensionLabels[key]
          : /[\u3400-\u9fff]/.test(key)
            ? key
            : "补充口径";
        let text: string;
        if (value === null || value === undefined) text = "未说明";
        else if (typeof value === "object")
          text = "未结构化信息，请查看报告原文";
        else {
          const formatted = contextLabel(String(value));
          text = typeof formatted === "string" ? formatted : String(value);
        }
        return <span key={key}>{label}：{text}</span>;
      })}
    </div>
  );
}
