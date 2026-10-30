import type {
  TaskObservation,
  ExtractionSession,
} from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import {
  PageHeader,
  ReadError,
  Loading,
  Badge,
  Empty,
  Notice,
} from "../../shared/ui";
import { label, tone, date } from "../../shared/format";
import { segment } from "../../shared/api";
import { href } from "../../app/routes";
type Compatibility = {
  restart_required: boolean;
  readiness?: { ready: boolean; blockers: string[] };
  service?: string;
};
export function RuntimePage({ taskId }: { taskId?: string }) {
  const doc = useResource<Compatibility>(
      "document",
      "/health/compatibility",
      8000,
    ),
    engine = useResource<{
      version: string;
      ready: boolean;
      fingerprint: string;
    }>("extraction", "/api/engine", 8000),
    queue = useResource<TaskObservation[]>(
      "document",
      "/api/operations/tasks",
      2500,
    ),
    task = useResource<TaskObservation>(
      "document",
      taskId ? "/api/operations/tasks/" + segment(taskId) : null,
      2500,
    ),
    runs = useResource<ExtractionSession[]>(
      "document",
      "/api/report-sessions",
      2500,
    );
  const parent = runs.data?.find((r) => r.queue_task_id === taskId),
    current = task.data;
  return (
    <>
      <PageHeader
        title={taskId ? current?.title || "执行任务" : "运行"}
        back={taskId ? { href: href("runtime"), label: "运行" } : undefined}
        subtitle={taskId ? taskId : "服务状态与实际执行队列"}
      />
      {taskId ? (
        <>
          <ReadError error={task.error} retry={task.reload} />
          {current ? (
            <section className="section">
              <div className="run-heading">
                <Badge tone={tone(current.status)}>
                  {label(current.status)}
                </Badge>
                <strong>{current.message}</strong>
              </div>
              {parent && (
                <a
                  className="button button-secondary"
                  href={href("extractions", parent.session_id)}
                >
                  返回所属提取 →
                </a>
              )}
              <dl className="detail-list">
                <dt>类型</dt>
                <dd>{label(current.task_type)}</dd>
                <dt>阶段</dt>
                <dd>{current.stage}</dd>
                <dt>实际计数</dt>
                <dd>
                  {current.progress_total
                    ? `${current.progress_current} / ${current.progress_total}`
                    : "未提供计数"}
                </dd>
                <dt>创建</dt>
                <dd>{date(current.created_at)}</dd>
                <dt>开始</dt>
                <dd>{date(current.started_at)}</dd>
                <dt>结束</dt>
                <dd>{date(current.finished_at)}</dd>
              </dl>
              {current.cancel_requested && (
                <Notice>已请求取消，等待执行进程确认。</Notice>
              )}
              {current.error && <Notice tone="error">{current.error}</Notice>}
            </section>
          ) : !task.error ? (
            <Loading />
          ) : null}
        </>
      ) : (
        <>
          <div className="service-grid">
            <section className="service-card">
              <div className="section-heading">
                <h2>文档服务</h2>
                <Badge
                  tone={
                    doc.error
                      ? "error"
                      : !doc.data
                        ? "neutral"
                        : doc.data?.readiness?.ready
                          ? "success"
                          : "warning"
                  }
                >
                  {doc.error
                    ? "未连接"
                    : !doc.data
                      ? "读取中"
                      : doc.data?.readiness?.ready
                        ? "就绪"
                        : "待就绪"}
                </Badge>
              </div>
              <ReadError error={doc.error} retry={doc.reload} />
              {doc.data?.readiness?.blockers.map((s) => (
                <p key={s}>{s}</p>
              ))}
            </section>
            <section className="service-card">
              <div className="section-heading">
                <h2>提取引擎</h2>
                <Badge
                  tone={
                    engine.error
                      ? "error"
                      : !engine.data
                        ? "neutral"
                        : engine.data?.ready
                          ? "success"
                          : "warning"
                  }
                >
                  {engine.error
                    ? "未连接"
                    : !engine.data
                      ? "读取中"
                      : engine.data?.ready
                        ? "就绪"
                        : "待重启"}
                </Badge>
              </div>
              <ReadError error={engine.error} retry={engine.reload} />
              <p>{engine.data?.version || "读取中"}</p>
            </section>
          </div>
          <section className="section">
            <div className="section-heading">
              <h2>执行队列</h2>
              <button className="text-button" onClick={queue.reload}>
                刷新
              </button>
            </div>
            <ReadError error={queue.error} retry={queue.reload} />
            {!queue.data && !queue.error ? (
              <Loading />
            ) : queue.data?.length ? (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>任务</th>
                      <th>状态</th>
                      <th>当前工作</th>
                      <th>计数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queue.data.map((t) => (
                      <tr key={t.task_id}>
                        <td>
                          <a
                            className="row-title"
                            href={href("runtime", t.task_id)}
                          >
                            {t.title}
                          </a>
                          <small>{label(t.task_type)}</small>
                        </td>
                        <td>
                          <Badge tone={tone(t.status)}>{label(t.status)}</Badge>
                        </td>
                        <td>{t.message}</td>
                        <td>
                          {t.progress_total
                            ? `${t.progress_current} / ${t.progress_total}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="队列为空" />
            )}
          </section>
          <p className="muted">
            页面按需刷新；计数来自执行服务。模型 token 不计入条款完成比例。
          </p>
        </>
      )}
    </>
  );
}
