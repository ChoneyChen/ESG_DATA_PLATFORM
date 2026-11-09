"""Model proposals name evidence; they cannot supply numerical values."""

from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, JsonValue, BeforeValidator, model_validator


class DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityProposal(DecisionModel):
    axis: str
    state: Literal["stated", "not_stated", "conflict"] = "stated"
    value: JsonValue = None
    quote: str | None = None
    source_id: str | None = None


def identity_claims(value):
    # The wire schema teaches the model a precise shape; malformed optional claims
    # become explicit uncertainty without rejecting correctly referenced raw values.
    if not isinstance(value, list):
        return [IdentityProposal(axis="_unparsed_identity", state="conflict")]
    result = []
    for item in value:
        try:
            result.append(IdentityProposal.model_validate(item))
        except ValueError:
            result.append(IdentityProposal(axis="_unparsed_identity", state="conflict"))
    return result


IdentityClaims = Annotated[list[IdentityProposal], BeforeValidator(identity_claims)]


class NumericMatch(DecisionModel):
    row_id: str
    columns: list[str] = Field(min_length=1)
    match: Literal["match", "different", "uncertain"] = "match"
    reason: str = "Reported row selected for the clause"
    selection: Literal["measurement", "nonmeasurement", "unresolved"] = "measurement"
    identity: IdentityClaims = Field(default_factory=list)
    column_identity: dict[str, IdentityClaims] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_measurement_relation(self):
        if self.match == "different" and self.selection != "measurement":
            raise ValueError("A different-target measurement is still a measurement")
        return self


class QuantitativeDecision(DecisionModel):
    identity: IdentityClaims = Field(default_factory=list)
    matches: list[NumericMatch]


class StatementMatch(DecisionModel):
    span_id: str
    quote: str | None = None
    match: Literal["match", "uncertain"] = "match"
    reason: str = "Reported statement selected for the clause"
    identity: IdentityClaims = Field(default_factory=list)


class QualitativeDecision(DecisionModel):
    identity: IdentityClaims = Field(default_factory=list)
    matches: list[StatementMatch]


def evidence_identity_schema(schema, source_ids):
    """An optional proposal contains an actual observation; absence is omission."""
    proposal = schema["$defs"]["IdentityProposal"]
    properties = proposal["properties"]
    properties.pop("state", None)
    properties["value"] = {"anyOf": [
        {"type":"string","minLength":1}, {"type":"number"}, {"type":"boolean"},
        {"type":"array","items":{"$ref":"#/$defs/JsonValue"}},
        {"type":"object","additionalProperties":{"$ref":"#/$defs/JsonValue"}},
    ]}
    properties["quote"] = {"type":"string", "minLength":1}
    properties["source_id"] = {"type":"string", "enum":source_ids}
    proposal["required"] = ["axis", "value", "quote", "source_id"]


def decision_schema(data_kind, frame):
    """Constrain references to actual evidence IDs, never to target/resource metadata."""
    if data_kind == "quantitative":
        # Numerical reading selects physical cells. The shared context reader
        # supplies optional attributes afterwards, once for the captured facts.
        return {
            "type": "object", "additionalProperties": False,
            "properties": {"matches": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "row_id": {"type": "string", "enum": [row.row_id for row in frame.rows]},
                    "columns": {"type": "array", "minItems": 1, "items": {
                        "type": "string", "enum": sorted({cell.column_id for row in frame.rows for cell in row.cells})}},
                    "match": {"type": "string", "enum": ["match", "different", "uncertain"]},
                }, "required": ["row_id", "columns"],
            }}}, "required": ["matches"],
        }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"matches": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "span_id": {"type":"string", "enum":[item.span_id or item.object_id for item in frame.texts]},
                "quote": {"type":"string", "minLength":1},
                "match": {"type":"string", "enum":["match", "uncertain"]},
            }, "required":["span_id"],
        }}}, "required":["matches"],
    }
