"""Cross-source consolidation of foundation observations into 16 conclusions."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from esg_targeted.foundation.catalog import METRICS
from esg_targeted.foundation.quantities import agreement, comparable

NOT_FOUND_STATUSES = ("not_found_in_selected_sources", "only_rejected_or_other_period")
# When nothing qualifies, one near miss is offered for review instead of
# "not found": a Scope 3 figure the report limits to named categories, or a
# renewable purchase/generation figure printed without a consumption total.
PROMOTIONS = {
    "E1-6_11": ("label_is_scope3_category", "scope3_only_named_categories_reported"),
    "E1-5_05": ("renewable_source_component", "renewable_purchase_or_generation_reported"),
}


def _scope2_unstated(obs: dict) -> bool:
    return any(flag in obs["flags"] for flag in ("scope2_method_unstated", "scope2_method_not_stated_in_source"))


def _cluster(items: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for obs in items:
        for cluster in clusters:
            if agreement(cluster[0], obs):
                cluster.append(obs)
                break
        else:
            clusters.append([obs])

    def rank(cluster):
        accepted = sum(o["status"] == "accepted" for o in cluster)
        labelled = sum(o.get("period_year") is not None for o in cluster)
        return (-accepted, -len(cluster), -labelled, min(o["page_number"] for o in cluster))

    return sorted(clusters, key=rank)


def _split_components(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Values that add up to another candidate are its breakdown, not rival totals."""
    scaled = [(o, comparable(o.get("value"), o.get("unit_raw"))) for o in items]
    scaled = [(o, q) for o, q in scaled if q is not None and q[1] > 0]
    components: list[dict] = []
    for total, (family, amount) in sorted(scaled, key=lambda item: -item[1][1]):
        if total in components:
            continue
        parts: dict[Decimal, dict] = {}
        for other, (other_family, other_amount) in scaled:
            if other is not total and other not in components and other_family == family and other_amount < amount:
                parts.setdefault(other_amount, other)
        values = sorted(parts)[-12:]
        for size in range(2, min(4, len(values)) + 1):
            match = next((combo for combo in combinations(values, size)
                          if abs(sum(combo) - amount) <= amount * Decimal("0.002")), None)
            if match:
                chosen = {parts[v]["value"] for v in match}
                components += [o for o, (f, a) in scaled if f == family and o["value"] in chosen and a in match]
                break
    return [o for o in items if o not in components], components


def _promote(code: str, candidates: list[dict], reporting_year: int | None) -> list[dict]:
    rule = PROMOTIONS.get(code)
    if rule is None:
        return []
    reason, flag = rule
    near = [o for o in candidates if o.get("rejected_by") == reason and o.get("value") is not None
            and (reporting_year is None or o.get("period_year") in (reporting_year, None))]
    pool, _ = _split_components(near)
    if not pool or len(_cluster(pool)) != 1:
        return []
    return [{**o, "status": "review", "flags": [*o["flags"], flag]} for o in pool]


def brief(obs: dict) -> dict:
    return {key: obs.get(key) for key in ("status", "value_raw", "unit_raw", "period", "table_id", "page_number",
                                           "row_label", "cell_id", "column", "note_block_id", "flags",
                                           "rejected_by", "attributes")}


