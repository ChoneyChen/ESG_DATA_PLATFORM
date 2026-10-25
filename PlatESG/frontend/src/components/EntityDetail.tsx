import { clauseReference, valueOriginLabel } from "../format";
import { publicCoverageLabel } from "../public-contract";
import { RangeDisclosures } from "./RangeDisclosures";
import { apiUrl } from "../api";
import { buildTrendSeries } from "../chart-contract";
import { contextLabel, periodLabel, reasonLabel, unitLabel } from "../format";
import { useLocation } from "../navigation";
import { useResource } from "../hooks/useResource";
import { useObservations } from "../hooks/useObservations";
import type { CoverageResponse, Entity, Metric } from "../types";
import { DataChart } from "./DataChart";
import { DisplaySwitch, ObservationTable } from "./ObservationTable";
import { ProductLink } from "./ProductLink";
import {
  DemoBadge,
  Dimensions,
  ErrorNotice,
  Icon,
  Kind,
  Loading,
  Notice,
} from "./Ui";
export default function EntityDetail({
  entity,
  metrics,
  dataset,
  onEvidence,
}: {
  entity: Entity;
  metrics: Metric[];
  dataset: string;
  onEvidence: (id: string) => void;
}) {
  const [params, patch] = useLocation(),
    metric = params.get("entity_metric") || "",
    display = params.get("display") === "table" ? "table" : "chart";
  const data = useObservations(dataset, {
    entity_ref: entity.entity_ref,
    metric_id: metric,
  });
  const coverage = useResource<CoverageResponse>(
    apiUrl("/v1/coverage", {
      entity_ref: entity.entity_ref,
      ...(dataset ? { dataset_version: dataset } : {}),
    }),
  );
  const groups = buildTrendSeries(data.rows);
  return (
    <>
      <ProductLink view="entities" className="back-button">
        ← 返回企业目录
      </ProductLink>
      <div className="entity-heading">
        <span className="entity-avatar large">
          {entity.name_zh.slice(0, 1)}
        </span>
        <div>
          <div className="eyebrow">
            企业档案 <DemoBadge entity={entity} />
          </div>
          <h1>{entity.name_zh}</h1>
          <div className="entity-metadata">
            <Kind value={entity.entity_kind} />
            <span>{entity.industry_code || "行业信息未提供"}</span>
            <span>{entity.country_code || "地区信息未提供"}</span>
          </div>
        </div>
      </div>
      <div className="compact-stats">
        <div>
          <span>已发布指标</span>
          <strong>
            {entity.coverage_count}
            <small> / {metrics.length}</small>
          </strong>
        </div>
        <div>
          <span>已读取数据</span>
          <strong>
            {data.rows.length}
            {data.cursor && <small> +</small>}
          </strong>
        </div>
        <div>
          <span>发布范围</span>
          <strong className="version-value">{dataset ? "历史快照" : "当前已发布报告"}</strong>
        </div>
      </div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>年度趋势</h2>
            <p>同一指标、单位、边界、方法及维度独立展示。</p>
          </div>
          <DisplaySwitch
            value={display}
            onChange={(value) => patch({ display: value })}
          />
        </div>
        <div className="toolbar compact">
          <select
            className="select"
            aria-label="筛选企业指标"
            value={metric}
            onChange={(event) => patch({ entity_metric: event.target.value })}
          >
            <option value="">全部指标</option>
            {metrics.map((item) => (
              <option value={item.metric_id} key={item.metric_id}>
                {clauseReference(item)} · {item.label_zh}
              </option>
            ))}
          </select>
          <a
            href={apiUrl("/v1/exports", {
              format: "csv",
              ...(dataset ? { dataset_version: dataset } : {}),
              entity_ref: entity.entity_ref,
              metric_id: metric,
            })}
            className="text-button"
          >
            <Icon name="download" size={16} /> 下载企业数据
          </a>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <ErrorNotice error={data.error} retry={data.reload} />
        ) : display === "table" ? (
          <ObservationTable rows={data.rows} onEvidence={onEvidence} />
        ) : groups.length ? (
          <div className="trend-grid">
            {groups.map((group) => (
              <article className="trend-card" key={group.identity}>
                <span className="eyebrow mono">
                  {clauseReference(group.observation)}
                </span>
                <h3>{group.observation.label_zh}</h3>
                <p className="trend-context">
                  {contextLabel(group.observation.boundary_code)} ·{" "}
                  {contextLabel(group.observation.measurement_method)} ·{" "}
                  {unitLabel(group.observation.canonical_unit)} · {valueOriginLabel(group.observation.value_origin)}
                </p>
                <Dimensions values={group.observation.dimensions} />
                <DataChart
                  rows={group.rows.filter((row) => row !== null)}
                  onEvidence={onEvidence}
                />
              </article>
            ))}
          </div>
        ) : (
          <Notice title="暂无可连接的年度趋势">
            至少需要两个相同口径的年度数值。单年数据、区间和定性信息可以在数据表查看。
          </Notice>
        )}
        {display === "chart" && !data.loading && !data.error && (
          <RangeDisclosures rows={data.rows} onEvidence={onEvidence} />
        )}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>已发布数据</h2>
            <p>保留披露数值、期间、计量口径与报告原文。</p>
          </div>
          <ProductLink
            view="sources"
            values={{ source_entity: entity.entity_ref }}
            className="text-button"
          >
            来源报告 <Icon name="arrow" size={16} />
          </ProductLink>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <ErrorNotice error={data.error} retry={data.reload} />
        ) : data.rows.length ? (
          <ObservationTable rows={data.rows} onEvidence={onEvidence} />
        ) : (
          <Notice title="此版本暂无已发布观测">
            当前发布中没有该企业的已发布数据。
          </Notice>
        )}
        {data.cursor && (
          <div className="load-more">
            <button
              className="button secondary"
              disabled={data.loadingMore}
              onClick={data.loadMore}
            >
              {data.loadingMore ? "正在读取…" : "加载更多数据"}
            </button>
          </div>
        )}
        {data.moreError && (
          <p className="inline-error" role="alert">
            {data.moreError}
          </p>
        )}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>指标与期间覆盖</h2>
            <p>只显示此发布版本的覆盖信息。暂无数据与明确未披露分别记录。</p>
          </div>
        </div>
        {coverage.loading ? (
          <Loading rows={2} />
        ) : coverage.error ? (
          <ErrorNotice error={coverage.error} retry={coverage.reload} />
        ) : (
          <div className="coverage-grid">
            {(coverage.data?.items || []).map((item) => (
              <div className="coverage-item" key={item.metric_id}>
                <span className="mono">{clauseReference(item)}</span>
                <strong>
                  {metrics.find((metric) => metric.metric_id === item.metric_id)
                    ?.label_zh || item.source_datapoint_id}
                </strong>
                <span
                  className={
                    "coverage-status " +
                    (item.status === "published" ? "published" : "")
                  }
                >
                  {item.status === "published" ? (
                    <>
                      <Icon name="check" size={14} />
                      已发布 · {item.published_count} 条
                    </>
                  ) : (
                    publicCoverageLabel(item.status)
                  )}
                </span>
                {item.reason &&
                  [
                    "not_disclosed_in_reviewed_scope",
                    "not_applicable",
                    "source_unreadable",
                    "not_selected",
                  ].includes(item.status) && (
                    <span className="coverage-reason">
                      {reasonLabel(item.reason)}
                    </span>
                  )}
                {!!item.periods?.length && (
                  <details className="coverage-periods">
                    <summary>{item.periods.length} 个已记录期间</summary>
                    {item.periods.map((period, index) => (
                      <div key={index}>
                        <span>{periodLabel(period)}</span>
                        <span>
                          {period.status === "published"
                            ? "已发布 · " + period.published_count + " 条"
                            : publicCoverageLabel(period.status)}
                        </span>
                        {period.reason &&
                          [
                            "not_disclosed_in_reviewed_scope",
                            "not_applicable",
                            "source_unreadable",
                            "not_selected",
                          ].includes(period.status) && (
                            <small>{reasonLabel(period.reason)}</small>
                          )}
                      </div>
                    ))}
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
