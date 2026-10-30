"""Generic interpreter for clause-owned reading rules and vocabulary."""

from __future__ import annotations

import re

from dataclasses import dataclass

from esg_data.normalize import fold


@dataclass
class GuardResult:
    ok: bool
    status: str | None = None  # None keeps the bind status; "review" demotes
    flags: tuple[str, ...] = ()


def _reject(flag: str) -> GuardResult:
    return GuardResult(False, "rejected", (flag,))


MARKUP_RE = re.compile(r"[$\\{}^]|_(?=[{\w])|\\(?:mathrm|text|operatorname)")


def normalize(text: str | None) -> str:
    """Folded text with OCR math markup removed: `$ SO_{{2}} $` -> `so2`.

    Ambiguous OCR symbols are not repaired into a different pollutant.
    """
    folded = fold(text)
    folded = re.sub(
        r"\$\s*([^$]{1,40}?)\s*\$",
        lambda m: MARKUP_RE.sub("", m.group(1)).replace(" ", ""),
        folded,
    )
    parts = [part.strip() for part in MARKUP_RE.sub("", folded).split(" › ")]
    return " › ".join(parts)


def _match(terms, text: str | None) -> bool:
    """A declared term matches by its own rule: substring or ``re:`` pattern."""
    folded = fold(text or "")
    for term in terms or ():
        if not term:
            continue
        if term.startswith("re:"):
            if re.search(term[3:], folded, re.I):
                return True
        elif term.casefold() in folded:
            return True
    return False


def _vocab(reading, name: str) -> list[str]:
    return list((getattr(reading, "vocabulary", {}) or {}).get(name, []))


def _rule_scope(
    rule, *, text: str, subject: str, scope_text: str, evidence_text: str, unit: str
) -> str:
    return {
        "text": text,
        "subject": subject,
        "scope_text": scope_text,
        "evidence": evidence_text,
        "unit": unit or "",
        "all": " ".join(
            part for part in (scope_text, evidence_text, unit or "") if part
        ),
    }.get(rule.scope, text)


def _unit_rule(
    rule, *, dimension: str | None, scope_text: str, text: str, metric, flags: list[str]
) -> GuardResult | None:
    allowed = set(rule.allowed)
    params = rule.params or {}
    if dimension is None:
        if params.get("allow_missing", True):
            flags.append("unit_dimension_not_confirmed")
            return None
        return _reject("unit_missing")
    if dimension in allowed:
        if params.get("ratio_review") and dimension == "ratio":
            flags.append(str(params["ratio_review"]))
        return None
    intensity_metric = bool(
        metric
        and (
            (metric.dimension or "").endswith("intensity")
            or metric.dimension == "ratio"
        )
    )
    if dimension.endswith("intensity") and not intensity_metric:
        return _reject("unit_is_intensity")
    if (
        params.get("mass_label_review")
        and dimension == "mass"
        and _match(_vocab(metric.reading, "ghg_label"), scope_text)
    ):
        flags.append("co2e_implied_by_label_mass_unit")
        return None
    return _reject(f"unit_dimension_{dimension}_not_{'_or_'.join(sorted(allowed))}")


def _ghg_scope(
    rule,
    *,
    reading,
    text: str,
    subject: str,
    scope_text: str,
    evidence_text: str,
    dimension: str | None,
    unit: str | None,
    flags: list[str],
) -> GuardResult | None:
    params = rule.params or {}
    scope = params.get("scope", "any")
    combined = _match(
        _vocab(reading, "combined_scope"), subject if subject != text else scope_text
    )
    if scope == "scope_1":
        if not _match(_vocab(reading, "scope1"), subject):
            return _reject("label_not_this_scope")
        if combined:
            return _reject("label_is_combined_scopes")
    elif scope == "scope_2":
        if not _match(_vocab(reading, "scope2"), subject):
            return _reject("label_not_this_scope")
        if combined:
            return _reject("label_is_combined_scopes")
        if _match(_vocab(reading, "scope3"), text):
            return _reject("label_is_scope3")
    elif scope == "scope_3":
        target = subject if subject != text else scope_text
        if not _match(_vocab(reading, "scope3"), target):
            return _reject("label_not_scope3")
        if combined:
            return _reject("label_is_combined_scopes")
    else:
        if params.get("single_scope_forbidden"):
            has = {
                name
                for name in ("scope1", "scope2", "scope3")
                if _match(_vocab(reading, name), scope_text)
            }
            if has and len(has) < 3:
                return _reject("label_single_scope_intensity_not_total")
    if params.get("method_check"):
        method_text = f"{scope_text} {evidence_text}"
        market = _match(_vocab(reading, "market_method"), method_text)
        location = _match(_vocab(reading, "location_method"), method_text)
        wanted = params.get("method")
        if wanted == "location_based":
            if market and not location:
                return _reject("method_not_location_based")
            if market and location:
                flags.append("scope2_method_ambiguous")
        elif wanted == "market_based":
            if location and not market:
                return _reject("method_not_market_based")
            if market and location:
                flags.append("scope2_method_ambiguous")
    return None


