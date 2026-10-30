"""Line protocol shared by isolated producers and the queue consumer."""

import json

EVENT_PREFIX = "ESG_PIPELINE_EVENT "


def encode_event(kind: str, **payload) -> str:
    return EVENT_PREFIX + json.dumps(
        {"kind": kind, **payload}, ensure_ascii=False, separators=(",", ":")
    )
