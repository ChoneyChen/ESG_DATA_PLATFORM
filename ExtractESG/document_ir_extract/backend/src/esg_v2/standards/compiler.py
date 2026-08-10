from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from esg_v2.standards.contracts import RequirementProfile, RequirementSlot, StandardTaskSpec


RULEPACK_PATH = Path(__file__).with_name("rulepacks") / "esrs_en_zh_v1.json"
EXECUTION_RULEPACK_PATH = Path(__file__).with_name("rulepacks") / "esrs_direct_fill_execution_v2.json"


class RequirementProfileCompiler:
    def __init__(
        self,
        rulepack_path: Path = RULEPACK_PATH,
        execution_rulepack_path: Path = EXECUTION_RULEPACK_PATH,
    ):
        self.rulepack_path = rulepack_path
        self.rulepack = json.loads(rulepack_path.read_text(encoding="utf-8"))
        self.execution_rulepack_path = execution_rulepack_path
        self.execution_rulepack = json.loads(execution_rulepack_path.read_text(encoding="utf-8"))

    def compile(self, task: StandardTaskSpec) -> RequirementProfile:
        haystack = " ".join(
            value
            for value in [task.topic, task.disclosure_requirement or "", task.official_text, task.data_type or ""]
            if value
        ).casefold()
        requirement_kind = self._kind(task, haystack)
        execution_rule = self._execution_rule(haystack)
        topic_terms = self._topic_terms(task.topic)
        topic_anchor_terms = self._topic_anchor_terms(task.topic, topic_terms)
        aliases: list[str] = []
        required_dimension_names: list[str] = []
        for concept in self.rulepack.get("concepts", []):
            if any(pattern.casefold() in haystack for pattern in concept.get("patterns", [])):
                aliases.extend(concept.get("aliases", []))
                required_dimension_names.extend(concept.get("required_dimensions", []))
        exact_terms = self._exact_terms(task.official_text)
        required_dimensions = self._dimensions(haystack, required_dimension_names)
        if execution_rule:
            required_dimensions = {
                name: self._unique(definition.get("aliases", []))
                for name, definition in execution_rule.get("dimensions", {}).items()
            }
        absence_patterns: list[str] = []
        positive_patterns: list[str] = []
        applicability_rule = None
        if requirement_kind == "conditional_absence":
            control = self._control_kind(haystack)
            aliases.extend(self.rulepack["controls"][control]["aliases"])
            absence_patterns = self.rulepack["controls"][control]["absence_patterns"]
            positive_patterns = self.rulepack["controls"][control]["positive_patterns"]
            applicability_rule = f"applicable_only_when_{control}_is_explicitly_absent"
        evidence_types = {
            "conditional_absence": ["paragraph_atom", "list_item_atom", "table_row_atom"],
            "table_bundle": ["table_region_atom", "table_row_atom", "logical_table_region_atom", "logical_table_row_atom"],
            "scalar": ["table_cell_atom", "table_row_atom", "paragraph_atom"],
            "narrative": ["paragraph_atom", "list_item_atom"],
        }[requirement_kind]
        disclosure_type = self._disclosure_type(requirement_kind, haystack)
        concept_id = str((execution_rule or {}).get("concept_id") or f"{task.topic.casefold().replace(' ', '_')}:{disclosure_type}")
        generic_measure_aliases = self._generic_measure_aliases(task, exact_terms, aliases)
        measure_aliases = self._unique((execution_rule or {}).get("measure_aliases", generic_measure_aliases))
        negative_aliases = self._unique((execution_rule or {}).get("negative_aliases", []))
        unit_families = self._unique(
            (execution_rule or {}).get("unit_families", self._infer_unit_families(haystack))
        )
        slot_dimensions = (execution_rule or {}).get("dimensions") or {
            name: {"aliases": values, "minimum_distinct": 1, "relation": "same_table"}
            for name, values in required_dimensions.items()
        }
        required_numeric = bool((execution_rule or {}).get("required_numeric")) or (
            execution_rule is None and disclosure_type in {"quantitative_table", "quantitative_scalar"}
        )
        required_period = bool((execution_rule or {}).get("required_period")) or (
            execution_rule is None
            and disclosure_type in {"quantitative_table", "quantitative_scalar"}
            and self._requires_explicit_period(haystack)
        )
        required_slots = self._required_slots(
            requirement_kind=requirement_kind,
            disclosure_type=disclosure_type,
            measure_aliases=measure_aliases,
            dimensions=slot_dimensions,
            unit_families=unit_families,
            required_numeric=required_numeric,
            required_period=required_period,
        )
        all_terms = self._unique([*measure_aliases, *aliases, *topic_terms, *exact_terms])
        execution_strategy = (
            "conditional_rule"
            if requirement_kind == "conditional_absence"
            else "specialized_rule"
            if execution_rule
            else "generic_local_rule"
        )
        compiler_notes = []
        if execution_strategy == "generic_local_rule":
            compiler_notes.append(
                "No specialized disclosure rule matched; concept, value, unit and dimension slots were inferred locally."
            )
        return RequirementProfile(
            requirement_id=task.task_id,
            sequence=task.sequence,
            framework=task.framework,
            framework_version=task.framework_version,
            topic_standard=task.topic_standard,
            topic=task.topic,
            data_point_id=task.data_point_id,
            requirement_kind=requirement_kind,
            execution_strategy=execution_strategy,
            compiler_notes=compiler_notes,
            disclosure_type=disclosure_type,
            concept_id=concept_id,
            official_text=task.official_text,
            query_text=" | ".join(all_terms),
            exact_terms=exact_terms,
            alias_terms=self._unique(aliases),
            topic_terms=topic_terms,
            topic_anchor_terms=topic_anchor_terms,
            required_dimensions=required_dimensions,
            required_slots=required_slots,
            measure_aliases=measure_aliases,
            negative_aliases=negative_aliases,
            unit_families=unit_families,
            multiplicity=(execution_rule or {}).get(
                "multiplicity",
                "table_bundle" if requirement_kind == "table_bundle" else "single_best",
            ),
            requires_dimension_intersection=bool((execution_rule or {}).get("requires_dimension_intersection")),
            allow_explicit_zero_statement=bool((execution_rule or {}).get("allow_explicit_zero_statement")),
            period_policy="reporting_period_preferred" if required_period else "any_period",
            scope_policy="reporting_boundary_preferred" if requirement_kind in {"table_bundle", "scalar"} else "preserve_all",
            evidence_types=evidence_types,
            absence_patterns=absence_patterns,
            positive_patterns=positive_patterns,
            applicability_rule=applicability_rule,
            source_sheet=task.source_sheet,
            source_row=task.source_row,
            source_fields=task.source_fields,
            rulepack_version=f"{self.rulepack['version']}+{self.execution_rulepack['version']}",
        )

    def compile_all(self, tasks: list[StandardTaskSpec]) -> list[RequirementProfile]:
        profiles = [self.compile(task) for task in tasks]
        ids = [profile.requirement_id for profile in profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique inside one task set")
        return profiles

    @staticmethod
    def _kind(task: StandardTaskSpec, haystack: str) -> str:
        if "mdr_no_" in haystack or re.search(r"\bif\b.*\bnot\b.*\b(adopted|set)\b", haystack):
            return "conditional_absence"
        if "table" in (task.data_type or "").casefold() or "[table]" in haystack:
            return "table_bundle"
        if any(item in (task.data_type or "").casefold() for item in ("integer", "decimal", "percentage", "monetary")):
            return "scalar"
        return "narrative"

    def _topic_terms(self, topic: str) -> list[str]:
        normalized = topic.casefold().strip()
        terms = [topic]
        for key, aliases in self.rulepack.get("topics", {}).items():
            if key.casefold() == normalized or key.casefold() in normalized:
                terms.extend(aliases)
        return self._unique(terms)

    def _topic_anchor_terms(self, topic: str, topic_terms: list[str]) -> list[str]:
        weak_terms = {
            "water", "resource use", "employees", "employee", "員工", "员工", "僱員", "雇员",
            "勞動力", "劳动力", "社區", "社区", "客戶", "客户", "合規", "合规",
        }
        anchors = [term for term in topic_terms if term.casefold().strip() not in weak_terms]
        return self._unique([topic, *anchors])

    def _dimensions(self, haystack: str, required_names: list[str]) -> dict[str, list[str]]:
        output: dict[str, list[str]] = {}
        definitions = self.rulepack.get("dimensions", {})
        for name in self._unique(required_names):
            definition = definitions.get(name)
            if definition:
                output[name] = self._unique(definition.get("aliases", []))
        if output:
            return output
        for name, definition in definitions.items():
            if definition.get("generic_detection", False) and any(
                trigger.casefold() in haystack for trigger in definition.get("triggers", [])
            ):
                output[name] = self._unique(definition.get("aliases", []))
        return output

    def _execution_rule(self, haystack: str) -> dict[str, Any] | None:
        matches = [
            rule
            for rule in self.execution_rulepack.get("rules", [])
            if any(pattern.casefold() in haystack for pattern in rule.get("patterns", []))
        ]
        if not matches:
            return None
        return max(
            matches,
            key=lambda rule: max(
                (len(pattern) for pattern in rule.get("patterns", []) if pattern.casefold() in haystack),
                default=0,
            ),
        )

    @staticmethod
    def _disclosure_type(requirement_kind: str, haystack: str) -> str:
        if requirement_kind == "table_bundle":
            return "quantitative_table"
        if requirement_kind == "scalar":
            return "quantitative_scalar"
        if requirement_kind == "conditional_absence":
            control = RequirementProfileCompiler._control_kind(haystack)
            return control
        return "narrative"

    @staticmethod
    def _required_slots(
        *,
        requirement_kind: str,
        disclosure_type: str,
        measure_aliases: list[str],
        dimensions: dict[str, dict[str, Any]],
        unit_families: list[str],
        required_numeric: bool,
        required_period: bool,
    ) -> list[RequirementSlot]:
        slots = [
            RequirementSlot(
                slot_id="concept",
                role="concept",
                aliases=measure_aliases,
                description="The evidence must measure or state the requested concept.",
            )
        ]
        if required_numeric:
            slots.append(
                RequirementSlot(
                    slot_id="value",
                    role="value",
                    value_types=["number", "explicit_zero"],
                    description="At least one reported value, not merely a plan or index reference.",
                )
            )
        if unit_families:
            slots.append(
                RequirementSlot(
                    slot_id="unit",
                    role="unit",
                    value_types=unit_families,
                    description="A unit compatible with the requested measure.",
                )
            )
        if required_period:
            slots.append(
                RequirementSlot(
                    slot_id="period",
                    role="period",
                    value_types=["year", "reporting_period"],
                    description="The reporting period must be identifiable.",
                )
            )
        for name, definition in dimensions.items():
            slots.append(
                RequirementSlot(
                    slot_id=f"dimension:{name}",
                    role="dimension",
                    aliases=definition.get("aliases", []),
                    minimum_distinct=int(definition.get("minimum_distinct", 1)),
                    relation=definition.get("relation", "same_table"),
                    description=f"Required disclosure dimension: {name}.",
                )
            )
        if requirement_kind == "conditional_absence":
            slots.append(
                RequirementSlot(
                    slot_id=f"statement:{disclosure_type}",
                    role="statement",
                    aliases=[],
                    description="A topic-specific positive or explicit-absence statement is required.",
                )
            )
        return slots

    @staticmethod
    def _control_kind(haystack: str) -> str:
        if any(term in haystack for term in ("target", "mdr-t", "mdr_no_t")):
            return "target"
        if any(term in haystack for term in ("action", "mdr-a", "mdr_no_a")):
            return "action"
        return "policy"

    def _exact_terms(self, value: str) -> list[str]:
        words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", value.casefold())
        stop = set(self.rulepack.get("english_stopwords", []))
        important = [word for word in words if word not in stop and len(word) >= 4]
        phrases = []
        for size in (3, 2):
            for index in range(max(0, len(important) - size + 1)):
                phrase = " ".join(important[index : index + size])
                if len(phrase) <= 64:
                    phrases.append(phrase)
        return self._unique([*phrases[:8], *important[:12]])

    def _generic_measure_aliases(
        self,
        task: StandardTaskSpec,
        exact_terms: list[str],
        concept_aliases: list[str],
    ) -> list[str]:
        official = re.sub(r"\[[^\]]+\]", " ", task.official_text)
        segments = [
            item.strip(" -:;,.[]()")
            for item in re.split(r"\s+-\s+|[;；]", official)
            if item.strip(" -:;,.[]()")
        ]
        useful_segments = [item for item in segments if 3 <= len(item) <= 120]
        return self._unique([*concept_aliases, *useful_segments[-2:], *exact_terms[:12]])

    @staticmethod
    def _infer_unit_families(haystack: str) -> list[str]:
        definitions = {
            "percent": ("percentage", "percent", "rate", "ratio", "proportion", "%"),
            "count_person": ("employees", "workers", "people", "persons", "headcount"),
            "count_incident": ("incidents", "cases", "complaints", "fatalities", "injuries"),
            "hours": ("hours", "training time"),
            "days": ("days", "working days"),
            "emissions": ("ghg", "co2e", "greenhouse gas", "carbon emissions"),
            "energy": ("energy", "electricity", "fuel consumption"),
            "mass": ("mass", "weight", "waste", "substances"),
            "volume": ("water", "discharge", "withdrawal", "consumption volume"),
            "monetary": ("monetary", "price", "cost", "revenue", "expenditure"),
            "area": ("area", "hectares", "land use"),
        }
        return [family for family, terms in definitions.items() if any(term in haystack for term in terms)]

    @staticmethod
    def _requires_explicit_period(haystack: str) -> bool:
        return any(
            term in haystack
            for term in ("reporting period", "reporting year", "current year", "annual", "year-on-year", "by year")
        )

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if str(value).strip()))
