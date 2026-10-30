import { useState } from "react";
import type { ExtractionSession, ReportList } from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import {
  PageHeader,
  Loading,
  ReadError,
  Empty,
  Badge,
  Field,
} from "../../shared/ui";
import { date, label, tone } from "../../shared/format";
import { href } from "../../app/routes";
export function SessionRows({ runs }: { runs: ExtractionSession[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>提取任务</th>
            <th>状态</th>
            <th>当前阶段</th>
            <th>条款</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.session_id}>
              <td>
                <a
                  className="row-title"
                  href={href("extractions", run.session_id)}
                >
                  {run.name}
                </a>
                <small>
                  {run.run?.document?.report_resource?.title ||
                    run.run?.document?.report_resource?.original_filename ||
                    run.session_id}
                </small>
              </td>
              <td>
                <Badge tone={tone(run.status)}>{label(run.status)}</Badge>
                {run.observation === "unavailable" && (
                  <small>状态连接暂不可用</small>
                )}
              </td>
              <td>{run.phase_label}</td>
              <td>{run.clause_keys?.length || 0}</td>
              <td>{date(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function ExtractionsPage({ reportId }: { reportId?: string }) {
  const runs = useResource<ExtractionSession[]>(
      "document",
      "/api/report-sessions",
      2500,
    ),
    reports = useResource<ReportList>("document", "/api/report-resources");
  const [status, setStatus] = useState(""),
    [query, setQuery] = useState("");
  const asset = reports.data?.reports.find(
    (r) => r.pdf_id === reportId,
  )?.asset_id;
  const rows = (runs.data || []).filter(
    (r) =>
      (!reportId ||
        r.request.input.asset_id === asset ||
        r.run?.document?.report_resource?.pdf_id === reportId) &&
      (!status || r.status === status) &&
      (!query ||
        [r.name, r.session_id, r.run?.document?.report_resource?.title].some(
          (s) => s?.toLowerCase().includes(query.toLowerCase()),
        )),
  );
  return (
    <>
      <PageHeader
        title="提取"
        subtitle="每份报告、每次选择独立保存"
        actions={
          <a className="button" href={href("extractions", "new")}>
            创建提取
          </a>
        }
      />
      <div className="filters">
        <Field label="搜索任务">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
        <Field label="状态">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">全部状态</option>
            {[
              "queued",
              "running",
              "cancelling",
              "completed",
              "partial",
              "failed",
              "cancelled",
              "interrupted",
            ].map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
        </Field>
        {reportId && <a href={href("extractions")}>清除报告筛选</a>}
      </div>
      <ReadError error={runs.error} retry={runs.reload} />
      {!runs.data && !runs.error ? (
        <Loading />
      ) : rows.length ? (
        <SessionRows runs={rows} />
      ) : (
        <Empty
          title="没有提取任务"
          action={
            <a className="button" href={href("extractions", "new")}>
              创建提取
            </a>
          }
        />
      )}
    </>
  );
}
