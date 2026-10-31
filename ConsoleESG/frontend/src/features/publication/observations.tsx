import { useState } from "react";
import type { Observation, CaptureFactPage } from "@esg/database-contracts";
import { Modal, Badge } from "../../shared/ui";
import { periodText, label } from "../../shared/format";

const identityLabels: Record<string, string> = {
  reporting_entity: "统计主体",
  reporting_period: "测量期间",
  reporting_boundary: "统计范围",
  measurement_method: "方法",
  value_role: "值角色",
  aggregation_role: "汇总角色",
  entity_aggregation_role: "主体汇总",
  denominator: "分母",
};

export function CapturedFactGrid({ rows }: { rows: CaptureFactPage["items"] }) {
  const [selected, setSelected] =
    useState<CaptureFactPage["items"][number]["record"]>();
  return (
    <>
      <div className="table-scroll">
        <table className="data-table observation-grid">
          <thead>
            <tr>
              <th>项目</th>
              <th>原值 / 原文</th>
              <th>单位</th>
              <th>期间</th>
              <th>实际主体</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ record: row }) => (
              <tr key={row.source_fact_id}>
                <td>{row.source_row_label || "来源原值"}</td>
                <td
                  className={
                    row.value.kind === "quantitative"
                      ? "numeric-cell"
                      : "statement-cell"
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
                <td>{periodText(row.identity?.reporting_period?.value)}</td>
                <td>{row.identity?.reporting_entity?.raw_text || "未陈述"}</td>
                <td>
                  <button
                    className="text-button"
                    onClick={() => setSelected(row)}
                  >
                    查看
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <Modal
          title={selected.source_row_label || "来源原值"}
          onClose={() => setSelected(undefined)}
        >
          <p className="lead raw-statement">
            {selected.value.kind === "quantitative"
              ? `${selected.value.value_raw} ${selected.value.unit_raw || ""}`
              : selected.value.statement_raw}
          </p>
          <dl className="fact-properties">
            {Object.entries({
              ...selected.identity,
              ...selected.identity?.dimensions,
            })
              .filter(
                ([key, field]) =>
                  key !== "dimensions" &&
                  field &&
                  "raw_text" in field &&
                  field.raw_text,
              )
              .map(([key, field]) => (
                <div className="property-pair" key={key}>
                  <dt>{identityLabels[key] || key}</dt>
                  <dd>{"raw_text" in field ? String(field.raw_text) : ""}</dd>
                </div>
              ))}
          </dl>
          <h3>原文证据</h3>
          <div className="evidence-list">
            {selected.evidence.map((item, index) => (
              <blockquote key={index}>
                <small>
                  {item.page_index != null
                    ? `PDF第${item.page_index + 1}页`
                    : "来源位置"}
                </small>
                <p>{item.excerpt}</p>
              </blockquote>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}
export function ObservationGrid({ rows }: { rows: Observation[] }) {
  const [selected, setSelected] = useState<Observation>();
  return (
    <>
      <div className="table-scroll">
        <table className="data-table observation-grid">
          <thead>
            <tr>
              <th>项目</th>
              <th>原值 / 原文</th>
              <th>单位</th>
              <th>期间</th>
              <th>实际主体</th>
              <th>目标关系</th>
              <th>来源</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.observation_id}>
                <td>{row.source_row_label || row.title}</td>
                <td
                  className={
                    row.value.kind === "quantitative"
                      ? "numeric-cell"
                      : "statement-cell"
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
                <td>{row.identity?.reporting_entity?.raw_text || "未陈述"}</td>
                <td>
                  <Badge>{label(row.relation)}</Badge>
                </td>
                <td className="source-title">
                  {row.source.title || row.source.report_company || "来源报告"}
                </td>
                <td>
                  <button
                    className="text-button"
                    onClick={() => setSelected(row)}
                  >
                    查看
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <Modal
          title={selected.source_row_label || selected.title}
          onClose={() => setSelected(undefined)}
        >
          <p className="lead raw-statement">
            {selected.value.kind === "quantitative"
              ? `${selected.value.value_raw} ${selected.value.unit_raw || ""}`
              : selected.value.statement_raw}
          </p>
          <dl className="fact-properties">
            <dt>期间</dt>
            <dd>{periodText(selected.period)}</dd>
            <dt>目标关系</dt>
            <dd>
              {label(selected.relation)} · {selected.mapping?.reason}
            </dd>
            {Object.entries(selected.identity)
              .filter(
                ([key, value]) =>
                  key !== "dimensions" &&
                  value &&
                  typeof value === "object" &&
                  "raw_text" in value &&
                  value.raw_text,
              )
              .map(([key, value]) => (
                <div className="property-pair" key={key}>
                  <dt>{identityLabels[key] || key}</dt>
                  <dd>{"raw_text" in value ? String(value.raw_text) : ""}</dd>
                </div>
              ))}
            {Object.entries(selected.identity.dimensions || {})
              .filter(([, field]) => field.raw_text)
              .map(([key, field]) => (
                <div className="property-pair" key={key}>
                  <dt>{key}</dt>
                  <dd>{field.raw_text}</dd>
                </div>
              ))}
          </dl>
          <h3>原文证据</h3>
          <div className="evidence-list">
            {selected.evidence.map((item, index) => (
              <blockquote key={index}>
                <small>
                  {item.page_index != null
                    ? `PDF第${item.page_index + 1}页`
                    : "来源位置"}{" "}
                  · {item.role}
                </small>
                <p>{item.excerpt}</p>
              </blockquote>
            ))}
          </div>
          <p className="muted">
            {selected.source.title || "来源报告"} ·{" "}
            {selected.comparison.eligible
              ? "满足声明的比较条件"
              : "比较口径尚未确认"}
          </p>
        </Modal>
      )}
    </>
  );
}

export function Pager({
  total,
  returned,
  next,
  history,
  onPage,
}: {
  total: number;
  returned: number;
  next?: string | null;
  history: (string | null)[];
  onPage: (value: (string | null)[]) => void;
}) {
  return (
    <div className="table-pagination">
      <span>
        共 {total} 条 · 本页 {returned} 条
      </span>
      <div>
        <button
          className="button-secondary"
          disabled={history.length === 1}
          onClick={() => onPage(history.slice(0, -1))}
        >
          上一页
        </button>
        <span>第 {history.length} 页</span>
        <button
          className="button-secondary"
          disabled={!next}
          onClick={() => next && onPage([...history, next])}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
