import { clauseReference, valueOriginLabel } from "../format";
import {
  contextLabel,
  formatValue,
  periodLabel,
  reasonLabel,
  unitLabel,
} from "../format";
import type { Observation } from "../types";
import { Dimensions, Eligibility, Icon } from "./Ui";
import { ReportedPropertySummary } from "./ReportedProperties";

export function ObservationTable({
  rows,
  onEvidence,
  showEntity = false,
}: {
  rows: Observation[];
  onEvidence: (id: string) => void;
  showEntity?: boolean;
}) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {showEntity && <th>企业</th>}
            <th>指标 / 原编号</th>
            <th>期间</th>
            <th>披露值</th>
            <th>单位与口径</th>
            <th>比较条件</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.observation_id}>
              {showEntity && (
                <td>
                  <strong>{row.entity_name}</strong>
                </td>
              )}
              <td>
                <strong>{row.label_zh}</strong>
                <span className="table-subtitle mono">
                  {clauseReference(row)}
                </span>
              </td>
              <td className="nowrap">{periodLabel(row)}</td>
              <td
                className={
                  row.statement_text && row.value_numeric === null
                    ? "statement-value"
                    : "numeric"
                }
              >
                {formatValue(row)}
                <span className="table-subtitle">{valueOriginLabel(row.value_origin)}</span>
              </td>
              <td>
                {row.statement_text && row.value_numeric === null
                  ? "定性披露"
                  : unitLabel(row.canonical_unit)}
                <span className="table-subtitle">
                  {contextLabel(row.boundary_code)} ·{" "}
                  {contextLabel(row.measurement_method)}
                </span>
                <Dimensions values={row.dimensions} />
                <ReportedPropertySummary properties={row.reported_properties} />
              </td>
              <td>
                <Eligibility value={row.comparison_eligibility} />
                {row.comparison_reasons?.length > 0 && (
                  <span className="table-subtitle">
                    {row.comparison_reasons.map(reasonLabel).join("；")}
                  </span>
                )}
              </td>
              <td>
                <button
                  className="text-button"
                  aria-label={"查看" + row.label_zh + "原文证据"}
                  onClick={() => onEvidence(row.observation_id)}
                >
                  原文 <Icon name="arrow" size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function DisplaySwitch({
  value,
  onChange,
}: {
  value: "chart" | "table";
  onChange: (value: "chart" | "table") => void;
}) {
  return (
    <div className="display-switch" role="group" aria-label="切换展示方式">
      <button
        aria-pressed={value === "chart"}
        onClick={() => onChange("chart")}
        type="button"
      >
        图表
      </button>
      <button
        aria-pressed={value === "table"}
        onClick={() => onChange("table")}
        type="button"
      >
        数据表
      </button>
    </div>
  );
}
