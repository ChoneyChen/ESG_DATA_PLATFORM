import { clauseReference, valueOriginLabel } from "../format";
import { useEffect, useRef } from "react";
import { apiUrl } from "../api";
import { useResource } from "../hooks/useResource";
import {
  contextLabel,
  dimensionLabels,
  formatDecimal,
  formatValue,
  periodLabel,
  reasonLabel,
  unitLabel,
} from "../format";
import { safePublicUrl } from "../public-contract";
import type { Evidence } from "../types";
import { Dimensions, Eligibility, ErrorNotice, Icon, Loading } from "./Ui";
import { ReportedProperties } from "./ReportedProperties";
import { ReviewStamp, ReviewTrail } from "./Review";
export default function EvidenceDrawer({
  observationId,
  dataset,
  close,
}: {
  observationId: string;
  dataset: string;
  close: () => void;
}) {
  const evidence = useResource<Evidence>(
    apiUrl("/v1/observations/" + encodeURIComponent(observationId), {
      ...(dataset ? { dataset_version: dataset } : {}),
    }),
  );
  const closeRef = useRef<HTMLButtonElement>(null),
    drawer = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        const controls = [
          ...(drawer.current?.querySelectorAll<HTMLElement>(
            'button, a[href], input, select, summary, [tabindex="0"]',
          ) || []),
        ].filter((element) => !element.hasAttribute("disabled"));
        const first = controls[0],
          last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      document.body.style.overflow = old;
      previous?.focus();
    };
  }, [close]);
  const item = evidence.data;
  return (
    <div className="drawer-overlay" onClick={close}>
      <aside
        ref={drawer}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="原文证据与修订详情"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <span className="eyebrow">原文证据 · 复核凭证</span>
          {evidence.data && (
            <ReviewStamp revisions={item?.public_revisions?.length || 0} />
          )}
          <button
            className="icon-button"
            ref={closeRef}
            aria-label="关闭证据详情"
            onClick={close}
          >
            <Icon name="close" />
          </button>
        </div>
        {evidence.loading ? (
          <Loading />
        ) : evidence.error ? (
          <ErrorNotice error={evidence.error} retry={evidence.reload} />
        ) : (
          item && (
            <div className="drawer-content">
              <span className="mono metric-id">{clauseReference(item)}</span>
              <h2>{item.label_zh}</h2>
              <p className="muted">{valueOriginLabel(item.value_origin)}</p>
              <p className="muted">
                {item.entity_name} · {periodLabel(item)}
              </p>
              <div className="evidence-value">
                <strong>{formatValue(item)}</strong>
                <span>
                  {item.statement_text && item.value_numeric === null
                    ? "定性披露"
                    : unitLabel(item.canonical_unit)}
                </span>
                <Eligibility value={item.comparison_eligibility} />
              </div>
              <ReviewTrail item={item} />
              {item.calculation&&<section className="drawer-section"><h3>计算来源</h3><p>将下列已审核、口径相容的披露量相加。公式 v{item.calculation.rule_version}，输入数据版本 {item.calculation.input_dataset_version}。</p><ul>{item.calculation.operands.map(operand=><li key={operand.observation_id}>{operand.label_zh}：{formatDecimal(operand.numeric_value)} {unitLabel(operand.canonical_unit)}</li>)}</ul></section>}
              <section className="drawer-section">
                <h3>披露与计量口径</h3>
                <dl className="evidence-dl">
                  <div>
                    <dt>报告原值</dt>
                    <dd>
                      {item.raw_value_text ?? "—"} {item.raw_unit || ""}
                    </dd>
                  </div>
                  {item.source_row_label && (
                    <div>
                      <dt>报告行名</dt>
                      <dd>{item.source_row_label}</dd>
                    </div>
                  )}
                  <div>
                    <dt>期间</dt>
                    <dd>{periodLabel(item)}</dd>
                  </div>
                  <div>
                    <dt>披露边界</dt>
                    <dd>{contextLabel(item.boundary_code)}</dd>
                  </div>
                  <div>
                    <dt>计量方法</dt>
                    <dd>{contextLabel(item.measurement_method)}</dd>
                  </div>
                  <div>
                    <dt>指标定义</dt>
                    <dd>{item.definition_version}</dd>
                  </div>
                  <div>
                    <dt>报告版本</dt>
                    <dd>{item.report_version ? `第 ${item.report_version} 版` : "当前发布"}</dd>
                  </div>
                </dl>
                <Dimensions values={item.dimensions} />
                <PrintedDimensions item={item} />
                <ContextSources item={item} />
                {!!item.comparison_reasons?.length && (
                  <p className="reason-box">
                    {item.comparison_reasons.map(reasonLabel).join("；")}
                  </p>
                )}
              </section>
              <ReportedProperties properties={item.reported_properties} />
              <section className="drawer-section">
                <h3>报告与原文</h3>
                <div className="report-title">
                  <Icon name="file" size={20} />
                  {item.report?.report_title_snapshot || "来源报告"}
                </div>
                {item.report?.publisher && (
                  <p className="muted">
                    {item.report.publisher}
                    {item.report.report_year
                      ? " · " + item.report.report_year
                      : ""}
                  </p>
                )}
                {safePublicUrl(item.report?.public_url) && (
                  <a
                    href={safePublicUrl(item.report.public_url)!}
                    target="_blank"
                    rel="noreferrer"
                    className="text-button"
                  >
                    查看原始报告 <Icon name="arrow" size={16} />
                  </a>
                )}
                {item.sources?.length ? (
                  item.sources.map((source, index) => (
                    <figure className="source-quote" key={index}>
                      <figcaption>
                        披露片段 {index + 1}
                        {source.printed_page_label
                          ? " · 印刷页码 " + source.printed_page_label
                          : source.page_number !== null
                            ? " · 文档页码 " + source.page_number
                            : ""}
                      </figcaption>
                      <blockquote>
                        {source.source_quote || "此来源未附原文片段。"}
                      </blockquote>
                      {source.unit_conversion && (
                        <div className="source-conversion">
                          <span>单位换算</span>
                          <p>
                            {source.raw_value_text ?? item.raw_value_text ?? ""}{" "}
                            {unitLabel(
                              source.raw_unit ||
                                source.unit_conversion.source_unit,
                            )}{" "}
                            →{" "}
                            {item.value_numeric !== null
                              ? formatDecimal(item.value_numeric)
                              : formatValue({
                                  ...item,
                                  canonical_unit: null,
                                })}{" "}
                            {["ratio", "unit.ratio"].includes(
                              source.unit_conversion.canonical_unit || "",
                            )
                              ? "比例（小数）"
                              : unitLabel(
                                  source.unit_conversion.canonical_unit,
                                )}
                          </p>
                          {source.unit_conversion.multiplier != null &&
                            source.unit_conversion.divisor != null && (
                              <small>
                                原值 ×{" "}
                                {formatDecimal(
                                  source.unit_conversion.multiplier,
                                )}{" "}
                                ÷{" "}
                                {formatDecimal(source.unit_conversion.divisor)}
                              </small>
                            )}
                        </div>
                      )}
                    </figure>
                  ))
                ) : (
                  <p className="muted">此数据未返回披露片段。</p>
                )}
              </section>
              {!!item.public_revisions?.length && (
                <section className="drawer-section">
                  <h3>公开修订说明</h3>
                  <ol className="revision-list">
                    {item.public_revisions.map((revision, index) => (
                      <li key={index}>
                        <Icon name="clock" size={16} />
                        <div>
                          <strong>修订 {revision.revision}</strong>
                          {revision.published_at && (
                            <span>
                              {revision.published_at
                                .replace("T", " ")
                                .slice(0, 19)}
                            </span>
                          )}
                          <p>
                            {revision.change_summary ||
                              "本次发布未附修订说明。"}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              <p className="drawer-version-note">
                当前内容来自已发布报告{item.report_version ? `（第 ${item.report_version} 版）` : ""}。
              </p>
            </div>
          )
        )}
      </aside>
    </div>
  );
}

/**
 * What the report itself printed, when it differs from the clause reading.
 * A clause may fix energy_source=renewable while the printed row is 风能;
 * comparison still uses the clause value, the printed one is the evidence.
 */
const PRINTED_CONTEXT_AXES = new Set([
  "period_start",
  "period_end",
  "reporting_period",
  "reporting_entity",
  "reporting_boundary",
  "entity_ref",
]);
function PrintedDimensions({ item }: { item: Evidence }) {
  const printed = Object.entries(item.reported_dimensions || {})
    .map(([key, values]) => [key, (Array.isArray(values) ? values : [values]).map(String)] as [string, string[]])
    .filter(([key, values]) => {
      if (PRINTED_CONTEXT_AXES.has(key)) return false;
      const clause = item.dimensions?.[key];
      const clauseValues = Array.isArray(clause) ? clause.map(String) : clause == null ? [] : [String(clause)];
      return [...values].sort().join("|") !== [...clauseValues].sort().join("|");
    });
  if (!printed.length) return null;
  return (
    <div className="dimension-tags">
      <span>报告原印分类：</span>
      {printed.map(([key, values]) => (
        <span key={key}>
          {Object.hasOwn(dimensionLabels, key) ? dimensionLabels[key] : key}：{values.join("、")}
        </span>
      ))}
    </div>
  );
}

const CONTEXT_FIELD_LABELS: Record<string, string> = {
  period_start: "期间",
  period_end: "期间",
  entity_kind: "主体类型",
  value_role: "值角色",
  measurement_method: "计量方法",
  reporting_boundary: "披露边界",
};
const CONTEXT_LEVEL_LABELS: Record<string, string> = {
  cell: "单元格原文",
  header: "表头",
  row: "行标签",
  table: "表格",
  footnote: "脚注",
  report_identity: "报告身份继承",
  review: "审核确认",
  registry: "主体登记",
  clause: "条款固定",
  not_stated: "未陈述",
};
/** Where each context field came from; inherited values never look printed. */
function ContextSources({ item }: { item: Evidence }) {
  const entries = Object.entries(item.context_sources || {}).filter(
    ([, level]) => level && level !== "source",
  );
  if (!entries.length) return null;
  return (
    <div className="dimension-tags">
      <span>口径来源：</span>
      {entries.map(([field, level]) => (
        <span key={field}>
          {CONTEXT_FIELD_LABELS[field] || field}：{CONTEXT_LEVEL_LABELS[level] || level}
        </span>
      ))}
    </div>
  );
}
