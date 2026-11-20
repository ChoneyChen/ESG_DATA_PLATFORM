import { useEffect, useRef, useState } from "react";
import type { MonitorSnapshot, MonitorSnapshot_MonitorRun } from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import { label, tone } from "../../shared/format";
import { href } from "../../app/routes";
import { Badge } from "../../shared/ui";

function MonitorRunRows({ runs }: { runs: MonitorSnapshot_MonitorRun[] }) {
  return <ol>
    {runs.map(run => (
      <li key={run.session_id}>
        <a href={href("extractions", run.session_id)} className="monitor-title">{run.name}</a>
        <div className="monitor-state">
          <Badge tone={tone(run.status)}>{label(run.status)}</Badge>
          {run.phase_label !== label(run.status) && <span>{run.phase_label}</span>}
          {run.status === "running" && run.clause_key && <small>{run.clause_key}</small>}
        </div>
        {run.products?.export_id && (
          <a className="monitor-products" href={href("extractions", run.session_id, "data")}>
            {run.products.stage_label} →
          </a>
        )}
        {run.observation === "unavailable" ? (
          <p className="monitor-warning">服务连接中断，保留最后状态</p>
        ) : (
          <div className="monitor-counts">
            {!!run.total && <span>{run.current ?? 0} / {run.total} 个目标</span>}
            <span>{run.facts ?? 0} 条原值</span>
            <span>{run.model_calls ?? 0} 次调用</span>
          </div>
        )}
        {run.status === "running" && ["preparing", "ocr", "ir", "quality"].includes(run.current_phase) &&
          run.preparation?.map(step => (
            <div className="monitor-counts" key={String(step.stage)}>
              <span>{step.label}</span>
              <span>
                {step.current != null && step.total
                  ? `${step.current} / ${step.total}${step.unit === "pages" ? " 页" : ""}`
                  : step.status === "partial" ? "已结束 · 证据受限" : label(String(step.status))}
              </span>
            </div>
          ))}
        {run.status === "running" && run.model_phase && (
          <small>
            模型{run.model_phase === "generation" ? "生成" : "读取"}
            {run.generated_tokens != null ? ` · ${run.generated_tokens} token` : ""}
            {run.elapsed_seconds != null ? ` · ${Math.round(run.elapsed_seconds)} 秒` : ""}
          </small>
        )}
      </li>
    ))}
  </ol>;
}

export function RunMonitor() {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const monitor = useResource<MonitorSnapshot>(
    "document",
    "/api/console/monitor",
    open ? 2000 : 6000,
  );
  function dismiss() {
    setOpen(false);
    trigger.current?.focus();
  }
  useEffect(() => {
    if (!open) return;
    close.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open]);
  const runs = monitor.data?.runs || [];
  const batchWaiting = monitor.data?.batch_waiting_count || 0;
  const active = runs.filter(run => ["queued", "running", "cancelling"].includes(run.status));
  const recent = runs.filter(run => !active.includes(run));
  return (
    <aside
      className="run-monitor"
      data-pinned={open && pinned}
      aria-label="全局运行监视"
    >
      {open && (
        <section
          className="run-monitor-panel"
          role="dialog"
          aria-modal="false"
          aria-labelledby="monitor-title"
        >
          <header>
            <div>
              <h2 id="monitor-title">运行监视</h2>
              {monitor.data && !monitor.error && (
                <small>{monitor.data.running_count ?? 0} 个执行 · {monitor.data.waiting_count ?? 0} 个等待准备 · {monitor.data.queued_count ?? 0} 个排队</small>
              )}
              {batchWaiting > 0 && !monitor.error && <small>
                <a href={href("extractions", "batches")}>{batchWaiting} 份报告等待进入批次窗口</a>
              </small>}
            </div>
            <div>
              <button
                className="text-button monitor-pin"
                aria-pressed={pinned}
                onClick={() => setPinned(!pinned)}
              >
                {pinned ? "取消固定" : "固定"}
              </button>
              <button
                ref={close}
                className="icon-button"
                aria-label="收起运行监视"
                onClick={dismiss}
              >
                ×
              </button>
            </div>
          </header>
          {monitor.error ? (
            <div className="monitor-empty">
              <p role="status">暂时无法读取运行状态</p>
              <button className="button-secondary" onClick={monitor.reload}>
                重试
              </button>
            </div>
          ) : !runs.length ? (
            <p className="monitor-empty">
              {monitor.loading ? "读取中…" : batchWaiting ? "当前还有报告候补" : "当前没有提取任务"}
            </p>
          ) : (
            <>
              {!!active.length && <MonitorRunRows runs={active}/>}
              {!!recent.length && (
                <details className="monitor-recent" open={!active.length}>
                  <summary>近期提取记录</summary>
                  <MonitorRunRows runs={recent}/>
                </details>
              )}
            </>
          )}
          <footer>
            <a href={href("runtime")}>查看全部运行 →</a>
          </footer>
        </section>
      )}
      <button
        ref={trigger}
        className="run-monitor-trigger"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span
          className={
            monitor.data?.active_count
              ? "monitor-light active"
              : "monitor-light"
          }
        />
        <span>运行监视</span>
        <strong>{monitor.data?.active_count || 0}</strong>
      </button>
    </aside>
  );
}
