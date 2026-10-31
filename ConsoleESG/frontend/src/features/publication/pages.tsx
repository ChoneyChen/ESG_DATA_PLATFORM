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
} from "../../shared/ui";
import { href } from "../../app/routes";
import { ObservationGrid, Pager } from "./observations";
import { date } from "../../shared/format";

export function PublicationPage() {
  const [tab, setTab] = useState("candidates"),
    [history, setHistory] = useState<(string | null)[]>([null]);
  const params = new URLSearchParams({ limit: "50" });
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
        }}
        tabs={[
          { id: "candidates", label: "已投影数据" },
          { id: "published", label: "发布记录" },
        ]}
      />
      <ReadError error={resource.error} retry={resource.reload} />
      {!resource.data && !resource.error ? (
        <Loading />
      ) : !resource.data?.items.length ? (
        <Empty
          title={tab === "candidates" ? "暂无已投影数据" : "暂无发布记录"}
        />
      ) : (
        <>
          <div className="object-list">
            {tab === "candidates"
              ? candidates.data?.items.map((item) => (
                  <a
                    className="object-row"
                    key={item.projection_id}
                    href={
                      href("publication", "new") +
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
                    <span>准备发布 →</span>
                  </a>
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
            onPage={setHistory}
          />
        </>
      )}
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