def consolidate(records: list[dict], reporting_year: int | None,
                metrics: tuple = METRICS) -> list[dict]:
    by_code: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        for obs in record.get("observations") or []:
            by_code[obs["code"]].append(obs)
    # A Scope 2 row without a stated method elsewhere carries the same quantity
    # that another source states as location- or market-based: it is that row.
    for code, other in (("E1-6_10", "E1-6_09"), ("E1-6_09", "E1-6_10")):
        stated = [o for o in by_code.get(other, []) if o["status"] == "accepted"]
        by_code[code] = [o for o in by_code.get(code, [])
                         if not ("scope2_method_unstated" in o["flags"] and any(agreement(o, s) for s in stated))]
    # A source that states one method with a different quantity tells the
    # method-less row apart: that row belongs to the other method.
    for code, other in (("E1-6_09", "E1-6_10"), ("E1-6_10", "E1-6_09")):
        stated = [o for o in by_code.get(code, []) if o["status"] == "accepted" and not _scope2_unstated(o)]
        if not stated:
            continue
        moved = [o for o in by_code.get(code, []) if _scope2_unstated(o) and all(agreement(o, s) is False for s in stated)]
        by_code[code] = [o for o in by_code.get(code, []) if o not in moved]
        for o in by_code.get(other, []):
            flag = "scope2_method_inferred_from_other_method_disclosure"
            if flag not in o["flags"] and any(o["value"] == m["value"] and o["table_id"] == m["table_id"] for m in moved):
                o["flags"] = [*o["flags"], flag]
    method_notes = [note for record in records for note in record.get("method_notes") or []]
    conclusions = []
    for metric in metrics:
        candidates = by_code.get(metric.code, [])
        usable = [o for o in candidates if o["status"] in ("accepted", "review")] or _promote(
            metric.code, candidates, reporting_year)
        current = [o for o in usable if reporting_year is None or o.get("period_year") in (reporting_year, None)]
        entry = {"code": metric.code, "metric_id": metric.metric_id, "package_id": metric.package_id,
                 "label": metric.label, "parent_code": metric.parent_code,
                 "rejected_candidates": len([o for o in candidates if o["status"] == "rejected"])}
        if metric.kind == "narrative":
            unique = {n["block_id"]: n for n in method_notes}
            entry.update(status="found" if unique else "not_found_in_selected_sources", statements=list(unique.values()))
            conclusions.append(entry)
            continue
        if not current:
            entry.update(status=NOT_FOUND_STATUSES[1] if candidates else NOT_FOUND_STATUSES[0],
                         candidates=[brief(o) for o in candidates[:8]])
            # Surface why printed candidates were not used, so "not found"
            # never hides a rejected combined-scope row or a prior-period row.
            reasons = sorted({o.get("rejected_by") for o in candidates if o.get("rejected_by")})
            if reasons:
                entry["rejection_reasons"] = reasons
            conclusions.append(entry)
            continue
        groups: dict[str, list[dict]] = defaultdict(list)
        for obs in current:
            groups[(obs.get("member") or "_") if metric.group in ("air", "water_pollutants") else "_"].append(obs)
        members = []
        for items in groups.values():
            full = [o for o in items if not any(f.startswith("subset_scope_") for f in o["flags"])
                    and o.get("entity_basis") != "subset"]
            # A "part of group" reading that repeats a full-scope value exactly is
            # the same disclosure seen under a misread boundary, not a segment.
            full += [o for o in items if o not in full and not any(f.startswith("subset_scope_") for f in o["flags"])
                     and any(o["value"] == f["value"] and agreement(o, f) for f in full)]
            subset = [o for o in items if o not in full]
            pool, components = _split_components(full or items)
            clusters = _cluster(pool)
            best = clusters[0]
            primary = sorted(best, key=lambda o: (o["status"] != "accepted", o.get("period_year") is None, o["page_number"]))[0]
            others = [o for cluster in clusters[1:] for o in cluster]
            if not full:
                # Different segments differ by nature; none of them is the group value.
                subset, others = [o for o in others if o is not primary], []
            # A different consolidation boundary is another valid view of the
            # group, so it never counts as a conflicting value.
            def other_boundary(item: dict) -> bool:
                return "consolidation_boundary_differs" in (item.get("flags") or [])
            conflicting = [o for o in others if not other_boundary(o) and agreement(primary, o) is False]
            alternative = [o for o in others if other_boundary(o) or agreement(primary, o) is None]
            status = primary["status"]
            flags = list(primary["flags"])
            if conflicting:
                status = "conflict"
            elif primary.get("period_year") is None:
                status = "review"
            if not full:
                # Only a subsidiary, segment or site value exists; the group
                # total is not a direct disclosure and must not be presented as one.
                status = "review" if status == "accepted" else status
                flags.append("only_part_of_group_disclosed")
            members.append({"member": primary.get("member"), "status": status, "value": primary["value"],
                            "value_raw": primary["value_raw"], "unit_raw": primary["unit_raw"], "unit_id": primary["unit_id"],
                            "period": primary.get("period"), "period_year": primary.get("period_year"),
                            "source": brief(primary), "agreeing_sources": [brief(o) for o in best if o is not primary],
                            "conflicting_sources": [brief(o) for o in conflicting],
                            "alternative_unit_sources": [brief(o) for o in alternative],
                            "related_subset_sources": [brief(o) for o in subset if o is not primary],
                            "component_sources": [brief(o) for o in components], "flags": flags})
        statuses = {m["status"] for m in members}
        overall = "found" if statuses == {"accepted"} else ("conflict" if "conflict" in statuses else "review")
        entry.update(status=overall, members=members)
        conclusions.append(entry)
    return conclusions