def _ghg_composition(
    rule, *, reading, text: str, scope_text: str, evidence_text: str, flags: list[str]
) -> GuardResult | None:
    if not _match(_vocab(reading, "ghg_label"), text):
        return _reject("label_not_ghg_emissions")
    has_scope_words = any(
        _match(_vocab(reading, name), text) for name in ("scope1", "scope2", "scope3")
    )
    evidence = text if has_scope_words else f"{text} {evidence_text}"
    has_12 = bool(
        (
            _match(_vocab(reading, "scope1"), evidence)
            and _match(_vocab(reading, "scope2"), evidence)
        )
        or _match(_vocab(reading, "combined_scope"), evidence)
    )
    has_3 = _match(_vocab(reading, "scope3"), evidence)
    total = _match(_vocab(reading, "total"), text)
    if has_12 and not has_3:
        return _reject("label_missing_scope3_for_total")
    if (
        has_3
        and not has_12
        and (
            total
            or _match(_vocab(reading, "scope1"), evidence)
            or _match(_vocab(reading, "scope2"), evidence)
        )
    ):
        return _reject("label_scope3_total_not_all_scopes")
    if (
        (
            _match(_vocab(reading, "scope1"), evidence)
            or _match(_vocab(reading, "scope2"), evidence)
        )
        and not has_12
        and not has_3
    ):
        return _reject("label_single_scope_not_all_scopes")
    method_text = f"{scope_text} {evidence_text}"
    market = _match(_vocab(reading, "market_method"), method_text)
    location = _match(_vocab(reading, "location_method"), method_text)
    if market and not location:
        return _reject("method_not_location_based")
    if market and location:
        flags.append("scope2_method_ambiguous")
    if _match(_vocab(reading, "consolidation_boundary"), text):
        flags.append("consolidation_boundary_differs")
    return None


def _pollutant_medium(rule, *, reading, text: str, subject: str) -> GuardResult | None:
    medium = (rule.params or {}).get("medium", "air")
    allowed_name, other_name = (
        ("air_pollutant", "water_pollutant")
        if medium == "air"
        else ("water_pollutant", "air_pollutant")
    )
    own_only, other_only = (
        ("air_only", "water_only") if medium == "air" else ("water_only", "air_only")
    )
    if not _match(_vocab(reading, allowed_name), subject):
        return _reject("label_not_a_pollutant_for_medium")
    if _match(_vocab(reading, other_only), text) and not _match(
        _vocab(reading, own_only), text
    ):
        return _reject("pollutant_belongs_to_other_medium")
    return None


