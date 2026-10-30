"""Canonical review-operation codecs. Historical aliases are boundary conversions only."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    StrictStr,
    model_validator,
)

VERSION = "review-operation-payload-v2"
Text = Annotated[StrictStr, Field(min_length=1)]
IdList = Annotated[list[Text], Field(min_length=1)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SpreadConfirmation(Payload):
    reading_direction: Literal["left_to_right", "right_to_left"] = "left_to_right"


class SpreadRejection(SpreadConfirmation):
    reason: str | None = None


class InsertCell(Payload):
    col_index: int = Field(ge=0)
    text: str = ""
    row_span: Literal[1] = 1
    col_span: int = Field(default=1, ge=1)
    is_header: bool = False
    unit_hint: str | None = None
    bbox: Any = None


class InsertRow(Payload):
    insert_before_row_index: int = Field(ge=0)
    cells: list[InsertCell] = Field(min_length=1)


class Continuation(Payload):
    continues_to_table_id: Text
    continuation_group_id: str | None = None
    axis: Literal["vertical", "horizontal"] = "vertical"


class HorizontalLink(Payload):
    source_id: Text
    target_id: Text
    composition_mode: Literal[
        "horizontal_append_columns", "horizontal_continue_last_column"
    ] = "horizontal_append_columns"
    confidence: float = Field(default=1, ge=0, le=1)


class HorizontalLinks(Payload):
    links: list[HorizontalLink] = Field(min_length=1)


class MergeBlocks(Payload):
    merge_block_ids: IdList
    text: str | None = None

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.merge_block_ids)) != len(self.merge_block_ids):
            raise ValueError("merge_block_ids must be unique")
        return self


class SplitPart(Payload):
    text: Text
    bbox: Any = None


class SplitBlock(Payload):
    parts: list[SplitPart] = Field(min_length=2)


class Retirement(Payload):
    disposition: Literal[
        "ordinary_text", "duplicate_fragment", "non_table_visual", "decoration"
    ]


class VisualText(Payload):
    text: Text
    bbox: Any
    visual_role: (
        Literal["node_title", "node_body", "label", "legend", "value", "annotation"]
        | None
    ) = None
    block_type: Literal["heading", "paragraph", "caption", "unknown"] = "paragraph"
    figure_id: str | None = None


class FigureBinding(Payload):
    figure_id: Text
    visual_role: (
        Literal["node_title", "node_body", "label", "legend", "value", "annotation"]
        | None
    ) = None
    relation: Literal["element", "caption", "legend"] = "element"


class FigureElement(Payload):
    element_id: Text
    text: Text
    visual_role: Literal[
        "node_title", "node_body", "label", "legend", "value", "annotation"
    ]
    block_type: Literal["unknown", "paragraph", "caption"] = "unknown"
    block_id: str | None = None
    bbox: Any


class FigureRelation(Payload):
    source_element_id: Text
    target_element_id: Text
    relation: Literal["visual_parent_of", "visual_connected_to", "visual_flow_to"]
    confidence: float = Field(default=0.8, ge=0, le=1)


class FigureStructure(Payload):
    elements: list[FigureElement] = Field(min_length=1)
    relations: list[FigureRelation] = Field(default_factory=list)


@dataclass(frozen=True)
class PayloadResolution:
    value: Any
    compatibility_codes: tuple[str, ...] = ()


def payload_type(operation: str):
    # Delayed imports avoid the contracts -> registry -> codecs dependency cycle.
    from esg_document.document.contracts import (
        BoundingBox,
        ChartSpec,
        TableGridRepairProposal,
    )

    return {
        "confirm": dict[str, Any] | None,
        "confirm_spread": SpreadConfirmation,
        "reject_spread": SpreadRejection,
        "link_horizontal_continuation": HorizontalLinks,
        "replace_block_text": Text,
        "replace_cell_text": Text,
        "correct_ocr_text": Text,
        "set_table_grid": TableGridRepairProposal,
        "replace_table": TableGridRepairProposal,
        "insert_table_row": InsertRow,
        "set_bbox": BoundingBox,
        "set_printed_page_label": Text,
        "set_visual_type": Literal[
            "unknown",
            "chart",
            "diagram",
            "illustration",
            "photo",
            "icon",
            "decoration",
            "composite",
        ],
        "set_caption": Text,
        "link_continuation": Continuation,
        "merge_blocks": MergeBlocks,
        "split_block": SplitBlock,
        "add_quality_flags": list[Text],
        "retire_table_candidate": Retirement,
        "add_visual_text_block": VisualText,
        "set_figure_legend_text": list[Text],
        "upsert_figure_structure": FigureStructure,
        "upsert_chart_spec": ChartSpec,
        "bind_block_to_figure": FigureBinding,
        "replace_text": Text,
    }[operation]


def payload_schema(operation: str) -> dict[str, Any]:
    schema = TypeAdapter(payload_type(operation)).json_schema()
    # Nested geometry uses the actual shared BoundingBox contract.
    from esg_document.document.contracts import BoundingBox

    if operation in {
        "insert_table_row",
        "split_block",
        "add_visual_text_block",
        "upsert_figure_structure",
    }:
        bbox_schema = BoundingBox.model_json_schema()

        def replace(node):
            if isinstance(node, dict):
                if "properties" in node and "bbox" in node["properties"]:
                    node["properties"]["bbox"] = (
                        bbox_schema
                        if "bbox" in node.get("required", [])
                        else {"anyOf": [bbox_schema, {"type": "null"}]}
                    )
                for value in list(node.values()):
                    replace(value)
            elif isinstance(node, list):
                for value in node:
                    replace(value)

        replace(schema)

    def compact(node):
        if isinstance(node, dict):
            node.pop("title", None)
            for child in node.values():
                compact(child)
        elif isinstance(node, list):
            for child in node:
                compact(child)

    compact(schema)
    if operation == "upsert_chart_spec":
        schema.get("properties", {}).pop("evidence_support", None)
    return schema


def resolve_payload(
    operation: str, value: Any, *, legacy_aliases: bool = True
) -> PayloadResolution:
    codes = []
    if isinstance(value, dict):
        value = dict(value)
        aliases = {
            "insert_table_row": {"row_index": "insert_before_row_index"},
            "merge_blocks": {"block_ids": "merge_block_ids"},
            "split_block": {"segments": "parts"},
            "link_continuation": {
                "target_id": "continues_to_table_id",
                "target_table_id": "continues_to_table_id",
            },
        }
        if legacy_aliases:
            for old, new in aliases.get(operation, {}).items():
                if old in value:
                    if new in value and value[new] != value[old]:
                        raise ValueError(
                            f"Conflicting operation fields {old} and {new}"
                        )
                    value[new] = value.pop(old)
                    codes.append(f"operation_payload_alias:{operation}:{old}->{new}")
            if operation in {
                "replace_block_text",
                "replace_cell_text",
                "correct_ocr_text",
                "replace_text",
            }:
                if set(value) != {"text"}:
                    raise ValueError("Text replacement object must contain only text")
                value = value["text"]
                codes.append(f"operation_payload_alias:{operation}:text_object->string")
    if value is None and operation in {"confirm_spread", "reject_spread"}:
        value = {}
    model = TypeAdapter(payload_type(operation)).validate_python(value, strict=True)
    if isinstance(model, BaseModel):
        canonical = model.model_dump(mode="json", exclude_unset=True)
    else:
        canonical = model
    # Typed bbox validation for embedded locations without synthesizing geometry.
    if isinstance(canonical, dict):
        from esg_document.document.contracts import BoundingBox

        for collection in ["cells", "parts", "elements"]:
            for item in canonical.get(collection, []):
                if collection == "elements" and item.get("bbox") is None:
                    raise ValueError(
                        "Visible figure elements require a real source bbox"
                    )
                if item.get("bbox") is not None:
                    item["bbox"] = BoundingBox.model_validate(
                        item["bbox"], strict=True
                    ).model_dump(mode="json")
        if operation in {"add_visual_text_block"} and canonical.get("bbox") is None:
            raise ValueError("Visible text requires a real source bbox")
        if canonical.get("bbox") is not None:
            canonical["bbox"] = BoundingBox.model_validate(
                canonical["bbox"], strict=True
            ).model_dump(mode="json")
    json.dumps(canonical, allow_nan=False)
    return PayloadResolution(canonical, tuple(codes))
