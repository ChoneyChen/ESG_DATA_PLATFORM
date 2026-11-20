import { useState } from "react";
import type { ExtractionBatch, ExtractionBatchSummary } from "../../shared/contracts";
import { href } from "../../app/routes";
import { segment } from "../../shared/api";
import { date, label, providerInterruptionLabel, tone } from "../../shared/format";
import { useResource } from "../../shared/resource";
import { Badge, Empty, Field, Loading, PageHeader, ReadError } from "../../shared/ui";

export const extractionBatchesPath = "/api/report-extraction-batches";

export function ExtractionBatchesPage() {
  const batches = useResource<ExtractionBatchSummary[]>("document", extractionBatchesPath, 2500);
  return <>
    <PageHeader title="提取批次" back={{ href: href("extractions"), label: "提取" }}
      actions={<a className="button" href={href("extractions", "new")}>创建提取</a>}/>
    <ReadError error={batches.error} retry={batches.reload}/>
    {batches.data?.length ? (
      <div className="table-scroll">
        <table className="data-table">
          <thead><tr><th>提取批次</th><th>窗口内 / 容量</th><th>候补报告</th><th>已结束 / 总数</th><th>提交时间</th></tr></thead>
          <tbody>
            {batches.data.map(batch => (
              <tr key={batch.batch_id}>
                <td><a className="row-title" href={href("extractions", "batches", batch.batch_id)}>{batch.name}</a>
                  {batch.provider_pauses?.length ? <Badge tone="warning">等待模型提供者恢复</Badge> : null}
                </td>
                <td>{batch.active_count} / {batch.window_size}</td>
                <td>{batch.waiting_count}</td>
                <td>{batch.finished_count} / {batch.total_count}</td>
                <td>{date(batch.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : batches.data ? <Empty title="没有提取批次"/> : !batches.error && <Loading/>}
  </>;
}

type Membership = "waiting" | "active" | "finished";
const terminal = new Set(["completed", "partial", "cancelled", "failed", "interrupted"]);

function membership(member: ExtractionBatch["members"][number]): Membership {
  if (terminal.has(member.status)) return "finished";
  return member.status === "waiting_for_window" ? "waiting" : "active";
}

const membershipLabel: Record<Membership, string> = {
  waiting: "候补",
  active: "窗口内",
  finished: "已结束",
};

export function ExtractionBatchPage({ batchId }: { batchId: string }) {
  const result = useResource<ExtractionBatch>("document", `${extractionBatchesPath}/${segment(batchId)}`, 2500);
  const [filter, setFilter] = useState("");
  const batch = result.data;
  const members = batch?.members.filter(member => !filter || membership(member) === filter);
  return <>
    <PageHeader title={batch?.name || "提取批次"} back={{ href: href("extractions", "batches"), label: "提取批次" }}/>
    <ReadError error={result.error} retry={result.reload}/>
    {batch ? <>
      <section className="progress-panel" aria-label="批次进度">
        <div className="metric-strip">
          <div><strong>{batch.finished_count} / {batch.total_count}</strong><span>份报告已结束</span></div>
          <div><strong>{batch.active_count} / {batch.window_size}</strong><span>窗口内报告 / 容量</span></div>
          <div><strong>{batch.waiting_count}</strong><span>份报告候补</span></div>
          {batch.submitted_count != null && batch.submitted_count !== batch.total_count &&
            <div><strong>{batch.submitted_count}</strong><span>份原提交报告</span></div>}
        </div>
        <p className="field-help">窗口内的报告按执行队列轮转。每有一份报告结束，候补报告按批次顺序补入。</p>
        {batch.provider_pauses?.map(pause => (
          <p key={pause.provider} role="status">
            <Badge tone="warning">{providerInterruptionLabel(pause)}</Badge>{" "}
            <a href={href("extractions", pause.session_id)}>查看中断任务并继续</a>
          </p>
        ))}
      </section>
      <section className="section">
        <div className="section-heading">
          <h2>批次成员</h2>
          <Field label="显示报告">
            <select value={filter} onChange={event => setFilter(event.target.value)}>
              <option value="">全部报告</option>
              {Object.entries(membershipLabel).map(([value, title]) => <option key={value} value={value}>{title}</option>)}
            </select>
          </Field>
        </div>
        {members?.length ? <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>批次顺序</th><th>报告任务</th><th>窗口状态</th><th>任务状态</th><th>进入窗口</th></tr></thead>
            <tbody>
              {members.map(member => <tr key={member.session_id}>
                <td>{member.position}</td>
                <td>
                  <a className="row-title" href={href("extractions", member.session_id)}>{member.name}</a>
                  {(member.replaces_session_id || !!member.replacement_session_ids?.length) && <div className="toolbar">
                    {member.replaces_session_id && <a href={href("extractions", member.replaces_session_id)}>原任务</a>}
                    {member.replacement_session_ids?.map((attempt, index) => (
                      <a key={attempt} href={href("extractions", attempt)}>第 {index + 1} 次重提</a>
                    ))}
                  </div>}
                </td>
                <td>{membershipLabel[membership(member)]}</td>
                <td><Badge tone={tone(member.status)}>{label(member.status)}</Badge></td>
                <td>{date(member.admitted_at)}</td>
              </tr>)}
            </tbody>
          </table>
        </div> : <Empty title="暂无符合筛选的报告"/>}
      </section>
    </> : !result.error && <Loading/>}
  </>;
}
