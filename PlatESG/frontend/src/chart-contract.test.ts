import { describe, expect, it } from "vitest";
import { buildTrendSeries, pointValue } from "./chart-contract";
import type { Observation } from "./types";
const base: Observation = {
  observation_id: "o1",
  entity_ref: "a",
  entity_name: "A",
  metric_id: "energy",
  source_datapoint_id: "E1-5_01",
  label_zh: "能源",
  period_start: "2022-01-01",
  period_end: "2022-12-31",
  boundary_code: "group",
  measurement_method: "reported",
  dimensions: {},
  canonical_unit: "MWh",
  value_numeric: "1",
  lower_bound: null,
  upper_bound: null,
  statement_text: null,
  raw_unit: "MWh",
  raw_value_text: "1",
  comparison_eligibility: "comparable",
  comparison_reasons: [],
  publication_status: "published",
  definition_version: "1",
};
const next = (overrides: Partial<Observation> = {}): Observation => ({
  ...base,
  observation_id: "o2",
  period_start: "2024-01-01",
  period_end: "2024-12-31",
  value_numeric: "3",
  ...overrides,
});
describe("scientific chart data", () => {
  it('keeps reported and derived extension trends distinct and honors explicit comparability',()=>{
    const extension={...base,metric_id:'project.esrs.e1-6.total',source_datapoint_id:null,clause_kind:'project_extension' as const,extension_id:'project.esrs.e1-6.total',measurement_method:null,dimensions:{amount_basis:'gross',scope_2_method:'location_based'}};
    const later={...extension,observation_id:'reported-later',period_start:'2024-01-01',period_end:'2024-12-31'};
    const derived=[{...extension,observation_id:'derived-first',value_origin:'derived' as const},{...later,observation_id:'derived-later',value_origin:'derived' as const}];
    const series=buildTrendSeries([extension,later,...derived]);expect(series).toHaveLength(2);expect(series.map(group=>group.observation.value_origin || 'reported')).toEqual(['reported','derived']);
    expect(buildTrendSeries([extension,{...later,comparison_eligibility:'not_comparable'}])).toEqual([]);
  });
  it("leaves missing calendar years null without fabricating interpolation or zero", () => {
    const series = buildTrendSeries([base, next()])[0];
    expect(series.years).toEqual(["2022", "2023", "2024"]);
    expect(series.points).toEqual([1, null, 3]);
    expect(series.rows[1]).toBeNull();
  });
  it("never uses a threshold or an interval as a point even if a numeric value also exists", () => {
    expect(
      pointValue({ ...base, precision_qualifier: "less_than" }),
    ).toBeNull();
    expect(
      pointValue({ ...base, lower_bound: "1", upper_bound: "3" }),
    ).toBeNull();
    expect(
      pointValue({ ...base, value_numeric: null, upper_bound: "3" }),
    ).toBeNull();
  });
  it("retains an explicit zero and converts a ratio for chart geometry only", () => {
    expect(pointValue({ ...base, value_numeric: "0" })).toBe(0);
    expect(
      pointValue({ ...base, canonical_unit: "ratio", value_numeric: "0.25" }),
    ).toBe(25);
  });
  it("separates entities, units, boundaries, methods, pollutants and definition versions", () => {
    for (const overrides of [
      { entity_ref: "b" },
      { canonical_unit: "tonne" },
      { boundary_code: "subsidiary" },
      { measurement_method: "market_based" },
      { dimensions: { pollutant: "NOx" } },
      { definition_version: "2" },
    ])
      expect(buildTrendSeries([base, next(overrides)])).toEqual([]);
  });
  it("keeps unknown methods and duplicate periods out of a joined trend", () => {
    expect(
      buildTrendSeries([base, next({ measurement_method: null })]),
    ).toEqual([]);
    expect(
      buildTrendSeries([
        base,
        { ...base, observation_id: "duplicate" },
        next(),
      ]),
    ).toEqual([]);
  });
  it("keeps an explicit upper-limit year as a gap in an otherwise usable trend", () => {
    const limit = next({
      observation_id: "limit",
      period_start: "2023-01-01",
      period_end: "2023-12-31",
      value_numeric: null,
      upper_bound: "2",
    });
    expect(buildTrendSeries([base, limit, next()])[0].points).toEqual([
      1,
      null,
      3,
    ]);
  });
  it("does not merge annual and instant observations or draw non-finite geometry", () => {
    expect(
      buildTrendSeries([base, next({ period_start: "2024-12-31" })]),
    ).toEqual([]);
    expect(pointValue({ ...base, value_numeric: "Infinity" })).toBeNull();
  });
});
