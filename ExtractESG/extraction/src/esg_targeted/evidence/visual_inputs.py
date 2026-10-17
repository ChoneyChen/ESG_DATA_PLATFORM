from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from esg_targeted.contracts import EvidencePacket


class FocusedVisualInputBuilder:
    """Prepare one visual object for one semantic call.

    Complete table crops are preserved by default because stacked headers and
    cross-column relations are semantically important. Geometry-based focusing is
    retained only as an explicit oversized/recovery policy. Document IR remains
    immutable in both cases.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()

    def build_all(self, packets: list[EvidencePacket]) -> list[EvidencePacket]:
        return [self.build(packet) for packet in packets]

    def build(self, packet: EvidencePacket) -> EvidencePacket:
        try:
            context = json.loads(packet.model_context)
            region = context.get("region") or {}
            geometry = region.get("visual_focus")
            if (
                region.get("kind") == "table"
                and region.get("visual_policy") == "full_object"
                and len(packet.page_image_paths) == 1
            ):
                return self._annotate(packet, "preserved_full_object")
            if (
                region.get("kind") != "table"
                or not geometry
                or len(packet.page_image_paths) != 1
            ):
                return self._annotate(packet, "not_applicable")
            source = Path(packet.page_image_paths[0]).resolve()
            if not source.is_file():
                return self._annotate(packet, "source_missing")
            output = self.output_dir / f"{packet.packet_id}-focused.png"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            dimensions = self._render(source, output, geometry)
            if dimensions is None:
                return self._annotate(packet, "geometry_not_focusable")
            context["region"]["visual_focus"] = {
                **geometry,
                "status": "rendered",
                "source_image": str(source),
                "rendered_image": str(output),
                **dimensions,
            }
            model_context = json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return packet.model_copy(
                update={
                    "page_image_paths": [str(output)],
                    "model_context": model_context,
                    "budget": {
                        **packet.budget,
                        "actual_context_chars": len(model_context),
                        "focused_visual": context["region"]["visual_focus"],
                    },
                },
                deep=True,
            )
        except Exception as exc:
            return self._annotate(
                packet,
                "render_failed",
                detail=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _render(source: Path, output: Path, geometry: dict) -> dict | None:
        table_x0, table_x1 = map(float, geometry["table_x_range"])
        context_x0, context_x1 = map(float, geometry["context_x_range"])
        target_x0, target_x1 = map(float, geometry["target_x_range"])
        if table_x1 <= table_x0 or context_x1 <= context_x0 or target_x1 <= target_x0:
            return None
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        width, height = image.size

        def pixel_x(value: float) -> int:
            ratio = (value - table_x0) / (table_x1 - table_x0)
            return max(0, min(width, round(ratio * width)))

        padding = max(4, round(width * 0.008))
        context_box = (
            max(0, pixel_x(context_x0) - padding),
            0,
            min(width, pixel_x(context_x1) + padding),
            height,
        )
        target_box = (
            max(0, pixel_x(target_x0) - padding),
            0,
            min(width, pixel_x(target_x1) + padding),
            height,
        )
        if context_box[2] <= context_box[0] or target_box[2] <= target_box[0]:
            return None
        # For an adjacent first value column, a single contiguous crop is enough.
        if target_box[0] <= context_box[2] + padding:
            contiguous_box = (
                min(context_box[0], target_box[0]),
                0,
                max(context_box[2], target_box[2]),
                height,
            )
            focused = image.crop(contiguous_box)
        else:
            context_image = image.crop(context_box)
            target_image = image.crop(target_box)
            divider = max(8, round(width * 0.01))
            focused = Image.new(
                "RGB",
                (context_image.width + divider + target_image.width, height),
                "white",
            )
            focused.paste(context_image, (0, 0))
            focused.paste(target_image, (context_image.width + divider, 0))
            draw = ImageDraw.Draw(focused)
            line_x = context_image.width + divider // 2
            draw.line((line_x, 0, line_x, height), fill=(36, 108, 73), width=2)

        temporary = output.with_suffix(".tmp.png")
        focused.save(temporary, format="PNG", optimize=True)
        temporary.replace(output)
        return {
            "source_size": [width, height],
            "rendered_size": [focused.width, focused.height],
            "context_pixel_box": list(context_box),
            "target_pixel_box": list(target_box),
        }

    @staticmethod
    def _annotate(
        packet: EvidencePacket,
        status: str,
        *,
        detail: str | None = None,
    ) -> EvidencePacket:
        record = {"status": status}
        if detail:
            record["detail"] = detail
        return packet.model_copy(
            update={
                "budget": {
                    **packet.budget,
                    "focused_visual": record,
                }
            },
            deep=True,
        )
