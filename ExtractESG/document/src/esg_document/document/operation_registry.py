from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationSpec:
    name: str
    target_types: frozenset[str]
    risk_level: str
    payload_kind: str
    prompt_instruction: str
    guard_handler: str
    applier_handler: str

    @property
    def payload_contract(self) -> dict:
        from esg_document.document.operation_payloads import payload_schema

        return payload_schema(self.name)


def _spec(
    name: str,
    targets: str,
    risk: str,
    payload: str,
    prompt: str,
) -> OperationSpec:
    return OperationSpec(
        name=name,
        target_types=frozenset(targets.split()),
        risk_level=risk,
        payload_kind=payload,
        prompt_instruction=prompt,
        guard_handler=f"guard:{name}",
        applier_handler=f"apply:{name}",
    )


OPERATION_SPECS = (
    _spec(
        "confirm",
        "page spread block table figure cell section",
        "low",
        "null or concise evidence object",
        "Confirm only when the scoped candidate already matches the visible evidence.",
    ),
    _spec(
        "confirm_spread",
        "spread",
        "low",
        "{reading_direction}",
        "Confirm only an information-bearing horizontal composition. Decorative continuity alone is not a semantic spread; use reject_spread unless a text-bearing entity or reading unit crosses the seam.",
    ),
    _spec(
        "reject_spread",
        "spread",
        "low",
        "{reason?}",
        "Reject when pages are independent or their only cross-seam continuity is decorative and no information-bearing entity continues.",
    ),
    _spec(
        "link_horizontal_continuation",
        "spread",
        "low",
        "HorizontalLinks",
        "Link only objects that visibly continue across the confirmed seam.",
    ),
    _spec(
        "replace_block_text",
        "block",
        "medium",
        "string or {text}",
        "Replace block text only from readable visual evidence.",
    ),
    _spec(
        "replace_cell_text",
        "cell",
        "medium",
        "string or {text}",
        "Replace one cell without changing unrelated cells.",
    ),
    _spec(
        "correct_ocr_text",
        "block cell",
        "medium",
        "{text}",
        "Correct a localized OCR error and retain source evidence.",
    ),
    _spec(
        "set_table_grid",
        "table",
        "high",
        "TableGridRepairProposal",
        "Return a complete final grid with source-cell mappings, missing-text accounting, and visual evidence.",
    ),
    _spec(
        "insert_table_row",
        "table",
        "medium",
        "InsertRow",
        "Insert only a visually present missing row.",
    ),
    _spec(
        "set_bbox",
        "block table figure cell",
        "medium",
        "BoundingBox",
        "Set a bounding box in the target coordinate system.",
    ),
    _spec(
        "set_printed_page_label",
        "page",
        "low",
        "string",
        "Set only the page label visibly printed on the page.",
    ),
    _spec(
        "set_visual_type",
        "figure",
        "low",
        "visual type string",
        "Classify the visible object without inventing semantics.",
    ),
    _spec(
        "set_caption",
        "table figure",
        "low",
        "string",
        "Use only a caption visibly associated with the target.",
    ),
    _spec(
        "link_continuation",
        "table",
        "medium",
        "Continuation",
        "Link only a visible continuation.",
    ),
    _spec(
        "merge_blocks",
        "block",
        "high",
        "MergeBlocks",
        "Merge blocks only when they are one visible reading unit.",
    ),
    _spec(
        "split_block",
        "block",
        "high",
        "SplitBlock",
        "Split a block only at visible semantic boundaries.",
    ),
    _spec(
        "add_quality_flags",
        "page spread block table figure cell section",
        "low",
        "list[string]",
        "Add diagnostic flags; do not use them as a substitute for a repair.",
    ),
    _spec(
        "retire_table_candidate",
        "table",
        "medium",
        "{disposition: ordinary_text|duplicate_fragment|non_table_visual|decoration}",
        "Retire only a false-positive table candidate; ordinary_text keeps the source block visible.",
    ),
    _spec(
        "add_visual_text_block",
        "page",
        "medium",
        "{text,bbox,visual_role?}",
        "Add only visible text absent from existing blocks and cells.",
    ),
    _spec(
        "set_figure_legend_text",
        "figure",
        "medium",
        "list[string]",
        "Transcribe visible legend labels exactly.",
    ),
    _spec(
        "upsert_figure_structure",
        "figure",
        "high",
        "figure structure object",
        "Model a non-chart visual using bounded visible nodes and relations.",
    ),
    _spec(
        "upsert_chart_spec",
        "figure",
        "high",
        "{chart_type,title?,categories?,series:[{name,unit?,points:[{category,value,display_value?,evidence_refs?}]}],visual_evidence_refs}",
        "If chart_spec is null and visible values exist, do not confirm unchanged. Return canonical ChartSpec using series[].points; never use type, x_categories, data, or values aliases.",
    ),
    _spec(
        "bind_block_to_figure",
        "block",
        "medium",
        "{figure_id,visual_role?}",
        "Bind an existing block instead of duplicating its text.",
    ),
    # Compatibility operations are readable for old revisions but are never planned for new work.
    _spec(
        "replace_text",
        "page block cell",
        "high",
        "legacy",
        "Legacy compatibility operation.",
    ),
    _spec(
        "replace_table", "table", "high", "legacy", "Legacy compatibility operation."
    ),
)

