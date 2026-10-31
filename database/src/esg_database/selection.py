"""Versioned, explicit source selection. Unknown identity always keeps its sources."""
from collections import Counter, defaultdict
import base64
import json
from esg_data.hashing import digest
from esg_data.periods import period_label
from .products_contracts import DatasetRequest, DatasetPreview, DatasetDecision, Observation, ObservationPage
from .projection import period_coordinate, period_facet


def compile_preview(request: DatasetRequest, source):
    rows = [Observation.model_validate(row) for row in source["observations"]]
    decisions = {row.observation_id: DatasetDecision(observation_id=row.observation_id,
        selected=row.relation in request.policy.relations,
        reason="all_sources" if row.relation in request.policy.relations else "relation_not_selected") for row in rows}
    candidates = [row for row in rows if decisions[row.observation_id].selected]
    if request.policy.mode == "latest_report":
        groups = defaultdict(list)
        for row in candidates:
            if not row.comparison_key:
                decisions[row.observation_id] = decisions[row.observation_id].model_copy(update={"reason": "identity_incomplete_keep_source"})
            else:
                groups[row.comparison_key].append(row)
        for group in groups.values():
            if any(row.source.publication_year is None for row in group):
                for row in group:
                    decisions[row.observation_id] = decisions[row.observation_id].model_copy(update={"reason": "publication_year_unknown_keep_source"})
                continue
            latest = max(row.source.publication_year for row in group)
            tied = sum(row.source.publication_year == latest for row in group) > 1
            for row in group:
                selected = row.source.publication_year == latest
                decisions[row.observation_id] = DatasetDecision(observation_id=row.observation_id, selected=selected,
                    reason=("latest_report_tie_keep_sources" if tied else "latest_report") if selected else "earlier_report_retained_in_database")
    elif request.policy.mode == "prefer_explicit_restated":
        present = {row.observation_id for row in candidates}
        for link in source["links"]:
            if {link["newer_observation_id"], link["older_observation_id"]} <= present:
                old = link["older_observation_id"]
                decisions[old] = DatasetDecision(observation_id=old, selected=False, reason="explicitly_restated:" + link["link_id"])
    selected = [row for row in rows if decisions[row.observation_id].selected]
    title = request.title.strip()
    if not title:
        raise ValueError("Dataset title cannot be blank")
    body = {"schema_version": "capture-dataset-preview-1.0", "title": title,
        "projection_ids": sorted(row["projection_id"] for row in source["projections"]),
        "projection_digests": {row["projection_id"]: row["content_digest"] for row in source["projections"]},
        "policy": request.policy.model_dump(mode="json"), "total": len(rows), "selected": len(selected),
        "periods": dict(sorted(Counter(period_facet(row.period) for row in selected).items())),
        "decisions": [decisions[key].model_dump(mode="json") for key in sorted(decisions)],
        "restatement_link_ids": sorted(row["link_id"] for row in source["links"])}
    return DatasetPreview(**body, preview_digest=digest(body))


def preview_dataset(bridge, request):
    return compile_preview(request, bridge.call("product_input", {"projection_ids": request.projection_ids}))


def preview_observations(bridge,request,limit=200,cursor=None):
    source=bridge.call("product_input",{"projection_ids":request.projection_ids})
    preview=compile_preview(request,source)
    if preview.preview_digest!=request.preview_digest:
        raise ValueError("Dataset preview changed; review the current selection")
    query=digest([preview.preview_digest,limit]);after=""
    if cursor:
        try:
            token=json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
            if token["query"]!=query or not isinstance(token["after"],str):raise ValueError()
            after=token["after"]
        except (ValueError,TypeError,KeyError) as exc:raise ValueError("Cursor belongs to another preview query") from exc
    selected={row.observation_id for row in preview.decisions if row.selected}
    available=sorted((row for row in source["observations"] if row["observation_id"] in selected and row["observation_id"]>after),key=lambda row:row["observation_id"])
    page=available[:limit];more=len(available)>limit
    token=base64.urlsafe_b64encode(json.dumps({"query":query,"after":page[-1]["observation_id"]}).encode()).decode().rstrip("=") if more else None
    return ObservationPage(snapshot_id="preview-"+preview.preview_digest,query_digest=query,total=preview.selected,returned=len(page),items=page,next_cursor=token)


def publish_dataset(bridge, request, actor):
    preview = preview_dataset(bridge, request)
    if preview.preview_digest != request.preview_digest:
        raise ValueError("Dataset preview changed; review the current selection before publishing")
    if not preview.selected:
        raise ValueError("The selection contains no observations")
    return bridge.call("publish_dataset", {"preview": preview.model_dump(mode="json"), "actor": actor, "reason": request.reason})


def validate_restatement(new: Observation, old: Observation):
    if new.observation_id == old.observation_id or not new.clause_digest or new.clause_digest != old.clause_digest:
        raise ValueError("A restatement must refer to distinct observations of the same frozen definition")
    if new.period.kind == "unknown" or old.period.kind == "unknown" or period_coordinate(new.period) != period_coordinate(old.period):
        raise ValueError("A restatement must identify the same known measurement period")
    new_identity, old_identity = new.identity.model_dump(), old.identity.model_dump()
    for identity in (new_identity, old_identity):
        identity.update(identity.pop("dimensions"))
    for axis in set(new_identity) & set(old_identity) - {"reporting_period", "value_role"}:
        a, b = new_identity[axis], old_identity[axis]
        if a["state"] in {"stated", "inherited"} and b["state"] in {"stated", "inherited"} and a["value"] != b["value"]:
            raise ValueError("Conflicting known identity cannot be a replacement: " + axis)


def record_restatement(bridge, request, actor):
    rows = bridge.call("restatement_input", request.model_dump())
    new, old = (Observation.model_validate(rows[key]) for key in ("newer", "older"))
    validate_restatement(new, old)
    return bridge.call("record_restatement", {**request.model_dump(), "actor": actor, "expected_records": rows})
