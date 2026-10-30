"""Read durable production reservations before releasing source or child records."""

import json


def production_references(db, *, asset_id=None, source_sha256=None, processing_id=None):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_automations'"
    ).fetchone():
        return []
    matches = []
    statuses = {
        "planned",
        "active",
        "pausing",
        "paused",
        "waiting_credentials",
        "cancelling",
        "completed",
        "partial",
        "cancelled",
    }
    for identity, raw in db.execute(
        "SELECT automation_id,state_json FROM production_automations"
    ):
        try:
            state = json.loads(raw)
            if (
                not isinstance(state, dict)
                or state.get("schema_version") != "production-automation-v1"
                or state.get("automation_id") != identity
                or state.get("status") not in statuses
                or not isinstance(state.get("items"), list)
            ):
                raise ValueError("invalid production reservation")
            if state["status"] in {"completed", "partial", "cancelled"}:
                continue
            for item in state["items"]:
                if (
                    not isinstance(item, dict)
                    or not item.get("asset_id")
                    or not item.get("source_sha256")
                ):
                    raise ValueError("invalid production item")
                if (
                    (asset_id and item["asset_id"] == asset_id)
                    or (source_sha256 and item["source_sha256"] == source_sha256)
                    or (
                        processing_id
                        and item.get("processing_id") == processing_id
                        and item.get("status")
                        not in {"completed", "partial", "cancelled"}
                    )
                ):
                    matches.append(
                        {
                            "automation_id": identity,
                            "item_id": item.get("item_id"),
                            "status": state["status"],
                        }
                    )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "自动生产来源引用无法核实；恢复计划记录后再清理。"
            ) from exc
    return matches