def _water_concept(
    rule,
    *,
    reading,
    text: str,
    subject: str,
    scope_text: str,
    component: bool,
    total: bool,
    flags: list[str],
) -> GuardResult | None:
    concept = (rule.params or {}).get("concept", "consumption")
    if (
        _match(_vocab(reading, "intensity"), text)
        or _match(_vocab(reading, "ratio_word"), text)
        or _match(_vocab(reading, "change"), text)
    ):
        return _reject("label_is_ratio_intensity_or_change")
    if _match(_vocab(reading, "not_water"), text) and not _match(
        _vocab(reading, "water_word"), text
    ):
        return _reject("label_not_about_water")
    consumption = _match(_vocab(reading, "water_consumption"), scope_text)
    withdrawal = _match(_vocab(reading, "withdrawal"), text)
    discharge = _match(_vocab(reading, "discharge"), text)
    recycle = _match(_vocab(reading, "recycle"), text)
    risk = _match(_vocab(reading, "risk_area"), text)
    if concept == "consumption":
        if not _match(_vocab(reading, "water_consumption"), subject):
            return _reject("label_not_water_consumption")
        if withdrawal and not consumption:
            return _reject("label_is_withdrawal")
        if risk:
            return _reject("label_is_risk_area_subset")
        if discharge or recycle:
            return _reject("label_is_discharge_or_recycling")
    elif concept == "risk_area":
        if not (consumption and risk):
            return _reject("label_not_water_consumption_in_risk_areas")
        if withdrawal:
            return _reject("label_is_withdrawal_in_risk_areas")
    elif concept == "recycled":
        if not _match(_vocab(reading, "recycle"), subject):
            return _reject("label_not_recycled_or_reused_water")
        if _match(_vocab(reading, "waste_solid"), scope_text) and not _match(
            _vocab(reading, "recycled_water"), text
        ):
            return _reject("label_is_solid_waste_not_water")
        if _match(_vocab(reading, "alternative_source"), text) and _match(
            _vocab(reading, "consumption_terms"), text
        ):
            flags.append("reclaimed_water_as_alternative_source")
    elif concept == "withdrawal":
        if not _match(_vocab(reading, "withdrawal"), subject):
            return _reject("label_not_water_withdrawal")
        if discharge or recycle or risk or consumption:
            return _reject("label_is_other_water_concept")
        if _match(_vocab(reading, "water_use_only"), text) and not _match(
            _vocab(reading, "withdrawal"), text
        ):
            flags.append("water_use_reported_as_withdrawal")
    elif concept == "discharge":
        if not _match(_vocab(reading, "discharge"), scope_text):
            return _reject("label_not_water_discharge")
        if risk or recycle:
            return _reject("label_is_other_water_concept")
    if concept in {"consumption", "withdrawal", "discharge", "recycled"}:
        qualifier = (
            _match(_vocab(reading, "freshwater"), text)
            or _match(_vocab(reading, "non_fresh"), text)
            or _match(_vocab(reading, "water_source"), text)
            or (concept == "discharge" and _match(_vocab(reading, "water_dest"), text))
        )
        if component and not total:
            return _reject("component_row")
        if qualifier:
            if total or consumption:
                return None
            return _reject("component_by_source_or_destination")
    return None


def _waste_concept(
    rule, *, reading, text: str, subject: str, scope_text: str, flags: list[str]
) -> GuardResult | None:
    hazardous = (rule.params or {}).get("hazardous")
    total = _match(_vocab(reading, "total"), text)
    if hazardous is True:
        if not (
            _match(_vocab(reading, "waste"), subject)
            and _match(_vocab(reading, "hazardous"), text)
        ):
            return _reject("label_not_hazardous_waste")
        if _match(_vocab(reading, "non_hazardous"), text) and not _match(
            _vocab(reading, "hazardous"), text
        ):
            return _reject("label_is_non_hazardous_waste")
        if _match(_vocab(reading, "waste_dest"), text) and not _match(
            _vocab(reading, "generated"), text
        ):
            return _reject("label_is_treatment_route_not_generated")
        return None
    if not _match(_vocab(reading, "waste"), subject):
        return _reject("label_not_waste")
    if _match(_vocab(reading, "waste_category"), text):
        return _reject("label_is_waste_category")
    if _match(_vocab(reading, "waste_solid"), text) and not total:
        return _reject("label_is_waste_category")
    if _match(_vocab(reading, "ratio_word"), text) or _match(
        _vocab(reading, "intensity"), text
    ):
        return _reject("label_is_ratio_or_intensity")
    if _match(_vocab(reading, "hazardous"), text) and not total:
        return _reject("label_is_hazardous_subset")
    if _match(_vocab(reading, "waste_dest"), text) and not _match(
        _vocab(reading, "generated"), text
    ):
        return _reject("label_is_treatment_route_not_generated")
    return None


