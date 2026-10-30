import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function PageHeader({
  title,
  back,
  subtitle,
  actions,
}: {
  title: string;
  back?: { href: string; label: string };
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {back && (
          <a className="back-link" href={back.href}>
            ← {back.label}
          </a>
        )}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
export function Notice({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "info" | "error" | "success";
}) {
  return (
    <div
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function Loading({ label = "正在读取…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}
export function ReadError({
  error,
  retry,
}: {
  error?: Error;
  retry?: () => void;
}) {
  return error ? (
    <Notice tone="error">
      {error.message}{" "}
      {retry && (
        <button className="link-button" onClick={retry}>
          重试
        </button>
      )}
    </Notice>
  ) : null;
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "error" | "active";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <nav className="tabs" aria-label="页面内容">
      {tabs.map((item) => (
        <button
          key={item.id}
          onClick={() => onChange(item.id)}
          className={value === item.id ? "selected" : ""}
          aria-pressed={value === item.id}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}

let locks = 0;
let originalOverflow = "";
const dialogs: HTMLDivElement[] = [];
export function Modal({
  title,
  children,
  onClose,
  footer,
  busy = false,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  busy?: boolean;
  wide?: boolean;
}) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : undefined;
    if (locks++ === 0) {
      originalOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      const root = document.getElementById("root");
      if (root) root.inert = true;
    }
    const element = panel.current;
    if (element) dialogs.push(element);
    panel.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (dialogs.at(-1) !== element) return;
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = [
        ...(panel.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]',
        ) || []),
      ].filter(
        (node) => !node.hidden && node.getAttribute("aria-hidden") !== "true",
      );
      const first = nodes[0],
        last = nodes.at(-1);
      if (!first) {
        event.preventDefault();
        panel.current?.focus();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === panel.current)
      ) {
        event.preventDefault();
        last?.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === panel.current)
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      if (element) {
        const index = dialogs.indexOf(element);
        if (index >= 0) dialogs.splice(index, 1);
      }
      if (--locks === 0) {
        document.body.style.overflow = originalOverflow;
        const root = document.getElementById("root");
        if (root) root.inert = false;
      }
      previous?.focus();
    };
  }, []);
  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={panel}
        className={"modal" + (wide ? " modal-wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        tabIndex={-1}
      >
        <header className="modal-header">
          <h2 id={id}>{title}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭窗口"
          >
            ×
          </button>
        </header>
        <div className="modal-body">
          <fieldset className="modal-fields" disabled={busy}>
            {children}
          </fieldset>
        </div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
