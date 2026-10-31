"""Database-owned observation and publication ports. All periods remain addressable."""
from typing import Annotated, Literal
from pydantic import Field, JsonValue, model_validator
from esg_contracts.facts import QuantitativePayload, QualitativePayload, ReportedContext, EvidenceLocation, ClauseMapping
from esg_data.periods import ReportedPeriod
from .contracts import Model, PeriodFacet
from esg_contracts.exports import IngestionReceipt


class ComparisonStatus(Model):
    eligible: bool
    reasons: list[str]


class ObservationSource(Model):
    document_id: str
    pdf_sha256: str | None = None
    pdf_id: str | None = None
    title: str | None = None
    report_company: str | None = None
    publication_year: int | None = None
    resource_revision: int | None = None
    report_company_id: str | None = None
    industry: str | None = None
    reporting_year: int | None = None


class Observation(Model):
    schema_version: Literal["capture-observation-1.0"] = "capture-observation-1.0"
    observation_id: str
    source_fact_id: str
    occurrence_id: str
    clause_key: str | None
    clause_digest: str | None
    title: str
    relation: Literal["match", "uncertain", "different"]
    mapping: ClauseMapping | None
    period: ReportedPeriod
    source_row_label: str | None
    value: Annotated[QuantitativePayload | QualitativePayload, Field(discriminator="kind")]
    identity: ReportedContext
    evidence: list[EvidenceLocation]
    quality: Literal["grounded", "needs_confirmation"]
    flags: list[str]
    extensions: dict[str, JsonValue]
    source: ObservationSource
    comparison: ComparisonStatus
    comparison_key: str | None


class ProjectionSummary(Model):
    schema_version: Literal["capture-projection-1.0"] = "capture-projection-1.0"
    projection_id: str
    export_id: str
    input_digest: str
    transform_version: str
    content_digest: str
    source: ObservationSource
    facts: int = Field(ge=0)
    observations: int = Field(ge=0)
    periods: dict[str, int]
    relations: dict[str, int]
    repeated_occurrences: int = Field(ge=0)
    diagnostics: list[str]


class ProjectionStatus(Model):
    export_id: str
    status: Literal["pending", "completed", "failed", "schema_unavailable"]
    projection: ProjectionSummary | None = None
    error_code: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def completed_projection(self):
        if (self.status == "completed") != (self.projection is not None):
            raise ValueError("Only a completed projection carries its immutable result")
        return self


class SourceSelectionPolicy(Model):
    schema_version: Literal["source-selection-1.0"] = "source-selection-1.0"
    mode: Literal["all_sources", "latest_report", "prefer_explicit_restated"] = "all_sources"
    relations: list[Literal["match", "uncertain", "different"]] = Field(default_factory=lambda: ["match"], min_length=1)


class DatasetRequest(Model):
    title: str = Field(min_length=1, max_length=200)
    projection_ids: list[str] = Field(min_length=1, max_length=1000)
    policy: SourceSelectionPolicy = Field(default_factory=SourceSelectionPolicy)


class DatasetDecision(Model):
    observation_id: str
    selected: bool
    reason: str


class DatasetPreview(Model):
    schema_version: Literal["capture-dataset-preview-1.0"] = "capture-dataset-preview-1.0"
    preview_digest: str
    title: str
    projection_ids: list[str]
    projection_digests: dict[str, str]
    policy: SourceSelectionPolicy
    total: int = Field(ge=0)
    selected: int = Field(ge=0)
    periods: dict[str, int]
    decisions: list[DatasetDecision]
    restatement_link_ids: list[str]


class PublishRequest(DatasetRequest):
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=1000)


class PreviewQuery(DatasetRequest):
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class WithdrawalRequest(Model):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class RestatementRequest(Model):
    newer_observation_id: str
    older_observation_id: str
    reason: str = Field(min_length=1, max_length=1000)


class DatasetVersion(Model):
    schema_version: Literal["capture-dataset-version-1.0"] = "capture-dataset-version-1.0"
    dataset_version_id: str
    title: str
    content_digest: str
    policy: SourceSelectionPolicy
    state: Literal["published", "withdrawn"]
    revision: int
    published_at: str
    observations: int
    periods: dict[str, int]


class ProductPage(Model):
    query_digest: str
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    next_cursor: str | None = None

    @model_validator(mode="after")
    def counts(self):
        if self.returned != len(self.items) or self.returned > self.total:
            raise ValueError("Product page counts disagree")
        return self


class ObservationPage(ProductPage):
    schema_version: Literal["capture-observation-page-1.0"] = "capture-observation-page-1.0"
    snapshot_id: str
    items: list[Observation]


class DatasetPage(ProductPage):
    schema_version: Literal["capture-dataset-page-1.0"] = "capture-dataset-page-1.0"
    items: list[DatasetVersion]


class ProjectionPage(ProductPage):
    schema_version: Literal["capture-projection-page-1.0"] = "capture-projection-page-1.0"
    items: list[ProjectionSummary]


class ProductDefinition(Model):
    clause_key: str
    clause_digest: str
    title: str
    contract: dict[str, JsonValue]


class CaptureProductState(Model):
    schema_version: Literal["capture-product-state-1.0"] = "capture-product-state-1.0"
    export_id: str
    receipt: IngestionReceipt
    captured_facts: int = Field(ge=0)
    capture_periods: list[PeriodFacet]
    projection: ProjectionStatus
    datasets: DatasetPage | None = None
    published_dataset_count: int | None = None
    auto_publish: Literal[False] = False

    @model_validator(mode="after")
    def reception_counts(self):
        if self.captured_facts != self.receipt.accepted_facts or sum(item.facts for item in self.capture_periods) != self.captured_facts:
            raise ValueError("Capture counts must agree with the receipt and period facets")
        return self
