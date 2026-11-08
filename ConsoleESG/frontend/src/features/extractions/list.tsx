import { useState } from "react";
import type { ExtractionSession, ReportList } from "../../shared/contracts";
import { useResource, invalidate } from "../../shared/resource";
import { write } from "../../shared/api";
import {
  PageHeader,
  Loading,
  ReadError,
  Empty,
  Badge,
  Field,
  SelectionCheckbox,
  Modal,
} from "../../shared/ui";
import { date, label, tone } from "../../shared/format";
import { href } from "../../app/routes";
const removable = (run: ExtractionSession) => ["completed","partial","failed","cancelled","interrupted"].includes(run.status);
export function SessionRows({ runs, selected, onSelect, disabled = false }: { runs: ExtractionSession[]; selected?: string[]; onSelect?: (ids: string[]) => void; disabled?: boolean }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {onSelect && <th className="selection-cell"><span className="sr-only">选择任务</span></th>}
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
              {onSelect && <td className="selection-cell"><input type="checkbox" aria-label={"选择任务 "+run.name}
                disabled={disabled || !removable(run)} checked={selected?.includes(run.session_id) || false}
                onChange={() => onSelect(selected?.includes(run.session_id) ? selected.filter(id => id !== run.session_id) : [...(selected || []),run.session_id])}/></td>}
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
  const [archived, setArchived] = useState(false),
    [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [purging, setPurging] = useState(false),
    [error, setError] = useState<Error>();
  const runs = useResource<ExtractionSession[]>(
      "document",
      "/api/report-sessions" + (archived ? "?archived=true" : ""),
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
  async function clearSelected(purge = false) {
    setBusy(true); setError(undefined);
    try {
      await write("document","/api/report-sessions/"+(purge ? "purge" : archived ? "restore" : "archive"),{session_ids:selected});
      setSelected([]); setPurging(false); invalidate("document","/api/report-sessions"); invalidate("document","/api/console/monitor");
    } catch (e) { setError(e as Error); }
    finally { setBusy(false); }
  }
  return (
    <>
      <PageHeader
        title="提取"
        actions={
          <a className="button" href={href("extractions", "new")}>
            创建提取
          </a>
        }
      />
      <div className="toolbar cleanup-toolbar">
        <SelectionCheckbox ids={rows.filter(removable).map(r => r.session_id)} selected={selected} onChange={setSelected} disabled={busy} label="全选当前列表"/>
        <span>已选 {selected.length} 项</span>
        <button className={archived ? "button-secondary" : "button-danger"} disabled={busy || !selected.length} onClick={() => clearSelected()}>
          {busy ? "处理中…" : archived ? "恢复所选" : "删除所选记录"}
        </button>
        {archived && <button className="button-danger" disabled={busy || !selected.length} onClick={() => setPurging(true)}>彻底删除所选</button>}
        <button className="text-button" disabled={busy} onClick={() => { setArchived(!archived); setSelected([]); setError(undefined); }}>{archived ? "返回任务记录" : "已删除"}</button>
      </div>
      {!!selected.length && !archived && <p className="field-help">删除后可在“已删除”中恢复，提取结果保留。执行中的任务不可选择。</p>}
      <ReadError error={error}/>
      <div className="filters">
        <Field label="搜索任务">
          <input
            type="search"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelected([]); }}
          />
        </Field>
        <Field label="状态">
          <select value={status} onChange={(e) => { setStatus(e.target.value); setSelected([]); }}>
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
        <SessionRows runs={rows} selected={selected} onSelect={setSelected} disabled={busy}/>
      ) : (
        <Empty
          title={archived ? "没有已删除记录" : "没有提取任务"}
          action={
            <a className="button" href={href("extractions", "new")}>
              创建提取
            </a>
          }
        />
      )}
      {purging && <Modal title="彻底删除提取记录" onClose={() => !busy && setPurging(false)} busy={busy} footer={<>
        <button className="button-secondary" disabled={busy} onClick={() => setPurging(false)}>取消</button>
        <button className="button-danger" disabled={busy} onClick={() => clearSelected(true)}>{busy ? "删除中…" : "确认彻底删除"}</button>
      </>}><p>永久删除选中的 {selected.length} 条任务记录及对应提取产物，无法恢复。原PDF和已入库数据保留。</p><ReadError error={error}/></Modal>}
    </>
  );
}
