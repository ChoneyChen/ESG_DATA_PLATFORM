import { useEffect, useState } from "react";
import type {
  DatasetPage,
  DatasetVersion,
  ObservationPage,
  Observation,
  ProductDefinition,
  ObservationSource,
} from "@esg/database-contracts";
import { apiUrl } from "../api";
import { useResource } from "../hooks/useResource";
import { ProductLink } from "./ProductLink";
import AppShell from "./AppShell";
import { ErrorNotice, Loading, Notice } from "./Ui";

const relationLabel = {
  match: "本目标",
  uncertain: "待确认",
  different: "其他目标",
};
const sourcePolicy: Record<string, string> = {
  all_sources: "保留全部来源",
  latest_report: "相同口径取最新披露",
  prefer_explicit_restated: "优先明确重述",
};
const fieldLabels: Record<string, string> = {
  reporting_entity: "实际统计主体",
  reporting_period: "测量期间",
  reporting_boundary: "实际范围",
  measurement_method: "方法",
  value_role: "值角色",
  aggregation_role: "汇总角色",
  entity_aggregation_role: "主体汇总",
  denominator: "分母",
};
const periodText = (period: Observation["period"]) =>
  period.kind === "annual" && period.year
    ? String(period.year)
    : period.kind === "quarter" && period.year
      ? `${period.year} Q${period.quarter}`
      : period.raw;

function CursorPages({
  total,
  returned,
  next,
  history,
  set,
}: {
  total: number;
  returned: number;
  next?: string | null;
  history: (string | null)[];
  set: (value: (string | null)[]) => void;
}) {
  return (
    <nav className="published-pagination" aria-label="分页">
      <span>
        共 {total} 条 · 本页 {returned} 条
      </span>
      <div>
        <button
          className="button secondary"
          disabled={history.length === 1}
          onClick={() => set(history.slice(0, -1))}
        >
          上一页
        </button>
        <span>第 {history.length} 页</span>
        <button
          className="button secondary"
          disabled={!next}
          onClick={() => next && set([...history, next])}
        >
          下一页
        </button>
      </div>
    </nav>
  );
}

export default function PublishedDataApp({
  params,
}: {
  params: URLSearchParams;
}) {
  const datasetId = params.get("dataset_id"),
    observationId = params.get("observation_id");
  const [catalogHistory, setCatalogHistory] = useState<(string | null)[]>([
    null,
  ]);
  const catalog = useResource<DatasetPage>(
    apiUrl("/v2/datasets", { limit: 50, cursor: catalogHistory.at(-1) }),
  );
  const dataset = useResource<DatasetVersion>(
    datasetId ? apiUrl("/v2/datasets/" + encodeURIComponent(datasetId)) : null,
  );
  useEffect(() => {
    document.title = [dataset.data?.title || "已发布数据", "PlatESG"].join(
      " · ",
    );
  }, [dataset.data]);
  const download = datasetId
    ? apiUrl("/v2/datasets/" + encodeURIComponent(datasetId) + "/export", {
        format: "csv",
      })
    : null;
  return (
    <AppShell
      datasetCount={catalog.data?.total || 0}
      observationCount={dataset.data?.observations ?? null}
      latestPublishedAt={dataset.data?.published_at || null}
      downloadUrl={download}
    >
      {!datasetId ? (
        <>
          <div className="page-heading">
            <h1>已发布数据</h1>
          </div>
          <ErrorNotice error={catalog.error} retry={catalog.reload} />
          {catalog.loading && !catalog.data ? (
            <Loading />
          ) : catalog.data?.items.length ? (
            <>
              <div className="published-datasets">
                {catalog.data.items.map((item) => (
                  <ProductLink
                    key={item.dataset_version_id}
                    view="datasets"
                    values={{ dataset_id: item.dataset_version_id }}
                    className="published-dataset-row"
                  >
                    <div>
                      <h2>{item.title}</h2>
                      <p>
                        {item.observations} 条观测 ·{" "}
                        {Object.keys(item.periods)
                          .map((key) => (key === "unknown" ? "期间未知" : key))
                          .join("、")}
                      </p>
                    </div>
                    <span>查看数据 →</span>
                  </ProductLink>
                ))}
              </div>
              <CursorPages
                total={catalog.data.total}
                returned={catalog.data.returned}
                next={catalog.data.next_cursor}
                history={catalogHistory}
                set={setCatalogHistory}
              />
            </>
          ) : !catalog.error ? (
            <Notice title="尚无已发布数据" />
          ) : null}
        </>
      ) : (
        <>
          <ProductLink view="datasets" className="published-back">
            ← 已发布数据
          </ProductLink>
          <ErrorNotice error={dataset.error} retry={dataset.reload} />
          {dataset.loading && !dataset.data ? (
            <Loading />
          ) : dataset.data ? (
            <DatasetContent
              key={datasetId}
              dataset={dataset.data}
              observationId={observationId}
            />
          ) : null}
        </>
      )}
    </AppShell>
  );
}

