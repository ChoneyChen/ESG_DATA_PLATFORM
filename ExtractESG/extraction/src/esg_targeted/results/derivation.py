from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from esg_targeted.ids import stable_id
from esg_targeted.evidence.units import canonical_unit_id, normalize_unit_text


class DerivationExecutor:
    """Execute package-declared data-processing derivations conservatively.

    This stage never infers missing identity or units. It only computes when all
    operands have the same explicit period, boundary, required dimensions and
    compatible units. Reported target observations always take precedence.
    """

    def execute(
        self,
        *,
        records: dict[str, list[dict[str, Any]]],
        outcomes: list[dict[str, Any]],
        package,
        source_facts=None,
        source_bindings=None,
    ) -> dict[str, Any]:
        observations = records.get("quantitative_observations", [])
        dimensions = records.get("dimension_values", [])
        evidence = records.get("evidence_references", [])
        tasks_by_metric = {
            item["metric_id"]: item for item in records.get("reporting_tasks", [])
        }
        metrics_by_id = {item.metric_id: item for item in package.metrics}
        dimension_map = self._dimension_map(dimensions)
        source_by_id = {f["source_fact_id"]: f for f in (source_facts or [])}
        source_semantics = {b["core_record_id"]: source_by_id.get(b["source_fact_id"], {}).get("semantics", {})
                            for b in (source_bindings or [])}
        evidence_by_fact: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in evidence:
            evidence_by_fact[item.get("source_record_id")].append(item)

        report = {
            "schema_version": "derivation-execution-v1",
            "rules": [],
            "derived_observation_ids": [],
            "computations": [],
            "package_digest": package.compilation.source_digest if getattr(package, "compilation", None) else None,
        }
        blocked_metrics = {
            item.get("metric_id")
            for item in outcomes
            if item.get("semantic_decision_status") == "unresolved"
            or item.get("contract_validation_status") == "failed"
        }
        for outcome in outcomes:
            outcome.update(dependency_status="not_applicable", dependency_blocks=[])
        for rule in package.derivations:
            if rule.execution_stage != "data_processing":
                continue
            rule_report = {
                "rule_id": rule.rule_id,
                "target_metric_id": rule.target_metric_id,
                "status": "not_ready",
                "derived_observation_ids": [],
                "skipped": [],
                "dependency_bindings": [],
                "dependency_status": "not_applicable",
                "dependency_blocks": [],
            }
            target_metric = metrics_by_id.get(rule.target_metric_id)
            if (
                target_metric is None
                or not target_metric.depends_on
                or target_metric.derivation_policy
                != "reported_then_derived_when_dependencies_complete"
            ):
                rule_report["skipped"].append(
                    "package_derivation_execution_not_enabled"
                )
                self._finish_rule(report, rule_report, records, outcomes)
                continue
            target_task = tasks_by_metric.get(rule.target_metric_id)
            if target_task is None:
                rule_report["skipped"].append("target_metric_not_selected")
                self._finish_rule(report, rule_report, records, outcomes)
                continue
            rule_report["dependency_status"] = "ready"
            rule_report["reported_target_ids"] = [item["observation_id"] for item in observations
                if item.get("metric_id") == rule.target_metric_id and item.get("value_origin") == "reported"]
            if any(
                operand.source_metric_id in blocked_metrics
                and any(item.get("metric_id") == operand.source_metric_id for item in observations)
                for operand in rule.operands
            ):
                rule_report["skipped"].append("dependency_semantics_unresolved")
                self._block(rule_report, "mapping", "dependency_semantics_unresolved",
                    "An operand's semantic mapping or result contract is unresolved.",
                    dependency_metric_ids=[operand.source_metric_id for operand in rule.operands
                                           if operand.source_metric_id in blocked_metrics
                                           and any(item.get("metric_id") == operand.source_metric_id for item in observations)])
                self._finish_rule(report, rule_report, records, outcomes)
                continue

            operand_groups = {}
            for operand in rule.operands:
                source = [
                    item
                    for item in observations
                    if item.get("metric_id") == operand.source_metric_id
                    and item.get("value_origin") in {None, "reported", "derived"}
                ]
                if not source:
                    outcome = next((item for item in outcomes if item.get("metric_id") == operand.source_metric_id), {})
                    self._block(rule_report, "source", "dependency_observations_missing",
                        "No accepted quantitative operand is available; absence of an operand is not non-disclosure of the target.",
                        dependency_metric_ids=[operand.source_metric_id], operand_id=operand.operand_id,
                        dependency_result_status=outcome.get("status", "not_executed"),
                        dependency_search_resolution=outcome.get("search_resolution_status", "not_applicable"))
                operand_groups[operand.operand_id] = self._group_operands(
                    source,
                    rule.required_match_fields,
                    dimension_map,
                    metrics_by_id.get(operand.source_metric_id),
                    source_semantics=source_semantics,
                    total_dimension_id=next((
                        element.binding.target
                        for element in package.elements
                        if package.format_version in {"2.0", "2.1"}
                        and element.metric_id == operand.source_metric_id
                        and element.element_code == "aggregation_role"
                        and element.binding.storage.value == "dimension"
                        and element.value_contract.fixed_value is None
                    ), None),
                    issues=rule_report["dependency_blocks"],
                    dependency_metric_id=operand.source_metric_id,
                    operand_id=operand.operand_id,
                    rule_id=rule.rule_id,
                )
            if any(not groups for groups in operand_groups.values()):
                rule_report["skipped"].append("dependency_observations_incomplete")
                self._finish_rule(report, rule_report, records, outcomes)
                continue

            common_keys = set.intersection(
                *(set(groups) for groups in operand_groups.values())
            )
            if not common_keys:
                rule_report["skipped"].append("dependency_identity_mismatch")
                self._block(rule_report, "identity", "dependency_identity_mismatch",
                    "Operand periods, entities, boundaries or other required identity axes do not share a complete matching identity.",
                    dependency_metric_ids=[operand.source_metric_id for operand in rule.operands],
                    required_fields=list(rule.required_match_fields),
                    operand_identities={operand.operand_id: [dict(key) for key in operand_groups[operand.operand_id]]
                                        for operand in rule.operands})
                self._finish_rule(report, rule_report, records, outcomes)
                continue
            reported_targets = [
                item
                for item in observations
                if item.get("metric_id") == rule.target_metric_id
                and item.get("value_origin") == "reported"
            ]
            reported_target_keys = {
                key
                for item in reported_targets
                if (
                    key := self._identity_key(
                        item,
                        rule.required_match_fields,
                        dimension_map,
                    )
                )
                is not None
            }
            if len(reported_target_keys) != len(reported_targets):
                rule_report["skipped"].append(
                    "reported_target_identity_incomplete"
                )
                self._block(rule_report, "identity", "reported_target_identity_incomplete",
                    "A directly reported target has incomplete identity; deriving a competing value could duplicate it.",
                    observation_ids=[item["observation_id"] for item in reported_targets],
                    required_fields=list(rule.required_match_fields))
                self._finish_rule(report, rule_report, records, outcomes)
                continue
            for identity_key in sorted(common_keys, key=repr):
                if rule.reported_value_priority and identity_key in reported_target_keys:
                    rule_report["skipped"].append(
                        {"identity": list(identity_key), "reason": "reported_target_priority"}
                    )
                    continue
                operands = [
                    operand_groups[operand.operand_id][identity_key]
                    for operand in rule.operands
                ]
                derived = self._calculate(rule, operands)
                if derived is None:
                    rule_report["skipped"].append(
                        {"identity": list(identity_key), "reason": "unit_or_value_incompatible"}
                    )
                    code, reason = self._calculation_failure(rule, operands)
                    self._block(rule_report, "calculation", code, reason,
                        dependency_metric_ids=[operand.source_metric_id for operand in rule.operands],
                        identity=dict(identity_key), operand_units=[list(operand["unit"]) for operand in operands])
                    continue
                value, unit_raw, unit_id = derived
                source_fact_ids = sorted(
                    {
                        fact["observation_id"]
                        for operand in operands
                        for fact in operand["facts"]
                    }
                )
                source_evidence = [
                    item
                    for fact_id in source_fact_ids
                    for item in evidence_by_fact.get(fact_id, [])
                ]
                missing_evidence = [fact_id for fact_id in source_fact_ids if not evidence_by_fact.get(fact_id)]
                if missing_evidence:
                    rule_report["skipped"].append(
                        {"identity": list(identity_key), "reason": "operand_evidence_missing"}
                    )
                    self._block(rule_report, "source", "operand_evidence_missing",
                        "Every operand must have its own accepted source evidence before derivation.",
                        observation_ids=missing_evidence, identity=dict(identity_key))
                    continue
                observation_id = stable_id(
                    "obs-derived",
                    rule.rule_id,
                    identity_key,
                    source_fact_ids,
                )
                if any(
                    item.get("observation_id") == observation_id
                    for item in observations
                ):
                    continue
                evidence_set_id = stable_id(
                    "evset-derived", rule.rule_id, observation_id
                )
                first = operands[0]["facts"][0]
                value_raw = self._decimal_text(value)
                observation = {
                    "observation_id": observation_id,
                    "task_id": target_task["task_id"],
                    "metric_id": rule.target_metric_id,
                    "value_origin": "derived",
                    "value_raw": value_raw,
                    "comparator": "exact",
                    "value_numeric": value_raw,
                    "value_lower": None,
                    "value_upper": None,
                    "unit_raw": unit_raw,
                    "unit_id": unit_id,
                    "reporting_period_raw": first.get("reporting_period_raw"),
                    "reporting_year": first.get("reporting_year"),
                    "period_start": first.get("period_start"),
                    "period_end": first.get("period_end"),
                    "reporting_boundary": first.get("reporting_boundary"),
                    "evidence_set_id": evidence_set_id,
                    "confidence": None,
                    "review_status": "pending",
                }
                observations.append(observation)
                rule_report["dependency_bindings"].append({"derived_observation_id": observation_id,
                    "source_observation_ids": source_fact_ids, "rule_id": rule.rule_id,
                    "formula_version": f"{package.manifest.package_id}@{package.manifest.package_version}:{rule.rule_id}" if getattr(package, "manifest", None) else rule.rule_id,
                    "operation": rule.operation, "identity": dict(identity_key)})
                report["computations"].append({"observation_id": observation_id, "rule_id": rule.rule_id,
                    "operation": rule.operation, "operand_fact_ids": source_fact_ids,
                    "identity": dict(identity_key), "package_digest": report["package_digest"]})
                new_dimensions = self._target_dimensions(
                    package=package,
                    target_metric_id=rule.target_metric_id,
                    observation_id=observation_id,
                    evidence_set_id=evidence_set_id,
                    identity_key=identity_key,
                )
                dimensions.extend(new_dimensions)
                dimension_map[observation_id] = {
                    item["dimension_id"]: (
                        item["value_raw"],
                        item.get("value_code"),
                    )
                    for item in new_dimensions
                }
                for source in source_evidence:
                    copied = dict(source)
                    copied.update(
                        evidence_id=stable_id(
                            "evidence-derived",
                            observation_id,
                            source.get("evidence_id"),
                        ),
                        evidence_set_id=evidence_set_id,
                        source_record_type="quantitative_observation",
                        source_record_id=observation_id,
                        evidence_role="supporting",
                        review_status="pending",
                    )
                    evidence.append(copied)
                    evidence_by_fact[observation_id].append(copied)
                rule_report["derived_observation_ids"].append(observation_id)
                report["derived_observation_ids"].append(observation_id)

            if rule_report["derived_observation_ids"]:
                rule_report["status"] = "completed"
                self._refresh_target_state(
                    target_metric_id=rule.target_metric_id,
                    records=records,
                    outcomes=outcomes,
                )
            self._finish_rule(report, rule_report, records, outcomes)
        return report

    @staticmethod
    def _block(rule_report, stage, code, reason, **detail):
        rule_report["dependency_blocks"].append({"rule_id": rule_report["rule_id"],
            "stage": stage, "code": code, "reason": reason, **detail})

    @staticmethod
    def _finish_rule(report, rule_report, records, outcomes):
        blocks = rule_report["dependency_blocks"]
        if blocks:
            rule_report["dependency_status"] = "blocked"
        for outcome in outcomes:
            if outcome.get("metric_id") != rule_report["target_metric_id"]:
                continue
            if rule_report["dependency_status"] == "blocked":
                outcome["dependency_status"] = "blocked"
                outcome.setdefault("dependency_blocks", []).extend(blocks)
                # A reported fact remains usable even if an optional calculation
                # cannot run. Missing operands never negate that report fact.
                has_reported = any(item.get("metric_id") == outcome["metric_id"] and item.get("value_origin") == "reported"
                                   for item in records.get("quantitative_observations", []))
                if not has_reported and outcome.get("status") == "not_found":
                    outcome.update(status="ambiguous", found_status="uncertain",
                        semantic_decision_status="unresolved", review_status="human_required",
                        uncertainty_reason="Package derivation is blocked; missing operands do not establish target non-disclosure.")
                    for task in records.get("reporting_tasks", []):
                        if task.get("metric_id") == outcome["metric_id"]:
                            task.update(task_status="partial", found_status="uncertain", review_status="human_required",
                                        uncertainty_reason=outcome["uncertainty_reason"])
            elif outcome.get("dependency_status") != "blocked":
                outcome["dependency_status"] = rule_report["dependency_status"]
        report["rules"].append(rule_report)

    @classmethod
    def apply_dependency_state(cls, *, records, outcomes, report):
        """Overlay durable rule verdicts without materializing any derived Core.

        Stage readers use this after an interruption between writing the rule
        report and exporting the final Bundle. It is idempotent and only updates
        task/outcome metadata; checkpoint quantities remain unchanged.
        """
        for outcome in outcomes:
            outcome.update(dependency_status="not_applicable", dependency_blocks=[])
        for rule in report.get("rules", []):
            if "dependency_status" not in rule:
                continue  # old rule reports carry no checked dependency verdict
            cls._finish_rule({"rules": []}, rule, records, outcomes)

    @staticmethod
    def _calculation_failure(rule, operands):
        if any(not any(operand["unit"]) for operand in operands):
            return "operand_unit_unknown", "A required operand has no explicit unit."
        if rule.operation == "sum" and len({operand["unit"] for operand in operands}) != 1:
            return "operand_units_incompatible", "Sum operands have incompatible explicit units; no implicit conversion is permitted."
        if rule.operation == "ratio":
            roles = {operand.role: value for operand, value in zip(rule.operands, operands)}
            if roles.get("denominator", {}).get("value") == 0:
                return "zero_denominator", "The ratio denominator is zero."
            if not roles.get("numerator") or not roles.get("denominator"):
                return "ratio_roles_missing", "The ratio requires explicit numerator and denominator operands."
        return "unsupported_operation", "The package operation or its explicit operand contract cannot be executed."

    @staticmethod
    def _dimension_map(dimensions):
        result: dict[str, dict[str, tuple[str, str | None]]] = defaultdict(dict)
        for item in dimensions:
            parent = item.get("parent_record_id")
            dimension_id = item.get("dimension_id")
            if parent and dimension_id:
                result[parent][dimension_id] = (
                    str(item.get("value_raw") or ""),
                    item.get("value_code"),
                )
        return result

    @staticmethod
    def _identity_key(observation, required_fields, dimension_map):
        values = []
        for field in required_fields:
            if field in observation:
                value = observation.get(field)
            else:
                dimension = dimension_map.get(
                    observation.get("observation_id"), {}
                ).get(field)
                value = (dimension[1] or dimension[0]) if dimension else None
            if value is None or str(value).strip() == "":
                return None
            values.append((field, str(value).strip()))
        return tuple(values)

    def _group_operands(
        self,
        observations,
        required_fields,
        dimension_map,
        metric,
        total_dimension_id=None,
        source_semantics=None,
        issues=None,
        dependency_metric_id=None,
        operand_id=None,
        rule_id=None,
    ):
        def block(stage, code, reason, facts, **detail):
            if issues is not None:
                issues.append({"rule_id": rule_id, "stage": stage, "code": code, "reason": reason,
                    "dependency_metric_ids": [dependency_metric_id], "operand_id": operand_id,
                    "observation_ids": [fact["observation_id"] for fact in facts], **detail})

        grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        non_total = []
        for item in observations:
            if total_dimension_id:
                role = dimension_map.get(item.get("observation_id"), {}).get(total_dimension_id)
                declared_role = (role[1] if role else None) or (source_semantics or {}).get(item.get("observation_id"), {}).get("aggregation_role")
                if declared_role != "total":
                    non_total.append(item)
                    continue  # a component is not the total input of a formula
            key = self._identity_key(item, required_fields, dimension_map)
            if key is not None:
                grouped[key].append(item)
            else:
                missing = [field for field in required_fields
                           if self._identity_key(item, [field], dimension_map) is None]
                block("identity", "operand_identity_incomplete",
                      "An operand lacks explicit required identity fields.", [item], missing_fields=missing)
        if non_total and not grouped:
            block("identity", "operand_total_role_unconfirmed",
                  "The operand requires an explicitly supported total; a component or unknown aggregation role cannot substitute for it.", non_total)
        result = {}
        can_aggregate_members = bool(
            metric
            and "scope3_category" in metric.required_semantic_discriminators
        )
        for key, facts in grouped.items():
            numeric = []
            for fact in facts:
                try:
                    value = Decimal(str(fact.get("value_numeric")))
                    if not value.is_finite():
                        raise InvalidOperation
                    numeric.append(value)
                except (InvalidOperation, TypeError, ValueError):
                    numeric = []
                    break
            conflicting = [fact for fact in facts if fact.get("unit_id") and canonical_unit_id(fact.get("unit_raw"))
                           and fact["unit_id"] != canonical_unit_id(fact["unit_raw"])]
            if conflicting:
                block("calculation", "operand_unit_id_conflict",
                      "An operand's explicit raw unit contradicts its normalized unit ID; the scale cannot be assumed.",
                      conflicting, identity=dict(key))
                continue
            units = {self._unit_key(fact) for fact in facts}
            if not numeric:
                block("calculation", "operand_numeric_value_invalid",
                      "A reported operand has no finite accepted numeric value.", facts, identity=dict(key))
                continue
            if len(units) != 1:
                block("calculation", "operand_units_incompatible",
                      "Occurrences of an operand have incompatible explicit units.", facts,
                      identity=dict(key), units=[list(unit) for unit in sorted(units)])
                continue
            if not any(next(iter(units))):
                block("calculation", "operand_unit_unknown",
                      "A reported operand lacks an explicit unit; a requested standard unit cannot fill it.", facts, identity=dict(key))
                continue
            if len(facts) > 1 and not can_aggregate_members:
                block("identity", "operand_occurrence_ambiguous",
                      "Several operand occurrences share the same required identity; no confirmed relation permits choosing or summing them.", facts, identity=dict(key))
                continue
            result[key] = {
                "facts": facts,
                "value": sum(numeric),
                "unit": next(iter(units)),
                "unit_raw": facts[0].get("unit_raw"),
                "unit_id": facts[0].get("unit_id"),
            }
        return result

    @staticmethod
    def _unit_key(fact):
        raw = fact.get("unit_raw")
        canonical = canonical_unit_id(raw)
        # Unknown raw scales stay in the comparison key even if a Core unit ID
        # exists. A shared ID cannot silently turn 亿吨 into tonnes for a sum.
        return (canonical or str(fact.get("unit_id") or ""),
                "" if canonical or not raw else normalize_unit_text(raw))

    @staticmethod
    def _calculate(rule, operands):
        units = [item["unit"] for item in operands]
        if any(not any(unit) for unit in units):
            return None
        if rule.operation == "sum":
            if len(set(units)) != 1:
                return None
            return (
                sum(item["value"] for item in operands),
                operands[0]["unit_raw"],
                operands[0]["unit_id"],
            )
        if rule.operation == "ratio":
            roles = {operand.role: value for operand, value in zip(rule.operands, operands)}
            numerator = roles.get("numerator")
            denominator = roles.get("denominator")
            if numerator is None or denominator is None or denominator["value"] == 0:
                return None
            numerator_unit = numerator["unit_raw"] or numerator["unit_id"]
            denominator_unit = denominator["unit_raw"] or denominator["unit_id"]
            if not numerator_unit or not denominator_unit:
                return None
            return (
                numerator["value"] / denominator["value"],
                f"{numerator_unit}/{denominator_unit}",
                None,
            )
        return None

    @staticmethod
    def _target_dimensions(
        *,
        package,
        target_metric_id,
        observation_id,
        evidence_set_id,
        identity_key,
    ):
        identity = dict(identity_key)
        dimensions = []
        for element in package.elements:
            if (
                element.metric_id != target_metric_id
                or element.binding.storage.value != "dimension"
                or element.binding.record_type.value != "quantitative_observation"
            ):
                continue
            dimension_id = element.binding.target
            fixed = element.value_contract.fixed_value
            value = fixed if fixed is not None else identity.get(dimension_id)
            if value is None:
                continue
            value_raw = str(value)
            dimensions.append(
                {
                    "dimension_value_id": stable_id(
                        "dim", observation_id, element.element_id, value_raw
                    ),
                    "parent_record_type": "quantitative_observation",
                    "parent_record_id": observation_id,
                    "metric_id": target_metric_id,
                    "element_id": element.element_id,
                    "dimension_id": dimension_id,
                    "value_raw": value_raw,
                    "value_code": value_raw if fixed is not None else None,
                    "value_level": None,
                    "parent_dimension_value_id": None,
                    "sequence": 1,
                    "evidence_set_id": evidence_set_id,
                    "review_status": "pending",
                }
            )
        return dimensions

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        normalized = format(value.normalize(), "f")
        return "0" if normalized in {"-0", ""} else normalized

    @staticmethod
    def _refresh_target_state(*, target_metric_id, records, outcomes):
        observations = [
            item
            for item in records.get("quantitative_observations", [])
            if item.get("metric_id") == target_metric_id
        ]
        fact_ids = {item["observation_id"] for item in observations}
        dimension_count = sum(
            item.get("metric_id") == target_metric_id
            for item in records.get("dimension_values", [])
        )
        evidence_count = sum(
            item.get("source_record_id") in fact_ids
            for item in records.get("evidence_references", [])
        )
        for task in records.get("reporting_tasks", []):
            if task.get("metric_id") != target_metric_id:
                continue
            task.update(
                result_count=len(observations),
                evidence_count=evidence_count,
                task_status="partial",
                found_status="partial",
                review_status="pending",
                uncertainty_reason=(
                    "One or more values were produced by a package-declared "
                    "data-processing derivation and require review."
                ),
            )
        for outcome in outcomes:
            if outcome.get("metric_id") != target_metric_id:
                continue
            outcome["result_counts"]["quantitative_observation"] = len(observations)
            outcome["result_counts"]["dimension_value"] = dimension_count
            outcome["result_counts"]["evidence_reference"] = evidence_count
            outcome.update(
                status="partial",
                found_status="partial",
                review_status="pending",
                semantic_decision_status="decided",
                display_readiness_status="ready",
                uncertainty_reason=(
                    "Package-declared derivation produced a reviewable value; "
                    "directly reported values remain preferred."
                ),
            )
