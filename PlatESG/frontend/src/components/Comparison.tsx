import { clauseReference } from "../format";
import { useEffect, useRef, useState } from "react";
import { apiUrl, request } from "../api";

const CAVEAT_TEXT: Record<string, string> = {
  measurement_method_differs_across_entities: "参与比较的企业使用了不同的测量方法，请结合方法说明阅读",
  boundary_code_differs_across_entities: "参与比较的企业报告边界不同，不能直接视为同一口径",
  value_role_differs_across_entities: "参与比较的数值包含实际值与重述值，请核对值角色",
};
import { observationGroup, pointValue } from "../chart-contract";
import {
  contextLabel,
  editableDimensionKeys,
  dimensionLabels,
  numericMetric,
  periodLabel,
  reasonLabel,
  unitLabel,
} from "../format";
import { useLocation } from "../navigation";
import { readDimensions, readStringArray } from "../public-contract";
import type { ComparisonResponse, Entity, Metric } from "../types";
import { DataChart } from "./DataChart";
import { DisplaySwitch, ObservationTable } from "./ObservationTable";
import { DemoBadge, Dimensions, Icon, Kind, Notice } from "./Ui";
export default function Comparison({
  entities,
  metrics,
  dataset,
  onEvidence,
}: {
  entities: Entity[];
  metrics: Metric[];
  dataset: string;
  onEvidence: (id: string) => void;
}) {
  const [params, patch] = useLocation();
  const metricId =
    params.get("metric") || metrics.find(numericMetric)?.metric_id || "";
  const selected = readStringArray(params.get("companies")),
    metric = metrics.find((item) => item.metric_id === metricId);
  const periodStart = params.get("period_start") || "",
    periodEnd = params.get("period_end") || "",
    boundary = params.get("boundary") || "",
    method = params.get("method") || "",
    dimensions = Object.fromEntries(
      Object.entries(readDimensions(params.get("dimensions"))).filter(
        ([, value]) => value.trim() !== "",
      ),
    );
  const [entityQuery, setEntityQuery] = useState("");
  const [result, setResult] = useState<ComparisonResponse>(),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [attempt, setAttempt] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const display =
    params.get("comparison_display") === "table" ? "table" : "chart";
  const requestBody = {
    metric_id: metricId,
    entity_refs: selected,
    ...(dataset ? { dataset_version: dataset } : {}),
    ...(periodStart ? { period_start: periodStart } : {}),
    ...(periodEnd ? { period_end: periodEnd } : {}),
    ...(boundary ? { boundary_code: boundary } : {}),
    ...(method ? { measurement_method: method } : {}),
    dimensions,
  };
  const requestKey = JSON.stringify(requestBody),
    shouldCompare = params.get("compared") === "1";
  useEffect(() => {
    controller.current?.abort();
    setResult(undefined);
    setError("");
    setLoading(false);
    if (
      !shouldCompare ||
      !metric ||
      !numericMetric(metric) ||
      !selected.length
    )
      return;
    const pending = new AbortController();
    controller.current = pending;
    setLoading(true);
    request<ComparisonResponse>(apiUrl("/v1/comparisons"), {
      method: "POST",
      signal: pending.signal,
      body: requestKey,
    })
      .then((response) => {
        if (!pending.signal.aborted) setResult(response);
      })
      .catch((failure) => {
        if (!pending.signal.aborted) setError((failure as Error).message);
      })
      .finally(() => {
        if (!pending.signal.aborted) setLoading(false);
      });
    return () => pending.abort();
  }, [requestKey, shouldCompare, attempt, metric]);
  function change(values: Record<string, string | null>) {
    patch({ ...values, compared: null });
  }
  function applyCohort(
    cohort: NonNullable<ComparisonResponse["available_cohorts"]>[number],
  ) {
    const axes = new Set([
      "period_start",
      "period_end",
      "boundary_code",
      "measurement_method",
      "definition_version",
    ]);
    change({
      period_start: String(cohort.dimensions.period_start || ""),
      period_end: String(cohort.dimensions.period_end || ""),
      boundary: String(cohort.dimensions.boundary_code || ""),
      method: String(cohort.dimensions.measurement_method || ""),
      dimensions: JSON.stringify(
        Object.fromEntries(
          Object.entries(cohort.dimensions)
            .filter(([key, value]) => !axes.has(key) && value !== null)
            .map(([key, value]) => [key, String(value)]),
        ),
      ),
    });
  }
  const chartable =
    !!result?.rows.length &&
    result.rows.every((row) => pointValue(row) !== null) &&
    new Set(
      result.rows.map(
        (row) =>
          observationGroup(row, false) +
          ":" +
          row.period_start +
          ":" +
          row.period_end,
      ),
    ).size === 1;
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">PEER COMPARISON</div>
        <h1>在一致口径下比较</h1>
        <p>
          选择企业与指标，查看同一期间、边界、方法和必要维度的数据。绝对量描述披露规模，不代表
          ESG 优劣排名。
        </p>
      </div>
      <form
        className="comparison-layout"
        onSubmit={(event) => {
          event.preventDefault();
          if (periodStart && periodEnd && periodStart > periodEnd) {
            setError("起始日期不能晚于结束日期。");
            return;
          }
          patch({ metric: metricId, compared: "1" });
          setAttempt((value) => value + 1);
        }}
      >
        <section className="panel comparison-form">
          <div className="section-heading">
            <h2>
              <span className="step-number">01</span>选择指标与口径
            </h2>
          </div>
          <div className="form-body">
            <label className="field">
              主要指标
              <select
                value={metricId}
                onChange={(event) =>
                  change({ metric: event.target.value, dimensions: null })
                }
                required
              >
                <option value="" disabled>
                  选择一个主要指标
                </option>
                {metrics.map((item) => (
                  <option
                    key={item.metric_id}
                    value={item.metric_id}
                    disabled={!numericMetric(item)}
                  >
                    {clauseReference(item)} · {item.label_zh}
                    {!numericMetric(item) ? "（定性展示）" : ""}
                  </option>
                ))}
              </select>
            </label>
            {metric && (
              <div className="selected-metric">
                <span className="mono">{clauseReference(metric)}</span>
                <strong>{metric.label_zh}</strong>
                <p>{metric.description}</p>
                <span className="badge neutral">
                  标准单位：{unitLabel(metric.canonical_unit)}
                </span>
              </div>
            )}
            <div className="form-row">
              <label className="field">
                期间起始
                <input
                  type="date"
                  value={periodStart}
                  onChange={(event) =>
                    change({ period_start: event.target.value })
                  }
                />
              </label>
              <label className="field">
                期间结束
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(event) =>
                    change({ period_end: event.target.value })
                  }
                />
              </label>
            </div>
            <div className="form-row">
              <label className="field">
                披露边界
                <select
                  value={boundary}
                  onChange={(event) => change({ boundary: event.target.value })}
                >
                  <option value="">由数据核验</option>
                  {[
                    ...new Set(
                      [
                        boundary,
                        "consolidated_group",
                        "listed_company",
                        "legal_entity",
                        "subsidiary",
                        "own_operations",
                      ].filter(Boolean),
                    ),
                  ].map((value) => (
                    <option key={value} value={value}>
                      {contextLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                计量方法
                <input
                  value={method}
                  list="comparison-methods"
                  placeholder="留空，由数据核验"
                  onChange={(event) => change({ method: event.target.value })}
                />
                <datalist id="comparison-methods">
                  <option value="location_based">地点法</option>
                  <option value="market_based">市场法</option>
                  <option value="reported">报告披露方法</option>
                </datalist>
              </label>
            </div>
            {metric?.fixed_dimensions && (
              <div className="dimension-fields">
                <h3>指标固定口径</h3>
                <Dimensions values={metric.fixed_dimensions} />
              </div>
            )}
            {metric && editableDimensionKeys(metric).length > 0 && (
              <details className="dimension-fields">
                <summary>
                  更多比较条件 · {editableDimensionKeys(metric).length} 个维度
                </summary>
                {editableDimensionKeys(metric).map((key) => (
                  <label className="field" key={key}>
                    {dimensionLabels[key] || key}
                    <input
                      list={"dimension-" + key}
                      value={dimensions[key] || ""}
                      onChange={(event) =>
                        change({
                          dimensions: JSON.stringify({
                            ...dimensions,
                            [key]: event.target.value,
                          }),
                        })
                      }
                      placeholder="留空，由数据核验"
                    />
                    <datalist id={"dimension-" + key}>
                      {metric.dimension_constraints?.[key]?.allowed_values?.map(
                        (value) => (
                          <option key={value} value={value}>
                            {contextLabel(value)}
                          </option>
                        ),
                      )}
                    </datalist>
                  </label>
                ))}
              </details>
            )}
            {Object.keys(dimensions).length > 0 && (
              <Dimensions values={dimensions} />
            )}
            <p className="form-help">
              留空时先核验可用口径。存在多组口径时，下方会提供可选择的组合。
            </p>
          </div>
        </section>
        <section className="panel comparison-entities">
          <div className="section-heading">
            <h2>
              <span className="step-number">02</span>选择企业
            </h2>
            <span className="muted">已选 {selected.length} 家</span>
          </div>
          <div className="form-body">
            <label className="search-field small">
              <Icon name="search" size={17} />
              <input
                aria-label="搜索待比较企业"
                placeholder="搜索企业"
                value={entityQuery}
                onChange={(event) => setEntityQuery(event.target.value)}
              />
            </label>
            <div className="entity-options">
              {entities
                .filter((entity) =>
                  entity.name_zh
                    .toLowerCase()
                    .includes(entityQuery.toLowerCase()),
                )
                .map((entity) => (
                  <label
                    className={
                      "entity-option " +
                      (selected.includes(entity.entity_ref) ? "selected" : "")
                    }
                    key={entity.entity_ref}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(entity.entity_ref)}
                      onChange={(event) =>
                        change({
                          companies: JSON.stringify(
                            event.target.checked
                              ? [...selected, entity.entity_ref]
                              : selected.filter(
                                  (ref) => ref !== entity.entity_ref,
                                ),
                          ),
                        })
                      }
                    />
                    <span className="entity-avatar small">
                      {entity.name_zh.slice(0, 1)}
                    </span>
                    <span>
                      <strong>
                        {entity.name_zh} <DemoBadge entity={entity} />
                      </strong>
                      <small>
                        <Kind value={entity.entity_kind} /> ·{" "}
                        {entity.industry_code || "行业未提供"}
                      </small>
                    </span>
                    <span className="option-coverage">
                      {entity.coverage_count} 项
                    </span>
                  </label>
                ))}
            </div>
            {!entities.length && (
              <p className="form-help">此发布版本暂无可选企业。</p>
            )}
            {selected.some(
              (ref) => !entities.some((entity) => entity.entity_ref === ref),
            ) && (
              <p className="form-help">
                部分所选企业不在当前版本；比较结果将说明没有数据的原因。
              </p>
            )}
            <button
              className="button primary compare-submit"
              type="submit"
              disabled={
                !metric ||
                !numericMetric(metric) ||
                !selected.length ||
                loading
              }
            >
              {loading ? "正在核验比较口径…" : "生成比较表"}
              <Icon name="arrow" size={18} />
            </button>
          </div>
        </section>
      </form>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <section className="panel comparison-result">
          <div className="section-heading">
            <div>
              <h2>比较结果</h2>
              <p>
                当前已发布报告 · {result.rows.length}{" "}
                条纳入，{result.excluded.length} 项未纳入
              </p>
              {result.caveats?.length ? (
                <p className="caveat">
                  {result.caveats.map((code) => CAVEAT_TEXT[code] || code).join("；")}
                </p>
              ) : null}
            </div>
            {result.rows.length > 0 && (
              <DisplaySwitch
                value={display}
                onChange={(value) => patch({ comparison_display: value })}
              />
            )}
          </div>
          {!!result.available_cohorts?.length && (
            <div className="cohort-options">
              <h3>选择一种可用口径</h3>
              <p>这些口径分别展示。选择一个，再生成比较表。</p>
              <div>
                {result.available_cohorts.map((cohort, index) => (
                  <button
                    type="button"
                    className="cohort-option"
                    key={index}
                    onClick={() => applyCohort(cohort)}
                  >
                    <strong>
                      {periodLabel({
                        period_start: String(
                          cohort.dimensions.period_start || "",
                        ),
                        period_end: String(cohort.dimensions.period_end || ""),
                      })}
                    </strong>
                    <span>
                      {contextLabel(
                        String(cohort.dimensions.boundary_code || ""),
                      )}{" "}
                      ·{" "}
                      {contextLabel(
                        String(cohort.dimensions.measurement_method || ""),
                      )}
                    </span>
                    <Dimensions
                      values={Object.fromEntries(
                        Object.entries(cohort.dimensions).filter(
                          ([key]) =>
                            ![
                              "period_start",
                              "period_end",
                              "boundary_code",
                              "measurement_method",
                              "definition_version",
                            ].includes(key),
                        ),
                      )}
                    />
                    <small>
                      {cohort.entity_refs.length} 家企业 ·{" "}
                      {cohort.observation_count} 条观测
                    </small>
                    <Icon name="arrow" size={15} />
                  </button>
                ))}
              </div>
            </div>
          )}
          {result.rows.length > 0 ? (
            <>
              {display === "chart" && chartable ? (
                <>
                  <div className="chart-unit">
                    {metric?.label_zh} ·{" "}
                    {unitLabel(result.rows[0].canonical_unit)}
                  </div>
                  <DataChart
                    rows={result.rows}
                    kind="comparison"
                    onEvidence={onEvidence}
                  />
                  <p className="chart-note">
                    按所选企业顺序展示，不构成 ESG 评级或排名。
                  </p>
                </>
              ) : (
                <>
                  {display === "chart" && !chartable && (
                    <p className="chart-note">
                      这些数据包含不同口径、上下限或区间，使用数据表分别呈现。
                    </p>
                  )}
                  <ObservationTable
                    rows={result.rows}
                    onEvidence={onEvidence}
                    showEntity
                  />
                </>
              )}
            </>
          ) : (
            <Notice title="没有满足本次条件的观测">
              选择可用口径，或查看未纳入原因。
            </Notice>
          )}
          {result.equivalent_alternatives?.map(alternative=><div className="excluded-row" key={alternative.alternative_observation_id}><strong>{alternative.entity_name}</strong><span>同口径计算值与报告披露相同，本表使用报告披露值。</span><button className="text-button" onClick={()=>onEvidence(alternative.alternative_observation_id)}>查看计算来源</button></div>)}
          {result.excluded.length > 0 && (
            <div className="excluded-section">
              <h3>未纳入本次比较</h3>
              {result.excluded.map((excluded, index) => (
                <div className="excluded-row" key={index}>
                  <strong>
                    {entities.find(
                      (entity) => entity.entity_ref === excluded.entity_ref,
                    )?.name_zh || "所选企业"}
                  </strong>
                  <span>{excluded.reasons.map(reasonLabel).join("；")}</span>
                  {excluded.observation_id && (
                    <button
                      className="text-button"
                      onClick={() => onEvidence(excluded.observation_id!)}
                    >
                      查看披露
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </>
  );
}
