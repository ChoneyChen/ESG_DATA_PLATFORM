export interface Entity {
  entity_ref: string;
  name_zh: string;
  entity_kind: string;
  industry_code: string | null;
  country_code: string | null;
  coverage_count: number;
  is_demo?: boolean;
}

export interface Metric {
  metric_id: string;
  source_datapoint_id: string | null;
  clause_kind?: "original_datapoint" | "project_extension";
  extension_id?: string | null;
  parent_package_id?: string;
  related_source_datapoint_ids?: string[];
  label_zh: string;
  topic: string;
  canonical_unit: string | null;
  value_type: string;
  description: string;
  required_dimensions: (
    string | { name?: string; key?: string; label_zh?: string }
  )[];
  comparison_conditions: string[];
  comparison_dimensions?: string[];
  fixed_dimensions?: Record<string, string>;
  dimension_constraints?: Record<string, { allowed_values?: string[] }>;
  comparison_allowed_values?: Record<string, string[]>;
  definition_version?: string;
}

export interface Observation {
  observation_id: string;
  entity_ref: string;
  entity_name: string;
  metric_id: string;
  source_datapoint_id: string | null;
  clause_kind?: "original_datapoint" | "project_extension";
  extension_id?: string | null;
  parent_package_id?: string;
  related_source_datapoint_ids?: string[];
  value_origin?: "reported" | "derived";
  label_zh: string;
  period_start: string | null;
  period_end: string | null;
  boundary_code: string | null;
  measurement_method: string | null;
  dimensions: Record<string, unknown>;
  reported_properties?: ReportedProperty[];
  canonical_unit: string | null;
  value_numeric: string | null;
  lower_bound: string | null;
  upper_bound: string | null;
  precision_qualifier?: string | null;
  statement_text: string | null;
  raw_unit: string | null;
  raw_value_text: string | null;
  comparison_eligibility: string;
  comparison_reasons: string[];
  publication_status: string;
  definition_version: string;
  revision?: number;
}

export interface ReportedProperty {
  field_key: string;
  label_zh: string;
  value_raw: string | string[];
  value_type: "text" | "number" | "boolean" | "text_array" | "list";
  unit_raw: string | null;
  evidence_scope: "field" | "source";
  evidence: {
    page_number: number | null;
    source_quote: string | null;
    bbox?: unknown;
  }[];
  report_title?: string;
  source_role?: string | null;
  source_label?: string | null;
}

export interface Evidence extends Observation {
  published_at?: string | null;
  calculation?: {
    operation: string;
    rule_version: string;
    input_dataset_version: number;
    operands: {
      observation_id: string;
      label_zh: string;
      numeric_value: string;
      canonical_unit: string;
      value_role?: string;
    }[];
  } | null;
  sources: {
    page_number: number | null;
    printed_page_label?: string | null;
    source_quote: string | null;
    raw_value_text?: string | null;
    raw_unit?: string | null;
    public_url?: string | null;
    unit_conversion?: UnitConversion | null;
  }[];
  report: {
    report_title_snapshot: string | null;
    publisher?: string | null;
    report_year?: number | string | null;
    public_url?: string | null;
  };
  public_revisions?: {
    revision: number | string;
    published_at?: string | null;
    change_summary?: string | null;
  }[];
  dataset_version: string | number;
}
export interface UnitConversion {
  source_unit: string | null;
  canonical_unit: string | null;
  multiplier: string | null;
  divisor: string | null;
}

export interface Coverage {
  entity_ref: string;
  metric_id: string;
  source_datapoint_id: string | null;
  clause_kind?: "original_datapoint" | "project_extension";
  extension_id?: string | null;
  status: string;
  reason: string | null;
  published_count: number;
  periods?: {
    period_start: string | null;
    period_end: string | null;
    status: string;
    reason: string | null;
    published_count: number;
  }[];
}

export interface CoverageResponse extends VersionedItems<Coverage> {
  coverage_as_of?: string;
  publication_as_of?: string;
  coverage_scope?: string;
}

export interface Dataset {
  dataset_version: string | number;
  created_at: string;
  reason: string | null;
  observation_count: number;
}

export interface VersionedItems<T> {
  dataset_version: string | number | null;
  items: T[];
}
export interface MetricsResponse extends VersionedItems<Metric> {
  dictionary_version: string;
}
export interface ObservationResponse extends VersionedItems<Observation> {
  next_cursor: string | null;
}
export interface DatasetResponse {
  items: Dataset[];
  latest: string | number | null;
}
export interface ComparisonResponse {
  dataset_version: string | number;
  metric_id: string;
  caveats?: string[];
  rows: Observation[];
  excluded: {
    observation_id?: string;
    entity_ref: string;
    reasons: string[];
  }[];
  contract_version: string;
  equivalent_alternatives?: {
    entity_ref: string;
    entity_name: string;
    selected_observation_id: string;
    alternative_observation_id: string;
    alternative_value_origin: "derived";
    reason: string;
  }[];
  available_cohorts?: {
    dimensions: Record<string, string | number | null>;
    observation_count: number;
    entity_refs: string[];
  }[];
}

export interface PublicSource {
  source_id: string;
  report_title: string;
  report_year?: number | string | null;
  publisher?: string | null;
  entity_refs: string[];
  observation_count: number;
  public_url?: string | null;
  is_demo?: boolean;
}
export type View =
  | "landing"
  | "overview"
  | "entities"
  | "entity"
  | "metrics"
  | "metric"
  | "compare"
  | "sources"
  | "methods"
  | "api";
