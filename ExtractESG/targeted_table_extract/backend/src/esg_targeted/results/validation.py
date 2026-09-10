from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from esg_targeted.results.spec import RECORD_ID_PREFIXES, RECORD_LAYOUT


class ResultContractError(ValueError):
    pass


class ResultContractValidator:
    """Validate Core records and their foreign-key graph before export."""

    def __init__(self, core_schema: Any) -> None:
        self.record_contracts = {
            item.record_type.value: item for item in core_schema.record_types
        }
        self.code_sets = {
            item.code_set_id: {value.code for value in item.values}
            for item in core_schema.common_code_sets
        }

    def validate(self, records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        errors: list[str] = []
        record_ids: dict[str, set[str]] = defaultdict(set)
        total = 0

        for collection, layout in RECORD_LAYOUT.items():
            rows = records.get(collection, [])
            total += len(rows)
            record_type = layout["record_type"]
            primary_key = layout["primary_key"]
            contract = self.record_contracts.get(record_type)
            if contract is None:
                errors.append(f"Core Schema is missing record type {record_type}")
                continue
            allowed_fields = {item.field_id for item in contract.fields}
            required_fields = {item.field_id for item in contract.fields if item.required}
            fields_by_id = {item.field_id: item for item in contract.fields}
            seen: set[str] = set()
            for index, row in enumerate(rows, 1):
                location = f"{collection}[{index}]"
                missing = sorted(required_fields - row.keys())
                unknown = sorted(row.keys() - allowed_fields)
                if missing:
                    errors.append(f"{location} missing required fields: {missing}")
                if unknown:
                    errors.append(f"{location} contains unknown fields: {unknown}")
                for field_id, value in row.items():
                    field = fields_by_id.get(field_id)
                    if field is None:
                        continue
                    if value is None:
                        if field.required and not field.nullable:
                            errors.append(f"{location}.{field_id} cannot be null")
                        continue
                    if not self._matches_type(value, field.value_type.value):
                        errors.append(
                            f"{location}.{field_id} does not match {field.value_type.value}"
                        )
                    if field.code_set_id and value not in self.code_sets[field.code_set_id]:
                        errors.append(
                            f"{location}.{field_id} has unknown code {value}"
                        )
                record_id = row.get(primary_key)
                if not isinstance(record_id, str) or not record_id:
                    errors.append(f"{location} has invalid primary key {primary_key}")
                elif record_id in seen:
                    errors.append(f"{location} duplicates {primary_key}={record_id}")
                elif not record_id.startswith(RECORD_ID_PREFIXES[collection]):
                    errors.append(
                        f"{location}.{primary_key} must start with "
                        f"{RECORD_ID_PREFIXES[collection]}"
                    )
                else:
                    seen.add(record_id)
                    record_ids[record_type].add(record_id)

        task_ids = record_ids["reporting_task"]
        fact_ids = {
            **{item: "quantitative_observation" for item in record_ids["quantitative_observation"]},
            **{item: "qualitative_assertion" for item in record_ids["qualitative_assertion"]},
        }
        for collection in ("quantitative_observations", "qualitative_assertions"):
            for row in records.get(collection, []):
                if row.get("task_id") not in task_ids:
                    errors.append(
                        f"{collection}:{row.get('task_id')} references an unknown reporting task"
                    )

        parent_ids = {
            "reporting_task": task_ids,
            "quantitative_observation": record_ids["quantitative_observation"],
            "qualitative_assertion": record_ids["qualitative_assertion"],
        }
        for collection in ("attribute_values", "dimension_values"):
            for row in records.get(collection, []):
                parent_type = row.get("parent_record_type")
                parent_id = row.get("parent_record_id")
                if parent_id not in parent_ids.get(parent_type, set()):
                    errors.append(
                        f"{collection}:{parent_id} references unknown {parent_type}"
                    )

        evidence_by_source: Counter[str] = Counter()
        facts_by_id = {
            row["observation_id"]: row
            for row in records.get("quantitative_observations", [])
        }
        facts_by_id.update(
            {
                row["assertion_id"]: row
                for row in records.get("qualitative_assertions", [])
            }
        )
        for row in records.get("evidence_references", []):
            source_id = row.get("source_record_id")
            source_type = row.get("source_record_type")
            if fact_ids.get(source_id) != source_type:
                errors.append(
                    f"evidence_references:{row.get('evidence_id')} references unknown "
                    f"{source_type}:{source_id}"
                )
            elif row.get("evidence_set_id") != facts_by_id[source_id].get("evidence_set_id"):
                errors.append(
                    f"evidence_references:{row.get('evidence_id')} has an evidence_set_id "
                    "different from its source fact"
                )
            evidence_by_source[str(source_id)] += 1

        for collection in ("quantitative_observations", "qualitative_assertions"):
            for row in records.get(collection, []):
                primary_key = RECORD_LAYOUT[collection]["primary_key"]
                record_id = row.get(primary_key)
                if evidence_by_source.get(str(record_id), 0) == 0:
                    errors.append(f"{collection}:{record_id} has no evidence reference")

        fact_count_by_task: Counter[str] = Counter(
            row["task_id"] for row in facts_by_id.values()
        )
        evidence_count_by_task: Counter[str] = Counter()
        for source_id, count in evidence_by_source.items():
            fact = facts_by_id.get(source_id)
            if fact:
                evidence_count_by_task[fact["task_id"]] += count
        for task in records.get("reporting_tasks", []):
            task_id = task["task_id"]
            if task.get("result_count") != fact_count_by_task[task_id]:
                errors.append(f"reporting_tasks:{task_id} has an inconsistent result_count")
            if task.get("evidence_count") != evidence_count_by_task[task_id]:
                errors.append(f"reporting_tasks:{task_id} has an inconsistent evidence_count")

        if errors:
            raise ResultContractError("; ".join(errors))
        return {
            "status": "passed",
            "record_count": total,
            "collection_counts": {
                key: len(records.get(key, [])) for key in RECORD_LAYOUT
            },
            "relation_checks": [
                "fact_to_task",
                "attribute_to_parent",
                "dimension_to_parent",
                "evidence_to_fact",
                "fact_has_evidence",
                "evidence_set_consistency",
                "task_record_counts",
            ],
        }

    @staticmethod
    def _matches_type(value: Any, value_type: str) -> bool:
        if value_type in {"string", "text", "identifier", "uri", "enum"}:
            return isinstance(value, str)
        if value_type == "hash":
            return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))
        if value_type in {"integer", "year"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if value_type == "boolean":
            return isinstance(value, bool)
        if value_type == "decimal":
            if isinstance(value, bool):
                return False
            try:
                Decimal(str(value))
                return True
            except (InvalidOperation, ValueError):
                return False
        if value_type == "date":
            if not isinstance(value, str):
                return False
            try:
                date.fromisoformat(value)
                return True
            except ValueError:
                return False
        if value_type in {"date_range", "decimal_range"}:
            return isinstance(value, (dict, list, tuple, str))
        return False