def check_reading(
    metric,
    label: str,
    section: str | None,
    unit: str | None,
    dimension: str | None,
    *,
    evidence: str | None = None,
) -> GuardResult:
    """Evaluate a frozen reading contract; the rule engine knows no clause key."""
    reading = getattr(metric, "reading", None)
    text = normalize(label)
    section = normalize(section)
    unit = normalize(unit) if unit else unit
    scope_text = f"{section or ''} {text}"
    evidence_text = normalize(evidence) if evidence else ""
    if reading is None:
        return GuardResult(True, None, ())
    flags: list[str] = []
    review = False
    last = text.split(" › ")[-1]
    subject = scope_text if _match(_vocab(reading, "subject_generic"), last) else text
    component = _match(_vocab(reading, "component_prefix"), last)
    other_item = _match(_vocab(reading, "other_member"), last)
    intensity_metric = bool(
        metric
        and (
            (metric.dimension or "").endswith("intensity")
            or metric.dimension == "ratio"
        )
    )
    if unit and (dimension or "").endswith("intensity") and not intensity_metric:
        return _reject("unit_is_intensity")
    for rule in getattr(reading, "rules", ()):
        target = _rule_scope(
            rule,
            text=text,
            subject=subject,
            scope_text=scope_text,
            evidence_text=evidence_text,
            unit=unit or "",
        )
        if rule.rule == "require_any":
            if not _match(rule.terms, target):
                return _reject(rule.reason or "concept")
        elif rule.rule == "require_all":
            if not all(_match([term], target) for term in rule.terms):
                return _reject(rule.reason or "concept")
        elif rule.rule in {"forbid_any", "forbid_any_unless"}:
            if _match(rule.terms, target) and not (
                rule.unless_terms and _match(rule.unless_terms, target)
            ):
                return _reject(rule.reason or "concept")
        elif rule.rule == "forbid_if_all":
            if _match(rule.if_terms, target) and _match(rule.terms, target):
                return _reject(rule.reason or "concept")
        elif rule.rule == "reject_component":
            hit = _match(rule.terms, text) if rule.terms else (component or other_item)
            if hit and not (rule.unless_terms and _match(rule.unless_terms, target)):
                return _reject(rule.reason or "component_row")
        elif rule.rule == "flag_review":
            if _match(rule.terms, target) and not (
                rule.unless_terms and _match(rule.unless_terms, target)
            ):
                flags.append(rule.flag or "review")
                review = True
        elif rule.rule == "unit_dimension":
            rejected = _unit_rule(
                rule,
                dimension=dimension,
                scope_text=scope_text,
                text=text,
                metric=metric,
                flags=flags,
            )
            if rejected is not None:
                return rejected
        elif rule.rule == "ratio_required":
            if dimension != "ratio" and not (
                _match(_vocab(reading, "ratio_word"), scope_text)
                or _match(_vocab(reading, "ratio_word"), unit or "")
            ):
                return _reject(rule.reason or "not_a_ratio")
        elif rule.rule == "ghg_unit":
            if (
                unit
                and dimension != "ghg-emissions"
                and not _match(_vocab(reading, "ghg_unit"), unit)
            ):
                if dimension == "mass" and _match(
                    _vocab(reading, "ghg_label"), scope_text
                ):
                    flags.append("co2e_implied_by_label_mass_unit")
                    review = True
                else:
                    return _reject("unit_not_co2e")
        elif rule.rule == "ghg_scope":
            rejected = _ghg_scope(
                rule,
                reading=reading,
                text=text,
                subject=subject,
                scope_text=scope_text,
                evidence_text=evidence_text,
                dimension=dimension,
                unit=unit,
                flags=flags,
            )
            if rejected is not None:
                return rejected
        elif rule.rule == "ghg_scope_composition":
            rejected = _ghg_composition(
                rule,
                reading=reading,
                text=text,
                scope_text=scope_text,
                evidence_text=evidence_text,
                flags=flags,
            )
            if rejected is not None:
                return rejected
        elif rule.rule == "pollutant_medium":
            rejected = _pollutant_medium(
                rule, reading=reading, text=text, subject=subject
            )
            if rejected is not None:
                return rejected
        elif rule.rule == "water_concept":
            rejected = _water_concept(
                rule,
                reading=reading,
                text=text,
                subject=subject,
                scope_text=scope_text,
                component=component,
                total=_match(_vocab(reading, "total"), text),
                flags=flags,
            )
            if rejected is not None:
                return rejected
        elif rule.rule == "waste_concept":
            rejected = _waste_concept(
                rule,
                reading=reading,
                text=text,
                subject=subject,
                scope_text=scope_text,
                flags=flags,
            )
            if rejected is not None:
                return rejected
    if any(flag == "scope2_method_ambiguous" for flag in flags):
        review = True
    if any(
        flag in {"consolidation_boundary_differs", "co2e_implied_by_label_mass_unit"}
        for flag in flags
    ):
        review = True
    return GuardResult(
        True, "review" if review or flags else None, tuple(dict.fromkeys(flags))
    )
