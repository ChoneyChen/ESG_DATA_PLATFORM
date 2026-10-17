import { boundQualifiers } from "../chart-contract";
import { formatValue, periodLabel, unitLabel } from "../format";
import type { Observation } from "../types";
import { Icon } from "./Ui";
export function RangeDisclosures({
  rows,
  onEvidence,
}: {
  rows: Observation[];
  onEvidence: (id: string) => void;
}) {
  const ranges = rows.filter(
    (row) =>
      row.lower_bound !== null ||
      row.upper_bound !== null ||
      boundQualifiers.has(row.precision_qualifier || ""),
  );
  if (!ranges.length) return null;
  return (
    <div className="range-disclosures">
      <h3>区间与上下限披露</h3>
      <p>保留披露限制，不转换为单一数值。</p>
      <div>
        {ranges.map((row) => (
          <article className="range-card" key={row.observation_id}>
            <span className="eyebrow">
              {row.entity_name} · {periodLabel(row)}
            </span>
            <h4>{row.label_zh}</h4>
            <div className="range-value">
              <span
                className={
                  "range-symbol " +
                  (row.lower_bound !== null && row.upper_bound !== null
                    ? "bounded"
                    : row.lower_bound !== null ||
                        [
                          "gt",
                          "gte",
                          "greater_than",
                          "greater_or_equal",
                        ].includes(row.precision_qualifier || "")
                      ? "lower"
                      : "upper")
                }
                aria-hidden="true"
              />
              <strong>{formatValue(row)}</strong>
              <span>{unitLabel(row.canonical_unit)}</span>
            </div>
            <button
              className="text-button"
              onClick={() => onEvidence(row.observation_id)}
            >
              查看原文 <Icon name="arrow" size={14} />
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}
