import { useState } from "react";
import type {
  ProjectionPage,
  DatasetPage,
  DatasetVersion,
  ObservationPage,
} from "@esg/database-contracts";
import { useResource, invalidate } from "../../shared/resource";
import { segment, serviceUrl, write } from "../../shared/api";
import {
  PageHeader,
  ReadError,
  Loading,
  Empty,
  Badge,
  Tabs,
  Field,
  Modal,
  SelectionCheckbox,
} from "../../shared/ui";
import { href } from "../../app/routes";
import { ObservationGrid, Pager } from "./observations";
import { date } from "../../shared/format";

export function PublicationPage() {
  const [tab, setTab] = useState("candidates"),
    [history, setHistory] = useState<(string | null)[]>([null]),
    [archived, setArchived] = useState(false),
    [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>(),
    [purging, setPurging] = useState(false);
  const params = new URLSearchParams({ limit: "50" });
  if (tab === "candidates" && archived) params.set("archived","true");
  if (history.at(-1)) params.set("cursor", history.at(-1)!);
  const candidates = useResource<ProjectionPage>(
    "database",
    tab === "candidates" ? "/v1/projections?" + params : null,
    5000,
  );
  const published = useResource<DatasetPage>(
    "database",
    tab === "published" ? "/v1/datasets?" + params : null,
    5000,
  );
  const resource = tab === "candidates" ? candidates : published;
  async function changeSelected(action: "archive" | "restore" | "purge") {
    setBusy(true); setError(undefined);
    try {
      await write("database","/v1/projections/"+action,{projection_ids:selected});
      setSelected([]); setHistory([null]); setPurging(false); invalidate("database","/v1/projections");
      invalidate("database","/v1/ingestion/exports/");
      invalidate("document","/api/report-sessions");
    } catch (e) { setError(e as Error); }
    finally { setBusy(false); }
  }
  return (
    <>
      <PageHeader
        title="发布"
        actions={
          <a className="button" href={href("publication", "new")}>
            准备发布
          </a>
        }
      />
      <Tabs
        value={tab}
        onChange={(value) => {
          setTab(value);
          setHistory([null]);
          setSelected([]); setError(undefined);
        }}
        tabs={[
          { id: "candidates", label: "已投影数据" },
          { id: "published", label: "发布记录" },
        ]}
      />
      {tab === "candidates" && <>
        <div className="toolbar cleanup-toolbar">
          <SelectionCheckbox ids={candidates.data?.items.map(p => p.projection_id) || []} selected={selected} onChange={setSelected} disabled={busy} label="全选本页"/>
          <span>已选 {selected.length} 项</span>
          <button className={archived ? "button-secondary" : "button-danger"} disabled={busy || !selected.length} onClick={() => changeSelected(archived ? "restore" : "archive")}>
            {busy ? "处理中…" : archived ? "恢复所选" : "删除所选"}
          </button>
          {archived && <button className="button-danger" disabled={busy || !selected.length} onClick={() => setPurging(true)}>彻底删除所选</button>}
          <button className="text-button" disabled={busy} onClick={() => { setArchived(!archived); setHistory([null]); setSelected([]); setError(undefined); }}>{archived ? "返回已投影数据" : "已删除"}</button>
        </div>
      </>}
      <ReadError error={error}/>
      <ReadError error={resource.error} retry={resource.reload} />
      {!resource.data && !resource.error ? (
        <Loading />
      ) : !resource.data?.items.length ? (
        <Empty
          title={tab === "candidates" ? archived ? "没有已删除投影" : "暂无已投影数据" : "暂无发布记录"}
        />
      ) : (
        <>
          <div className="object-list">
            {tab === "candidates"
              ? candidates.data?.items.map((item) => (
                  <div className="projection-choice" key={item.projection_id}>
                  <input type="checkbox" aria-label={"选择投影 "+(item.source.title || item.projection_id)} disabled={busy}
                    checked={selected.includes(item.projection_id)} onChange={() => setSelected(selected.includes(item.projection_id) ? selected.filter(id => id !== item.projection_id) : [...selected,item.projection_id])}/>
                  <a className="object-row"
                    href={
                      archived ? undefined : href("publication", "new") +
                      "?projection=" +
                      segment(item.projection_id)
                    }
                  >
                    <div>
                      <strong>
                        {item.source.title ||
                          item.source.report_company ||
                          "报告提取数据"}
                      </strong>
                      <small>
                        {item.observations} 条观测 ·{" "}
                        {Object.keys(item.periods)
                          .map((key) => (key === "unknown" ? "期间未知" : key))
                          .join("、")}
                      </small>
                    </div>
                    {!archived && <span>准备发布 →</span>}
                  </a>
                  </div>
                ))
              : published.data?.items.map((item) => (
                  <a
                    className="object-row"
                    key={item.dataset_version_id}
                    href={href("publication", item.dataset_version_id)}
                  >
                    <div>
                      <strong>{item.title}</strong>
                      <small>
                        {item.observations} 条观测 · {date(item.published_at)}
                      </small>
                    </div>
                    <Badge>
                      {item.state === "published" ? "已发布" : "已撤下"}
                    </Badge>
                    <span>→</span>
                  </a>
                ))}
          </div>
          <Pager
            total={resource.data.total}
            returned={resource.data.returned}
            next={resource.data.next_cursor}
            history={history}
            onPage={(value) => { setHistory(value); setSelected([]); }}
          />
        </>
      )}
      {purging && <Modal title="彻底删除投影" onClose={() => !busy && setPurging(false)} busy={busy} footer={<>
        <button className="button-secondary" disabled={busy} onClick={() => setPurging(false)}>取消</button>
        <button className="button-danger" disabled={busy} onClick={() => changeSelected("purge")}>{busy ? "删除中…" : "确认彻底删除"}</button>
      </>}><p>永久删除选中的 {selected.length} 份未发布投影及其观测数据，无法恢复。原PDF和已接收原值保留。</p><ReadError error={error}/></Modal>}
    </>
  );
}

function platformHref(id: string) {
  try {
    const url = new URL(
      import.meta.env.VITE_PLAT_URL || "http://127.0.0.1:8765/",
    );
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      return undefined;
    url.searchParams.set("view", "datasets");
    url.searchParams.set("dataset_id", id);
    return url.href;
  } catch {
    return undefined;
  }
}

export function DatasetPageView({ id }: { id: string }) {
  const base = "/v1/datasets/" + segment(id),
    dataset = useResource<DatasetVersion>("database", base);
  const [period, setPeriod] = useState(""),
    [history, setHistory] = useState<(string | null)[]>([null]),
    [withdrawing, setWithdrawing] = useState(false),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>();
  const params = new URLSearchParams({ limit: "100" });
  if (period) params.set("period", period);
  if (history.at(-1)) params.set("cursor", history.at(-1)!);
  const observations = useResource<ObservationPage>(
    "database",
    dataset.data ? base + "/observations?" + params : null,
  );
  async function withdraw() {
    if (!dataset.data) return;
    setBusy(true);
    setError(undefined);
    try {
      await write("database", base + "/withdraw", {
        expected_revision: dataset.data.revision,
        reason,
      });
      invalidate("database", "/v1/datasets");
      setWithdrawing(false);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        title={dataset.data?.title || "发布数据集"}
        back={{ href: href("publication"), label: "发布" }}
        actions={
          dataset.data ? (
            <>
              <a
                className="button button-secondary"
                href={serviceUrl("database", base + "/export?format=csv")}
              >
                CSV 长表
              </a>
              <a
                className="button button-secondary"
                href={serviceUrl("database", base + "/export?format=parquet")}
              >
                Parquet
              </a>
              {dataset.data.state === "published" && (
                <>
                  <a
                    className="button"
                    href={platformHref(id)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    在平台查看
                  </a>
                  <button
                    className="button-secondary"
                    onClick={() => setWithdrawing(true)}
                  >
                    撤下
                  </button>
                </>
              )}
            </>
          ) : undefined
        }
      />
      <ReadError error={dataset.error} retry={dataset.reload} />
      {!dataset.data && !dataset.error ? (
        <Loading />
      ) : (
        dataset.data && (
          <>
            <div className="run-heading">
              <Badge>
                {dataset.data.state === "published" ? "已发布" : "已撤下"}
              </Badge>
              <span>{dataset.data.observations} 条观测</span>
              {Object.entries(dataset.data.periods).map(([key, count]) => (
                <Badge key={key}>
                  {key === "unknown" ? "期间未知" : key} · {count}
                </Badge>
              ))}
            </div>
            <div className="filters">
              <Field label="期间">
                <select
                  value={period}
                  onChange={(e) => {
                    setPeriod(e.target.value);
                    setHistory([null]);
                  }}
                >
                  <option value="">全部期间</option>
                  {Object.keys(dataset.data.periods).map((key) => (
                    <option key={key} value={key}>
                      {key === "unknown" ? "期间未知" : key}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <ReadError error={observations.error} retry={observations.reload} />
            {observations.loading && !observations.data ? (
              <Loading />
            ) : observations.data ? (
              <>
                <ObservationGrid rows={observations.data.items} />
                <Pager
                  total={observations.data.total}
                  returned={observations.data.returned}
                  next={observations.data.next_cursor}
                  history={history}
                  onPage={setHistory}
                />
              </>
            ) : null}
          </>
        )
      )}
      {withdrawing && (
        <Modal
          title="撤下数据集"
          onClose={() => !busy && setWithdrawing(false)}
          busy={busy}
          footer={
            <>
              <button
                className="button-secondary"
                disabled={busy}
                onClick={() => setWithdrawing(false)}
              >
                取消
              </button>
              <button disabled={busy || !reason.trim()} onClick={withdraw}>
                {busy ? "提交中…" : "确认撤下"}
              </button>
            </>
          }
        >
          <p>公开入口将隐藏此数据集，原值和发布成员保留。</p>
          <ReadError error={error} />
          <Field label="撤下理由">
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={1000}
            />
          </Field>
        </Modal>
      )}
    </>
  );
}
