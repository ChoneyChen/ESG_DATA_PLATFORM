import type { ReactNode } from "react";

export function Notice({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="notice">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error?: string;
  retry: () => void;
}) {
  if (!error) return null;
  return (
    <Notice
      title="暂时无法读取数据"
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
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton-row" key={i} />
      ))}
    </div>
  );
}
