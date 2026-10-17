from __future__ import annotations

import json
from importlib.resources import files

from esg_standard_packages.contracts import (
    CompiledStandardPackage,
    ElementDefinition,
    MetricDefinition,
    SemanticConcept,
)

from esg_targeted.contracts import MetricQuery, QueryIntent
from esg_targeted.retrieval.fact_pattern import normalize_match_text


CONTEXT_CATEGORIES = {"measurement method", "reporting boundary", "reporting period"}
ROLE_CATEGORY_PREFIX = "role:"
class MetricQueryCompiler:
    """Compile a reusable retrieval intent from standard semantics."""

    def __init__(self) -> None:
        resource = files("esg_targeted").joinpath("resources/esg_lexicon.json")
        self.lexicon: dict[str, list[str]] = json.loads(resource.read_text(encoding="utf-8"))

    def compile(
        self,
        package: CompiledStandardPackage,
        metric: MetricDefinition,
    ) -> MetricQuery:
        elements_by_id = {item.element_id: item for item in package.elements}
        element_ids = package.elements_by_metric[metric.metric_id]
        elements = [elements_by_id[item_id] for item_id in element_ids]
        concepts_by_id = {item.concept_id: item for item in package.concepts}

        semantic_parts = [
            metric.source_datapoint_id,
            metric.labels.get("zh", ""),
            metric.labels.get("en", ""),
            metric.description,
            metric.reporting_requirement,
            metric.paragraph,
        ]
        for element in elements:
            semantic_parts.extend(
                [
                    element.element_code,
                    element.labels.get("zh", ""),
                    element.labels.get("en", ""),
                    element.description,
                    str(element.value_contract.fixed_value or ""),
                ]
            )
        referenced_concept_ids = {
            *(concept_id for group in metric.subject_concept_groups for concept_id in group),
            *metric.context_concept_ids,
            *metric.excluded_concept_ids,
            *(concept_id for element in elements for concept_id in element.concept_ids),
            *(
                concept_id
                for element in elements
                for concept_id in element.value_concept_root_ids
            ),
        }
        positive_concept_ids = {
            *(concept_id for group in metric.subject_concept_groups for concept_id in group),
            *(concept_id for element in elements for concept_id in element.concept_ids),
            *(
                concept_id
                for element in elements
                for concept_id in element.value_concept_root_ids
            ),
        }
        inherited_excluded_concept_ids = {
            excluded_id
            for concept_id in positive_concept_ids
            if (concept := concepts_by_id.get(concept_id)) is not None
            for excluded_id in concept.excluded_concept_ids
        }
        active_applicability_context_ids = self._active_applicability_context_ids(
            package,
            positive_concept_ids,
        )
        excluded_concept_ids = (
            set(metric.excluded_concept_ids) | set(inherited_excluded_concept_ids)
        ) - positive_concept_ids - active_applicability_context_ids
        for concept_id in sorted(referenced_concept_ids):
            concept = concepts_by_id.get(concept_id)
            if concept:
                semantic_parts.extend(
                    [*concept.labels.values(), concept.description]
                )

        base_text = "\n".join(part.strip() for part in semantic_parts if part and part.strip())
        subject_parts = [
            metric.labels.get("zh", ""),
            metric.labels.get("en", ""),
        ]
        subject_text = "\n".join(part for part in subject_parts if part)
        matched = self._matched_categories(subject_text)
        context_categories = self._matched_categories(base_text) & CONTEXT_CATEGORIES
        topic_categories = {
            category
            for category in matched
            if category not in CONTEXT_CATEGORIES
            and not category.startswith(ROLE_CATEGORY_PREFIX)
        }
        topic_groups = self._subject_concept_groups(package, metric)
        if not topic_groups:
            topic_groups = [
                self._group_terms(category) for category in sorted(topic_categories)
            ]
        role_groups = self._role_groups(package, elements)
        topic_fingerprints = {
            frozenset(normalize_match_text(term) for term in group if term.strip())
            for group in topic_groups
        }
        role_groups = [
            group
            for group in role_groups
            if frozenset(
                normalize_match_text(term) for term in group if term.strip()
            )
            not in topic_fingerprints
        ]
        must_groups = [*topic_groups, *role_groups]
        context_terms = sorted(
            set(self._terms_for(context_categories))
            | set(self._concept_terms(package, metric.context_concept_ids))
        )
        should_terms = sorted(
            {
                term
                for group in [*topic_groups, *role_groups]
                for term in group
            }
        )
        must_not_terms = sorted(
            set(
                self._concept_terms(
                    package,
                    sorted(excluded_concept_ids),
                )
            )
            | set(
                self._exclusive_applicability_terms(
                    package,
                    active_context_ids=active_applicability_context_ids,
                    excluded_context_ids=excluded_concept_ids,
                )
            )
        )
        expected_types = self._expected_candidate_types(metric)

        lexical_parts = [
            metric.labels.get("zh", ""),
            metric.labels.get("en", ""),
            " | ".join(should_terms),
        ]
        lexical_text = "\n".join(
            part.strip() for part in lexical_parts if part and part.strip()
        )
        semantic_parts = [
            metric.labels.get("zh", ""),
            metric.labels.get("en", ""),
            metric.description,
            " | ".join(should_terms),
        ]
        semantic_text = "\n".join(
            part.strip() for part in semantic_parts if part and part.strip()
        )
        expanded_terms = sorted(set(should_terms + context_terms))
        query_text = base_text
        if expanded_terms:
            query_text += "\n检索扩展: " + " | ".join(expanded_terms)
        return MetricQuery(
            metric_id=metric.metric_id,
            query_text=query_text,
            lexical_text=lexical_text,
            semantic_text=semantic_text,
            lexical_terms=expanded_terms,
            data_class=metric.data_class.value,
            element_ids=element_ids,
            intent=QueryIntent(
                topic_term_groups=topic_groups,
                role_term_groups=role_groups,
                must_term_groups=must_groups,
                should_terms=should_terms,
                must_not_terms=must_not_terms,
                context_terms=context_terms,
                expected_candidate_types=expected_types,
            ),
        )

    def _matched_categories(self, text: str) -> set[str]:
        lowered = text.lower()
        matched = set()
        for anchor, synonyms in self.lexicon.items():
            if anchor.lower() in lowered or any(item.lower() in lowered for item in synonyms):
                matched.add(anchor)
        return matched

    def _terms_for(self, categories: set[str]) -> list[str]:
        terms = set()
        for category in categories:
            terms.add(category)
            terms.update(self.lexicon.get(category, []))
        return sorted(terms)

    def _group_terms(self, category: str) -> list[str]:
        terms = set(self.lexicon.get(category, []))
        if not category.startswith(ROLE_CATEGORY_PREFIX):
            terms.add(category)
        return sorted(terms)

    def _role_groups(
        self,
        package: CompiledStandardPackage,
        elements: list[ElementDefinition],
    ) -> list[list[str]]:
        groups = []
        seen: set[tuple[str, ...]] = set()
        for element in elements:
            fixed_value = element.value_contract.fixed_value
            if not isinstance(fixed_value, str):
                continue
            group = self._concept_terms(package, element.concept_ids)
            if not group:
                category = f"{ROLE_CATEGORY_PREFIX}{fixed_value}"
                if category not in self.lexicon:
                    continue
                group = self._group_terms(category)
            fingerprint = tuple(group)
            if group and fingerprint not in seen:
                groups.append(group)
                seen.add(fingerprint)
        return groups

    def _subject_concept_groups(
        self,
        package: CompiledStandardPackage,
        metric: MetricDefinition,
    ) -> list[list[str]]:
        groups = []
        active_context_ids = {
            *(concept_id for group in metric.subject_concept_groups for concept_id in group),
            *metric.context_concept_ids,
        }
        for concept_ids in metric.subject_concept_groups:
            terms = self._concept_terms(
                package,
                concept_ids,
                include_descendants=True,
                active_context_concept_ids=active_context_ids,
            )
            if terms:
                groups.append(terms)
        return groups

    @staticmethod
    def _active_applicability_context_ids(
        package: CompiledStandardPackage,
        positive_concept_ids: set[str],
    ) -> set[str]:
        """Return the active members of package-defined exclusive contexts.

        A standard package expresses medium/topic applicability through concept
        relations, not through clause-specific Python.  For E2-4 this yields air,
        water or soil; another package can define a different exclusive family
        without changing the retrieval implementation.
        """

        applicability_contexts = {
            context_id
            for concept in package.concepts
            for context_id in concept.applicable_context_concept_ids
        }
        for concept in package.concepts:
            if concept.concept_id in applicability_contexts:
                applicability_contexts.update(concept.excluded_concept_ids)
        return set(positive_concept_ids).intersection(applicability_contexts)

    @staticmethod
    def _exclusive_applicability_terms(
        package: CompiledStandardPackage,
        *,
        active_context_ids: set[str],
        excluded_context_ids: set[str],
    ) -> list[str]:
        """Compile scoped concepts from opposite contexts into hard negatives.

        Merely omitting COD/BOD from an air query is insufficient: the generic
        parent term ``pollutant`` can still retrieve a row labelled only ``COD``.
        Concepts explicitly scoped to an excluded context therefore become
        negative evidence too.  A concept shared with an active context is kept,
        which prevents valid cross-medium pollutants from being over-excluded.
        """

        terms: set[str] = set()
        for concept in package.concepts:
            applicable = set(concept.applicable_context_concept_ids)
            if not applicable.intersection(excluded_context_ids):
                continue
            if applicable.intersection(active_context_ids):
                continue
            terms.update(concept.labels.values())
            terms.update(value for values in concept.aliases.values() for value in values)
            terms.update(concept.abbreviations)
            terms.update(concept.formulas)
        return sorted(term for term in terms if term.strip())

    @staticmethod
    def _concept_terms(
        package: CompiledStandardPackage,
        concept_ids: list[str],
        *,
        include_descendants: bool = False,
        active_context_concept_ids: set[str] | None = None,
    ) -> list[str]:
        concepts_by_id = {item.concept_id: item for item in package.concepts}
        applicability_universe = {
            context_id
            for concept in package.concepts
            for context_id in concept.applicable_context_concept_ids
        }
        # Applicability contexts form mutually exclusive families through the
        # package's normal exclusion relation. Include siblings which currently
        # have no scoped descendant (for example soil alongside air and water).
        applicability_family = set(applicability_universe)
        for context_id in list(applicability_universe):
            context = concepts_by_id.get(context_id)
            if context is not None:
                applicability_family.update(context.excluded_concept_ids)
        active_applicability_contexts = (
            set(active_context_concept_ids or ()) & applicability_family
        )
        children_by_parent: dict[str, set[str]] = {}
        for concept in package.concepts:
            for parent_id in concept.broader_concept_ids:
                children_by_parent.setdefault(parent_id, set()).add(concept.concept_id)

        selected: set[str] = set()

        def collect(concept_id: str) -> None:
            if concept_id in selected:
                return
            selected.add(concept_id)
            if include_descendants:
                for child_id in sorted(children_by_parent.get(concept_id, set())):
                    collect(child_id)

        for concept_id in concept_ids:
            collect(concept_id)

        terms: set[str] = set()
        for concept_id in selected:
            concept: SemanticConcept | None = concepts_by_id.get(concept_id)
            if concept is None:
                continue
            applicable_contexts = set(concept.applicable_context_concept_ids)
            if (
                active_applicability_contexts
                and applicable_contexts
                and not applicable_contexts.intersection(active_applicability_contexts)
            ):
                continue
            terms.update(concept.labels.values())
            terms.update(value for values in concept.aliases.values() for value in values)
            terms.update(concept.abbreviations)
            terms.update(concept.formulas)
        return sorted(term for term in terms if term.strip())

    @staticmethod
    def _expected_candidate_types(metric: MetricDefinition) -> list[str]:
        value_family = str(metric.value_family).lower()
        if "percentage" in value_family:
            return ["percentage"]
        if metric.data_class.value in {"quantitative", "mixed"}:
            return ["quantity", "number"]
        return []