function DatasetContent({
  dataset,
  observationId,
}: {
  dataset: DatasetVersion;
  observationId: string | null;
}) {
  const base = "/v2/datasets/" + encodeURIComponent(dataset.dataset_version_id);
  const [tab, setTab] = useState("data"),
    [period, setPeriod] = useState(""),
    [clause, setClause] = useState(""),
    [history, setHistory] = useState<(string | null)[]>([null]);
  const definitions = useResource<ProductDefinition[]>(
    apiUrl(base + "/definitions"),
  );
  const rows = useResource<ObservationPage>(
    !observationId && tab === "data"
      ? apiUrl(base + "/observations", {
          limit: 100,
          period,
          clause_key: clause,
          cursor: history.at(-1),
        })
      : null,
  );
  const sources = useResource<ObservationSource[]>(
    tab === "sources" && !observationId ? apiUrl(base + "/sources") : null,
  );
  const observation = useResource<Observation>(
    observationId
      ? apiUrl(base + "/observations/" + encodeURIComponent(observationId))
      : null,
  );
  if (observationId)
    return (
      <>
        <ProductLink
          view="datasets"
          values={{ dataset_id: dataset.dataset_version_id }}
          className="published-back"
        >
          ← {dataset.title}
        </ProductLink>
        <ErrorNotice error={observation.error} retry={observation.reload} />
        {observation.loading ? (
          <Loading />
        ) : observation.data ? (
          <ObservationDetail row={observation.data} />
        ) : null}
      </>
    );
  return (
    <>
      <div className="page-heading">
        <h1>{dataset.title}</h1>
        <div className="published-summary">
          <span>{dataset.observations} 条观测</span>
          <span>{sourcePolicy[dataset.policy.mode || "all_sources"]}</span>
          <span>{dataset.published_at.slice(0, 10)} 发布</span>
        </div>
      </div>
      <div className="topic-tabs" role="group" aria-label="数据视图">
        {[
          ["data", "数据"],
          ["definitions", "条款定义"],
          ["sources", "来源"],
        ].map(([key, title]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {title}
          </button>
        ))}
      </div>
      {tab === "data" ? (
        <>
          <div className="toolbar published-toolbar">
            <label>
              期间
              <select
                value={period}
                onChange={(event) => {
                  setPeriod(event.target.value);
                  setHistory([null]);
                }}
              >
                <option value="">全部期间</option>
                {Object.keys(dataset.periods).map((key) => (
                  <option key={key} value={key}>
                    {key === "unknown" ? "期间未知" : key}
                  </option>
                ))}
              </select>
            </label>
            <label>
              条款
              <select
                value={clause}
                onChange={(event) => {
                  setClause(event.target.value);
                  setHistory([null]);
                }}
              >
                <option value="">全部条款</option>
                {definitions.data?.map((item) => (
                  <option key={item.clause_digest} value={item.clause_key}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
            <a
              className="button secondary"
              href={apiUrl(base + "/export", {
                format: "csv",
                period,
                clause_key: clause,
              })}
            >
              CSV 长表
            </a>
            <a
              className="button secondary"
              href={apiUrl(base + "/export", {
                format: "parquet",
                period,
                clause_key: clause,
              })}
            >
              Parquet
            </a>
          </div>
          <ErrorNotice error={rows.error} retry={rows.reload} />
          {rows.loading ? (
            <Loading />
          ) : rows.data ? (
            <>
              <div className="table-wrap">
                <table className="data-table published-observations">
                  <thead>
                    <tr>
                      <th>项目</th>
                      <th>原值 / 原文</th>
                      <th>单位</th>
                      <th>期间</th>
                      <th>实际主体</th>
                      <th>目标关系</th>
                      <th>比较条件</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.data.items.map((row) => (
                      <tr key={row.observation_id}>
                        <td>{row.source_row_label || row.title}</td>
                        <td
                          className={
                            row.value.kind === "quantitative"
                              ? "numeric"
                              : "published-statement"
                          }
                        >
                          {row.value.kind === "quantitative"
                            ? row.value.value_raw
                            : row.value.statement_raw}
                        </td>
                        <td>
                          {row.value.kind === "quantitative"
                            ? row.value.unit_raw || "未陈述"
                            : "—"}
                        </td>
                        <td>{periodText(row.period)}</td>
                        <td>
                          {row.identity.reporting_entity?.raw_text || "未陈述"}
                        </td>
                        <td>{relationLabel[row.relation]}</td>
                        <td>
                          {row.comparison.eligible
                            ? "满足声明条件"
                            : "口径未确认"}
                        </td>
                        <td>
                          <ProductLink
                            view="datasets"
                            values={{
                              dataset_id: dataset.dataset_version_id,
                              observation_id: row.observation_id,
                            }}
                          >
                            查看 →
                          </ProductLink>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <CursorPages
                total={rows.data.total}
                returned={rows.data.returned}
                next={rows.data.next_cursor}
                history={history}
                set={setHistory}
              />
            </>
          ) : null}
        </>
      ) : tab === "definitions" ? (
        <>
          <ErrorNotice error={definitions.error} retry={definitions.reload} />
          {definitions.loading ? (
            <Loading />
          ) : (
            <div className="published-definitions">
              {definitions.data?.map((item) => (
                <article key={item.clause_digest}>
                  <small>{item.clause_key}</small>
                  <h2>{item.title}</h2>
                  <p>
                    {typeof item.contract.definition === "string"
                      ? item.contract.definition
                      : ""}
                  </p>
                </article>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <ErrorNotice error={sources.error} retry={sources.reload} />
          {sources.loading ? (
            <Loading />
          ) : (
            <div className="published-definitions">
              {sources.data?.map((source, index) => (
                <article key={index}>
                  <h2>{source.title || source.report_company || "来源报告"}</h2>
                  <p>
                    报告企业：{source.report_company || "未陈述"} · 发表年：
                    {source.publication_year || "未陈述"}
                  </p>
                  {source.pdf_sha256 && (
                    <a
                      className="button secondary"
                      target="_blank"
                      rel="noreferrer"
                      href={apiUrl("/v2/sources/" + source.pdf_sha256 + "/pdf")}
                    >
                      查看原PDF →
                    </a>
                  )}
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function ObservationDetail({ row }: { row: Observation }) {
  return (
    <>
      <div className="page-heading">
        <h1>{row.source_row_label || row.title}</h1>
        <p className="published-raw">
          {row.value.kind === "quantitative"
            ? `${row.value.value_raw} ${row.value.unit_raw || ""}`
            : row.value.statement_raw}
        </p>
      </div>
      <section className="panel published-details">
        <h2>实际口径</h2>
        <dl>
          <dt>期间</dt>
          <dd>{periodText(row.period)}</dd>
          {Object.entries(row.identity)
            .filter(
              ([key, field]) =>
                key !== "dimensions" &&
                field &&
                typeof field === "object" &&
                "raw_text" in field &&
                field.raw_text,
            )
            .map(([key, field]) => (
              <div key={key}>
                <dt>{fieldLabels[key] || key}</dt>
                <dd>{"raw_text" in field ? String(field.raw_text) : ""}</dd>
              </div>
            ))}
          {Object.entries(row.identity.dimensions || {})
            .filter(([, field]) => field.raw_text)
            .map(([key, field]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{field.raw_text}</dd>
              </div>
            ))}
        </dl>
        <p>
          {relationLabel[row.relation]} · {row.mapping?.reason}
        </p>
        <p>
          {row.comparison.eligible
            ? "满足此条声明的比较条件"
            : "可浏览原值，比较口径尚未确认"}
        </p>
      </section>
      <section className="panel published-details">
        <h2>原文证据</h2>
        {row.evidence.map((item, index) => (
          <blockquote key={index}>
            <small>
              {item.page_index != null
                ? `PDF第${item.page_index + 1}页`
                : "来源位置"}
            </small>
            <p>{item.excerpt}</p>
          </blockquote>
        ))}
        {row.source.pdf_sha256 && (
          <a
            className="button secondary"
            href={
              apiUrl("/v2/sources/" + row.source.pdf_sha256 + "/pdf") +
              `#page=${(row.evidence[0]?.page_index ?? 0) + 1}`
            }
            target="_blank"
            rel="noreferrer"
          >
            查看原PDF →
          </a>
        )}
      </section>
    </>
  );
}
