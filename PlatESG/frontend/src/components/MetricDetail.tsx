import { clauseReference, valueOriginLabel } from "../format";
import { RangeDisclosures } from "./RangeDisclosures";
import { apiUrl } from "../api";
import { buildTrendSeries } from "../chart-contract";
import {
  dimensionKeys,
  dimensionLabels,
  contextLabel,
  topicLabels,
  unitLabel,
  reasonLabel,
} from "../format";
import { useLocation } from "../navigation";
import { useObservations } from "../hooks/useObservations";
import type { Metric } from "../types";
import { DataChart } from "./DataChart";
import { DisplaySwitch, ObservationTable } from "./ObservationTable";
import { Dimensions, ErrorNotice, Icon, Loading, Notice } from "./Ui";
import { ProductLink } from "./ProductLink";
export default function MetricDetail({
  metric,
  dataset,
  onEvidence,
}: {
  metric: Metric;
  dataset: string;
  onEvidence: (id: string) => void;
}) {
  const [params, patch] = useLocation(),
    display = params.get("display") === "table" ? "table" : "chart",
    selectedEntity = params.get("trend_entity") || "";
  const data = useObservations(dataset, { metric_id: metric.metric_id });
  const groups = buildTrendSeries(
    data.rows.filter(
      (row) => !selectedEntity || row.entity_ref === selectedEntity,
    ),
  );
  return (
    <>
      <ProductLink view="metrics" className="back-button">
        ← 返回指标浏览
      </ProductLink>
      <div className="page-heading">
        <div className="eyebrow">
          {topicLabels[metric.topic] || metric.topic} ·{" "}
          <span className="mono">{clauseReference(metric)}</span>
        </div>
        <h1>{metric.label_zh}</h1>
        <p>{metric.description}</p>
      </div>
      <section className="definition-panel">
        <div>
          <span>标准单位</span>
          <strong>
            {metric.canonical_unit
              ? unitLabel(metric.canonical_unit)
              : "定性披露"}
          </strong>
        </div>
        <div>
          <span>{metric.clause_kind==='project_extension'?'关联 ESRS 原编号':'ESRS 原始编号'}</span>
          <strong className="mono">{metric.clause_kind==='project_extension'?metric.related_source_datapoint_ids?.join('、'):clauseReference(metric)}</strong>
        </div>
        <div>
          <span>指标定义版本</span>
          <strong>{metric.definition_version || "未提供"}</strong>
        </div>
        <div>
          <span>当前已读取数据</span>
          <strong>
            {data.rows.length}
            {data.cursor ? " +" : ""} 条
          </strong>
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>年度变化与已发布数据</h2>
            <p>不同企业、计量方法、边界和维度分别展示，缺失年份留空。</p>
          </div>
          <DisplaySwitch
            value={display}
            onChange={(value) => patch({ display: value })}
          />
        </div>
        <div className="toolbar compact">
          <select
            className="select"
            aria-label="选择趋势企业"
            value={selectedEntity}
            onChange={(event) => patch({ trend_entity: event.target.value })}
          >
            <option value="">全部企业</option>
            {[
              ...new Map(
                data.rows.map((row) => [row.entity_ref, row.entity_name]),
              ).entries(),
            ].map(([ref, name]) => (
              <option value={ref} key={ref}>
                {name}
              </option>
            ))}
          </select>
          <a
            className="text-button"
            href={
              dataset
                ? apiUrl("/v1/exports", {
                    format: "csv",
                    dataset_version: dataset,
                    metric_id: metric.metric_id,
                  })
                : undefined
            }
          >
            <Icon name="download" size={16} /> 下载该指标数据
          </a>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <ErrorNotice error={data.error} retry={data.reload} />
        ) : !data.rows.length ? (
          <Notice title="此版本暂无已发布数据">
            指标定义已列入目录。数据发布后，可在这里查看数值、趋势和来源。
          </Notice>
        ) : display === "table" ? (
          <ObservationTable
            rows={data.rows.filter(
              (row) => !selectedEntity || row.entity_ref === selectedEntity,
            )}
            onEvidence={onEvidence}
            showEntity
          />
        ) : groups.length ? (
          <div className="trend-grid">
            {groups.map((group) => (
              <article className="trend-card" key={group.identity}>
                <h3>{group.observation.entity_name}</h3>
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
            年度趋势需要至少两个同口径数值。区间、上下限和定性披露保留在数据表中。
          </Notice>
        )}
        {display === "chart" && !data.loading && !data.error && (
          <RangeDisclosures rows={data.rows} onEvidence={onEvidence} />
        )}
        {data.cursor && (
          <div className="load-more">
            <button
              className="button secondary"
              onClick={data.loadMore}
              disabled={data.loadingMore}
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
        <div className="chart-footer">
          <span>数据版本 {dataset || "尚无发布"}</span>
          <ProductLink view="compare" values={{ metric: metric.metric_id }}>
            在一致口径下比较 <Icon name="arrow" size={15} />
          </ProductLink>
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>理解这一指标</h2>
            <p>比较前应核对的口径条件。</p>
          </div>
        </div>
        <div className="method-content">
          <h3>必要维度</h3>
          <p>
            {dimensionKeys(metric)
              .map((key) => dimensionLabels[key] || key)
              .join("、") || "主体、期间、单位与披露边界"}
          </p>
          {metric.fixed_dimensions && (
            <Dimensions values={metric.fixed_dimensions} />
          )}
          <h3>比较条件</h3>
          <ul className="condition-list">
            {metric.comparison_conditions.map((condition) => (
              <li key={condition}>{reasonLabel(condition)}</li>
            ))}
          </ul>
          <ProductLink view="methods" className="text-button">
            数据与比较方法 <Icon name="arrow" size={15} />
          </ProductLink>
        </div>
      </section>
    </>
  );
}
