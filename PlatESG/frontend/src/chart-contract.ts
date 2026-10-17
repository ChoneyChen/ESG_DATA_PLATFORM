import type { Observation } from "./types";
import { formatValue, periodLabel, stableDimensions } from "./format";

export const boundQualifiers = new Set([
  "lt",
  "lte",
  "gt",
  "gte",
  "less_than",
  "less_or_equal",
  "greater_than",
  "greater_or_equal",
]);
export function pointValue(item: Observation): number | null {
  if (
    item.value_numeric === null ||
    item.lower_bound !== null ||
    item.upper_bound !== null ||
    boundQualifiers.has(item.precision_qualifier || "")
  )
    return null;
  const number = Number(item.value_numeric);
  if (!Number.isFinite(number)) return null;
  return ["ratio", "unit.ratio"].includes(item.canonical_unit || "")
    ? number * 100
    : number;
}
export function observationGroup(
  item: Observation,
  includeEntity = true,
): string {
  const basis =
    item.period_start === item.period_end
      ? "instant"
      : [item.period_start?.slice(4), item.period_end?.slice(4)].join(":");
  return JSON.stringify([
    includeEntity ? item.entity_ref : null,
    item.metric_id,
    item.definition_version,
    item.value_origin || 'reported',
    item.canonical_unit,
    item.boundary_code,
    item.measurement_method,
    basis,
    stableDimensions(item.dimensions),
  ]);
}
export interface TrendSeries {
  identity: string;
  observation: Observation;
  years: string[];
  rows: (Observation | null)[];
  points: (number | null)[];
}
export function buildTrendSeries(observations: Observation[]): TrendSeries[] {
  const groups = new Map<string, Observation[]>();
  for (const row of observations) {
    if (
      !row.period_start ||
      !row.period_end ||
      !row.boundary_code ||
      (!row.measurement_method && row.clause_kind!=='project_extension') ||
      !["comparable", "eligible"].includes(row.comparison_eligibility)
    )
      continue;
    const key = observationGroup(row);
    groups.set(key, [...(groups.get(key) || []), row]);
  }
  const result: TrendSeries[] = [];
  for (const [identity, group] of groups) {
    const years = group.map((row) => Number(row.period_end?.slice(0, 4)));
    if (
      years.some(
        (year) => !Number.isInteger(year) || year < 1900 || year > 2200,
      ) ||
      new Set(years).size !== years.length ||
      group.filter((row) => pointValue(row) !== null).length < 2
    )
      continue;
    const min = Math.min(...years),
      max = Math.max(...years);
    const slots = Array.from({ length: max - min + 1 }, (_, index) =>
      String(min + index),
    );
    const rows = slots.map(
      (year) => group.find((row) => row.period_end?.startsWith(year)) || null,
    );
    result.push({
      identity,
      observation: group[0],
      years: slots,
      rows,
      points: rows.map((row) => (row ? pointValue(row) : null)),
    });
  }
  return result;
}
export function chartDescription(rows: Observation[]) {
  return rows
    .map((row) =>
      [row.entity_name, periodLabel(row), formatValue(row)].join("，"),
    )
    .join("；");
}
