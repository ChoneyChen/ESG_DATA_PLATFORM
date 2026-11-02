"""Model proposals name evidence; they cannot supply numerical values."""

from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, JsonValue, BeforeValidator, model_validator
from copy import deepcopy
from .reading_frame import identity_source_ids


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
    reason: str
    selection: Literal["measurement", "nonmeasurement", "unresolved"]
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
    reason: str
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
    model = QuantitativeDecision if data_kind == "quantitative" else QualitativeDecision
    schema = model.model_json_schema()
    evidence_identity_schema(schema, identity_source_ids(frame))
    if data_kind == "quantitative":
        properties = schema["$defs"]["NumericMatch"]["properties"]
        properties["row_id"] = {
            "type": "string",
            "enum": [row.row_id for row in frame.rows],
        }
        properties["columns"]["items"] = {
            "type": "string",
            "enum": sorted(
                {cell.column_id for row in frame.rows for cell in row.cells}
            ),
        }
        properties["column_identity"]["propertyNames"] = properties["columns"]["items"]
        # Enforce the semantic combination during constrained decoding, not by
        # retrying an already generated contradictory table result.
        measurement = deepcopy(schema["$defs"]["NumericMatch"])
        measurement["properties"]["selection"] = {"const": "measurement", "type": "string"}
        other = deepcopy(schema["$defs"]["NumericMatch"])
        other["properties"]["selection"] = {"enum": ["nonmeasurement", "unresolved"], "type": "string"}
        other["properties"]["match"] = {"enum": ["match", "uncertain"], "type": "string"}
        schema["$defs"]["NumericMatch"] = {"anyOf": [measurement, other]}
    else:
        schema["$defs"]["StatementMatch"]["properties"]["span_id"] = {
            "type": "string",
            "enum": [item.span_id or item.object_id for item in frame.texts],
        }
    return schema