PATCH_OPERATION_NAMES = tuple(spec.name for spec in OPERATION_SPECS)


class OperationRegistry:
    """Runtime source of truth for review operations and their contracts."""

    _by_name = {spec.name: spec for spec in OPERATION_SPECS}

    REVIEW_KIND_OPERATIONS = {
        "horizontal_page_spread": (
            "confirm_spread",
            "reject_spread",
            "link_horizontal_continuation",
            "confirm",
            "replace_block_text",
            "replace_cell_text",
            "correct_ocr_text",
            "set_table_grid",
            "insert_table_row",
            "add_visual_text_block",
            "bind_block_to_figure",
            "set_figure_legend_text",
            "set_visual_type",
            "set_bbox",
            "set_caption",
            "upsert_chart_spec",
            "upsert_figure_structure",
            "merge_blocks",
            "split_block",
            "add_quality_flags",
        ),
        "table_candidate_classification": (
            "confirm",
            "retire_table_candidate",
            "replace_cell_text",
            "set_table_grid",
            "insert_table_row",
            "set_bbox",
        ),
        "table_structure_reconstruction": (
            "confirm",
            "replace_cell_text",
            "set_table_grid",
            "insert_table_row",
            "retire_table_candidate",
            "set_bbox",
        ),
        "figure_binding": (
            "confirm",
            "set_bbox",
            "set_visual_type",
            "set_caption",
            "set_figure_legend_text",
            "bind_block_to_figure",
        ),
        "figure_semantic_structure": (
            "confirm",
            "set_visual_type",
            "set_caption",
            "set_figure_legend_text",
            "upsert_chart_spec",
            "upsert_figure_structure",
            "bind_block_to_figure",
            "set_bbox",
        ),
        "page_text_coverage": (
            "confirm",
            "replace_block_text",
            "correct_ocr_text",
            "set_bbox",
            "add_visual_text_block",
        ),
        "generic_document_ir_review": (
            "confirm",
            "replace_block_text",
            "set_bbox",
            "add_quality_flags",
        ),
    }

    @classmethod
    def get(cls, name: str) -> OperationSpec:
        try:
            return cls._by_name[name]
        except KeyError as exc:
            raise ValueError(f"Unknown review operation: {name}") from exc

    @classmethod
    def for_review_kind(cls, review_kind: str) -> list[str]:
        operations = cls.REVIEW_KIND_OPERATIONS.get(review_kind)
        if operations is None:
            raise ValueError(f"Unknown review kind: {review_kind}")
        return list(operations)

    @classmethod
    def risk_level(cls, name: str) -> str:
        return cls.get(name).risk_level

    @classmethod
    def supports_target(cls, name: str, target_type: str) -> bool:
        return target_type in cls.get(name).target_types

    @staticmethod
    def contract_version() -> str:
        from esg_document.document.operation_payloads import VERSION

        return VERSION

    @staticmethod
    def payload_schema(name: str) -> dict:
        from esg_document.document.operation_payloads import payload_schema

        return payload_schema(name)

    @staticmethod
    def resolve_payload(name: str, value, *, legacy_aliases: bool = True):
        from esg_document.document.operation_payloads import resolve_payload

        return resolve_payload(name, value, legacy_aliases=legacy_aliases)

    @classmethod
    def referenced_write_targets(cls, patch) -> set[str]:
        """Secondary existing entities mutated by this operation, excluding children.

        These identities must participate in scope checks, snapshots and verifier
        coverage. New objects and intrinsic children are owned by the main target.
        """
        try:
            payload = cls.resolve_payload(patch.operation, patch.proposed_value).value
        except (ValueError, TypeError, KeyError):
            return set()  # The typed payload guard owns malformed proposals.
        if not isinstance(payload, dict):
            return set()
        if patch.operation == "merge_blocks":
            return set(payload["merge_block_ids"])
        if patch.operation == "link_continuation":
            return {payload["continues_to_table_id"]}
        if patch.operation == "link_horizontal_continuation":
            return {
                link[key]
                for link in payload["links"]
                for key in ("source_id", "target_id")
            }
        if patch.operation in {"bind_block_to_figure", "add_visual_text_block"}:
            return {payload["figure_id"]} if payload.get("figure_id") else set()
        if patch.operation == "upsert_figure_structure":
            return {
                element["block_id"]
                for element in payload["elements"]
                if element.get("block_id")
            }
        return set()

    @classmethod
    def contract_digest(cls, names: list[str]) -> str:
        import hashlib, json

        return hashlib.sha256(
            json.dumps(
                cls.prompt_fragment(names),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @classmethod
    def patch_schema(cls, names: list[str]) -> dict:
        branches = []
        for name in names:
            schema = cls.payload_schema(name)

            # Each standalone payload schema retains its own definitions with local references rebased.
            def rebase(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if (
                            key == "$ref"
                            and isinstance(value, str)
                            and value.startswith("#/$defs/")
                        ):
                            node[key] = "#/$defs/" + name + "_" + value.split("/")[-1]
                        else:
                            rebase(value)
                elif isinstance(node, list):
                    for value in node:
                        rebase(value)

            rebase(schema)
            definitions = schema.pop("$defs", {})
            branches.append(
                {
                    "type": "object",
                    "properties": {
                        "operation": {"const": name},
                        "target_type": {"enum": sorted(cls.get(name).target_types)},
                        "target_id": {"type": "string", "minLength": 1},
                        "field_path": {"type": ["string", "null"]},
                        "before_value": {},
                        "proposed_value": schema,
                        "evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": ["string", "null"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "operation",
                        "target_type",
                        "target_id",
                        "proposed_value",
                    ],
                    "additionalProperties": False,
                    "_definitions": {name + "_" + k: v for k, v in definitions.items()},
                }
            )
        definitions = {
            k: v for branch in branches for k, v in branch.pop("_definitions").items()
        }
        return {"oneOf": branches, "$defs": definitions}

    @classmethod
    def prompt_fragment(cls, names: list[str]) -> list[dict[str, object]]:
        return [
            {
                "operation": spec.name,
                "target_types": sorted(spec.target_types),
                "payload_contract": cls.payload_schema(spec.name),
                "payload_contract_version": cls.contract_version(),
                "risk_level": spec.risk_level,
                "guard_handler": spec.guard_handler,
                "applier_handler": spec.applier_handler,
                "instruction": spec.prompt_instruction,
            }
            for spec in (cls.get(name) for name in names)
        ]
