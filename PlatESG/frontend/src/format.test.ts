import { describe, expect, it } from "vitest";
import {
  contextLabel,
  dimensionLabels,
  formatDecimal,
  formatValue,
  periodLabel,
  shiftDecimal,
  stableDimensions,
  trendGroups,
} from "./format";
import type { Observation } from "./types";

function observation(overrides: Partial<Observation> = {}): Observation {
  return {
    observation_id: "demo-2023",
    entity_ref: "demo.entity",
    entity_name: "演示企业",
    metric_id: "energy_total",
    source_datapoint_id: "E1-5_01",
    label_zh: "总能源消耗",
    period_start: "2023-01-01",
    period_end: "2023-12-31",
    boundary_code: "consolidated_group",
    measurement_method: "reported",
    dimensions: {},
    canonical_unit: "MWh",
    value_numeric: "99999999999999999.000001",
    lower_bound: null,
    upper_bound: null,
    statement_text: null,
    raw_unit: "MWh",
    raw_value_text: "99999999999999999.000001",
    comparison_eligibility: "comparable",
    comparison_reasons: [],
    publication_status: "published",
    definition_version: "1.0.0",
    ...overrides,
  };
}

describe("precise source values", () => {
  it("keeps decimal precision beyond JavaScript safe integers", () => {
    expect(formatDecimal("99999999999999999.000001")).toBe(
      "99,999,999,999,999,999.000001",
    );
  });
  it("keeps explicit zero distinct from missing values", () => {
    expect(formatValue(observation({ value_numeric: "0" }))).toBe("0");
    expect(formatValue(observation({ value_numeric: null }))).toBe("—");
  });
  it("converts normalized ratios for presentation through string arithmetic", () => {
    expect(shiftDecimal("0.327001", 2)).toBe("32.7001");
    expect(
      formatValue(
        observation({ canonical_unit: "ratio", value_numeric: "0.327001" }),
      ),
    ).toBe("32.7001");
    expect(shiftDecimal("0.000001", 2)).toBe("0.0001");
    expect(shiftDecimal("-0.000001", 2)).toBe("-0.0001");
  });
  it("does not invent a point value from an interval", () => {
    expect(
      formatValue(
        observation({
          value_numeric: null,
          lower_bound: null,
          upper_bound: "0.010",
        }),
      ),
    ).toBe("上界 0.010");
    expect(
      formatValue(
        observation({
          value_numeric: null,
          lower_bound: null,
          upper_bound: "0.010",
          precision_qualifier: "less_than",
        }),
      ),
    ).toBe("< 0.010");
    expect(
      formatValue(
        observation({
          value_numeric: null,
          lower_bound: "10",
          upper_bound: "20",
        }),
      ),
    ).toBe("10 – 20");
  });
  it("keeps inclusive bounds and approximate reported values explicit", () => {
    expect(
      formatValue(
        observation({
          value_numeric: null,
          lower_bound: "3",
          precision_qualifier: "greater_or_equal",
        }),
      ),
    ).toBe("≥ 3");
    expect(
      formatValue(
        observation({
          value_numeric: "10",
          precision_qualifier: "approximate",
        }),
      ),
    ).toBe("≈ 10");
  });
  it("does not simplify a partial period to a calendar year", () => {
    expect(periodLabel(observation())).toBe("2023 年度");
    expect(periodLabel(observation({ period_start: "2023-04-01" }))).toBe(
      "2023-04-01 至 2023-12-31",
    );
  });
});

describe("trend comparison boundaries", () => {
  const current = observation({
    observation_id: "demo-2024",
    period_start: "2024-01-01",
    period_end: "2024-12-31",
  });
  it("can graph consistent annual observations", () => {
    expect(
      trendGroups([current, observation()])[0].map((item) => item.period_end),
    ).toEqual(["2023-12-31", "2024-12-31"]);
  });
  it("keeps scope 2 methods separate", () => {
    expect(
      trendGroups([
        observation({ measurement_method: "market_based" }),
        { ...current, measurement_method: "location_based" },
      ]),
    ).toEqual([]);
  });
  it("excludes unknown boundary and comparison-ineligible observations", () => {
    expect(
      trendGroups([observation({ boundary_code: null }), current]),
    ).toEqual([]);
    expect(
      trendGroups([
        observation({ comparison_eligibility: "not_comparable" }),
        current,
      ]),
    ).toEqual([]);
  });
  it("does not connect alternatives in the same period", () => {
    expect(
      trendGroups([
        observation(),
        observation({ observation_id: "duplicate" }),
        current,
      ]),
    ).toEqual([]);
  });
  it("does not mix reporting periods or definition versions", () => {
    expect(
      trendGroups([observation({ period_start: "2023-04-01" }), current]),
    ).toEqual([]);
    expect(
      trendGroups([observation({ definition_version: "2.0.0" }), current]),
    ).toEqual([]);
  });
  it("does not mix pollutant dimensions regardless of key order", () => {
    expect(stableDimensions({ pollutant: "SOx", media: "air" })).toBe(
      stableDimensions({ media: "air", pollutant: "SOx" }),
    );
    expect(
      trendGroups([
        observation({ dimensions: { pollutant: "SOx" } }),
        { ...current, dimensions: { pollutant: "NOx" } },
      ]),
    ).toEqual([]);
  });
});

describe("readable measurement contracts", () => {
  it("labels every comparison axis in the current environmental dictionary in Chinese", () => {
    for (const key of [
      "energy_source",
      "energy_flow",
      "aggregation_role",
      "energy_accounting_basis",
      "ghg_scope",
      "gwp_basis",
      "consolidation_approach",
      "pollution_medium",
      "release_boundary",
      "pollutant_definition",
      "concern_class",
      "material_flow",
      "classification_basis",
      "water_measure",
      "water_definition",
      "water_risk_class",
      "risk_classification_method",
      "category_coverage",
      "estimation_method",
      "denominator_scope",
    ]) {
      expect(dimensionLabels[key]).toMatch(/[\u4e00-\u9fff]/);
    }
  });
  it("keeps standard method and demo semantics readable without changing raw API codes", () => {
    expect(contextLabel("own_operations")).toBe("自身运营");
    expect(contextLabel("market_based")).toBe("市场法");
    expect(contextLabel("listed_company")).toBe("上市公司");
    expect(contextLabel("demo_100_year_gwp")).toMatch(/^演示/);
    expect(contextLabel("unrecognized_method")).toBe("unrecognized_method");
  });
});
