import { useState } from "react";
import type { ObservationPage, CaptureFactPage } from "@esg/database-contracts";
import type { SessionProducts } from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import { segment, serviceUrl, write } from "../../shared/api";
import {
  PageHeader,
  ReadError,
  Loading,
  Badge,
  Empty,
  Field,
} from "../../shared/ui";
import { href } from "../../app/routes";
import { ObservationGrid, CapturedFactGrid, Pager } from "./observations";

export function ProductStrip({ sessionId }: { sessionId: string }) {
  const result = useResource<SessionProducts>(
    "document",
    "/api/report-sessions/" + segment(sessionId) + "/products",
    3500,
  );
  if (!result.data?.export_id) return null;
  return (
    <a className="workflow-strip" href={href("extractions", sessionId, "data")}>
      <strong>
        {result.error ? "数据状态待确认" : result.data.stage_label}
      </strong>
      <span>
        {result.data.state
          ? `${result.data.state.captured_facts} 条已接收原值`
          : "查看交付状态"}{" "}
        →
      </span>
    </a>
  );
}

export function ReceivedDataPage({ sessionId }: { sessionId: string }) {
  const status = useResource<SessionProducts>(
    "document",
    "/api/report-sessions/" + segment(sessionId) + "/products",
    3000,
  );
  const state = status.data?.state,
    projection = state?.projection.projection;
  const [period, setPeriod] = useState(""),
    [history, setHistory] = useState<(string | null)[]>([null]),
    [error, setError] = useState<Error>(),
    [retrying, setRetrying] = useState(false);
  const params = new URLSearchParams({ limit: "100" });
  if (period) params.set("period", period);
  if (history.at(-1)) params.set("cursor", history.at(-1)!);
  const base = projection
    ? "/v1/projections/" + segment(projection.projection_id)
    : null;
  const rows = useResource<ObservationPage>(
    "database",
    base ? base + "/observations?" + params : null,
  );
  const raw = useResource<CaptureFactPage>(
    "database",
    state && !projection
      ? "/v1/ingestion/exports/" + segment(state.export_id) + "/facts?" + params
      : null,
  );
  async function retry() {
    if (!state) return;
    setRetrying(true);
    setError(undefined);
    try {
      await write(
        "database",
        "/v1/ingestion/exports/" + segment(state.export_id) + "/projection",
        {},
      );
      status.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setRetrying(false);
    }
  }
  return (
    <>
      <PageHeader
        title="已接收数据"
        back={{ href: href("extractions", sessionId), label: "本次提取" }}
        subtitle={status.data?.stage_label}
        actions={
          projection ? (
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
              <a
                className="button"
                href={
                  href("publication", "new") +
                  "?projection=" +
                  segment(projection.projection_id)
                }
              >
                准备发布
              </a>
            </>
          ) : undefined
        }
      />
      <ReadError error={status.error || error} retry={status.reload} />
      {!status.data && !status.error ? (
        <Loading />
      ) : !state ? (
        <Empty title={status.data?.stage_label || "等待接收"} />
      ) : (
        <>
          <div className="run-heading">
            <Badge>{state.captured_facts} 条原值</Badge>
            {state.capture_periods.map((item) => (
              <Badge key={item.period}>
                {item.period === "unknown" ? "期间未知" : item.period} ·{" "}
                {item.facts}
              </Badge>
            ))}
          </div>
          {projection ? (
            <>
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
                    {Object.keys(projection.periods).map((key) => (
                      <option key={key} value={key}>
                        {key === "unknown" ? "期间未知" : key}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <ReadError error={rows.error} retry={rows.reload} />
              {rows.loading && !rows.data ? (
                <Loading />
              ) : rows.data ? (
                <>
                  <ObservationGrid rows={rows.data.items} />
                  <Pager
                    total={rows.data.total}
                    returned={rows.data.returned}
                    next={rows.data.next_cursor}
                    history={history}
                    onPage={setHistory}
                  />
                </>
              ) : null}
            </>
          ) : (
            <>
              {state.projection.status === "failed" && (
                <div className="form-actions">
                  <button disabled={retrying} onClick={retry}>
                    {retrying ? "重试中…" : "重试投影"}
                  </button>
                </div>
              )}
              <ReadError error={raw.error} retry={raw.reload} />
              {raw.loading && !raw.data ? (
                <Loading />
              ) : raw.data ? (
                <>
                  <CapturedFactGrid rows={raw.data.items} />
                  <Pager
                    total={raw.data.total}
                    returned={raw.data.returned}
                    next={raw.data.next_cursor}
                    history={history}
                    onPage={setHistory}
                  />
                </>
              ) : null}
            </>
          )}
          {!!state.datasets?.total && (
            <section className="section">
              <h2>发布记录</h2>
              <div className="object-list">
                {state.datasets.items.map((item) => (
                  <a
                    className="object-row"
                    key={item.dataset_version_id}
                    href={href("publication", item.dataset_version_id)}
                  >
                    <strong>{item.title}</strong>
                    <span>
                      {item.state === "published" ? "已发布" : "已撤下"} →
                    </span>
                  </a>
                ))}
              </div>
              {state.datasets.next_cursor && (
                <a href={href("publication")}>查看全部发布记录 →</a>
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}
