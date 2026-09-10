from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os
import tempfile
from typing import Iterable, TypeVar

from pydantic import BaseModel

from . import __version__
from .contracts import (
    BindingStorage,
    CodeSetDefinition,
    CompilationMetadata,
    CompiledStandardPackage,
    CoreSchemaPackage,
    MetricRelation,
    ModuleSource,
    PackageManifest,
    RecordClass,
    RelationType,
    SourceFileDigest,
)
from .errors import PackageIntegrityError
from .io import canonical_json_bytes, load_json, sha256_bytes, sha256_file, write_json


ModelT = TypeVar("ModelT", bound=BaseModel)


class StandardPackageCompiler:
    compiler_id = "extractesg.standard-package-compiler"
    compiler_version = __version__

    def load_core(self, core_path: Path) -> CoreSchemaPackage:
        return CoreSchemaPackage.model_validate(load_json(core_path))

    def load_module(self, module_dir: Path) -> tuple[ModuleSource, list[Path]]:
        manifest_path = module_dir / "manifest.json"
        manifest = PackageManifest.model_validate(load_json(manifest_path))
        files = manifest.files
        source_paths = [
            manifest_path,
            module_dir / files.metrics,
            module_dir / files.elements,
            module_dir / files.relations,
            module_dir / files.code_sets,
            module_dir / files.dimensions,
            module_dir / files.derivations,
            module_dir / files.validation_rules,
            module_dir / files.sources,
        ]
        if files.concepts:
            source_paths.append(module_dir / files.concepts)
        missing = [str(path) for path in source_paths if not path.is_file()]
        if missing:
            raise PackageIntegrityError(f"missing module source files: {missing}")

        source = ModuleSource.model_validate(
            {
                "manifest": manifest,
                "metrics": load_json(module_dir / files.metrics),
                "elements": load_json(module_dir / files.elements),
                "relations": load_json(module_dir / files.relations),
                "code_sets": load_json(module_dir / files.code_sets),
                "dimensions": load_json(module_dir / files.dimensions),
                "derivations": load_json(module_dir / files.derivations),
                "validation_rules": load_json(module_dir / files.validation_rules),
                "sources": load_json(module_dir / files.sources),
                "concepts": (
                    load_json(module_dir / files.concepts) if files.concepts else []
                ),
            }
        )
        return source, source_paths

    def compile(
        self,
        *,
        core_path: Path,
        module_dir: Path,
    ) -> CompiledStandardPackage:
        core = self.load_core(core_path)
        module, module_paths = self.load_module(module_dir)
        self._validate_integrity(core, module)

        source_files = sorted(
            [
                SourceFileDigest(
                    path=self._portable_source_path(path, core_path, module_dir),
                    sha256=sha256_file(path),
                )
                for path in [core_path, *module_paths]
            ],
            key=lambda item: item.path,
        )
        source_digest = sha256_bytes(
            canonical_json_bytes(
                [{"path": item.path, "sha256": item.sha256} for item in source_files]
            )
        )

        elements_by_metric: dict[str, list[str]] = defaultdict(list)
        for element in sorted(module.elements, key=lambda item: item.element_id):
            elements_by_metric[element.metric_id].append(element.element_id)

        compiled = CompiledStandardPackage(
            format_version=module.manifest.format_version,
            compilation=CompilationMetadata(
                compiler_id=self.compiler_id,
                compiler_version=self.compiler_version,
                source_digest=source_digest,
                source_files=source_files,
            ),
            core=core,
            manifest=module.manifest,
            metrics=sorted(module.metrics, key=lambda item: item.metric_id),
            elements=sorted(module.elements, key=lambda item: item.element_id),
            elements_by_metric=dict(sorted(elements_by_metric.items())),
            relations=sorted(module.relations, key=lambda item: item.relation_id),
            code_sets=sorted(module.code_sets, key=lambda item: item.code_set_id),
            dimensions=sorted(module.dimensions, key=lambda item: item.dimension_id),
            derivations=sorted(module.derivations, key=lambda item: item.rule_id),
            validation_rules=sorted(module.validation_rules, key=lambda item: item.rule_id),
            sources=sorted(module.sources, key=lambda item: item.source_id),
            concepts=sorted(module.concepts, key=lambda item: item.concept_id),
        )
        return CompiledStandardPackage.model_validate(compiled.model_dump(mode="json"))

    def compile_to_path(
        self,
        *,
        core_path: Path,
        module_dir: Path,
        output_path: Path,
    ) -> CompiledStandardPackage:
        compiled = self.compile(core_path=core_path, module_dir=module_dir)
        if output_path.is_file():
            existing = self.validate_compiled(output_path)
            if existing.compilation.source_digest != compiled.compilation.source_digest:
                raise PackageIntegrityError("Published package versions are immutable; publish a new version")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".publish-", suffix=".json", dir=output_path.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            write_json(temporary, compiled.model_dump(mode="json"))
            self.validate_compiled(temporary)
            temporary.replace(output_path)
        finally:
            temporary.unlink(missing_ok=True)
        from .retention import retire_older_versions
        retire_older_versions(output_path, compiled, self.validate_compiled)
        return compiled

    def validate_compiled(self, compiled_path: Path) -> CompiledStandardPackage:
        package = CompiledStandardPackage.model_validate(load_json(compiled_path))
        source_paths = [item.path for item in package.compilation.source_files]
        if len(source_paths) != len(set(source_paths)):
            raise PackageIntegrityError("compiled package contains duplicate source paths")
        expected_digest = sha256_bytes(
            canonical_json_bytes(
                [
                    {"path": item.path, "sha256": item.sha256}
                    for item in package.compilation.source_files
                ]
            )
        )
        if expected_digest != package.compilation.source_digest:
            raise PackageIntegrityError("compiled package source digest is invalid")
        self._validate_integrity(
            package.core,
            ModuleSource(
                manifest=package.manifest,
                metrics=package.metrics,
                elements=package.elements,
                relations=package.relations,
                code_sets=package.code_sets,
                dimensions=package.dimensions,
                derivations=package.derivations,
                validation_rules=package.validation_rules,
                sources=package.sources,
                concepts=package.concepts,
            ),
        )
        expected_index: dict[str, list[str]] = defaultdict(list)
        for element in sorted(package.elements, key=lambda item: item.element_id):
            expected_index[element.metric_id].append(element.element_id)
        if dict(sorted(expected_index.items())) != package.elements_by_metric:
            raise PackageIntegrityError("compiled package element index is inconsistent")
        return package

    def write_schemas(self, output_dir: Path) -> None:
        write_json(output_dir / "core-package.schema.json", CoreSchemaPackage.model_json_schema())
        write_json(output_dir / "module-source.schema.json", ModuleSource.model_json_schema())
        write_json(
            output_dir / "compiled-package.schema.json",
            CompiledStandardPackage.model_json_schema(),
        )

    def _validate_integrity(self, core: CoreSchemaPackage, module: ModuleSource) -> None:
        manifest = module.manifest
        if manifest.core_schema_id != core.core_schema_id:
            raise PackageIntegrityError("module references a different core schema id")
        if manifest.core_schema_version != core.core_schema_version:
            raise PackageIntegrityError("module references a different core schema version")

        self._assert_unique("metric", [item.metric_id for item in module.metrics])
        self._assert_unique("element", [item.element_id for item in module.elements])
        self._assert_unique("relation", [item.relation_id for item in module.relations])
        self._assert_unique("derivation", [item.rule_id for item in module.derivations])
        self._assert_unique("validation rule", [item.rule_id for item in module.validation_rules])
        self._assert_unique("source", [item.source_id for item in module.sources])
        self._assert_unique("code set", [item.code_set_id for item in module.code_sets])
        self._assert_unique("dimension", [item.dimension_id for item in module.dimensions])
        self._assert_unique("concept", [item.concept_id for item in module.concepts])

        metric_ids = {item.metric_id for item in module.metrics}
        element_ids = {item.element_id for item in module.elements}
        source_ids = {item.source_id for item in module.sources}
        concept_ids = {item.concept_id for item in module.concepts}
        code_sets = self._index_code_sets(core.common_code_sets, module.code_sets)
        dimensions = {
            item.dimension_id for item in [*core.common_dimensions, *module.dimensions]
        }
        record_fields = {
            record.record_type: {field.field_id for field in record.fields}
            for record in core.record_types
        }

        prefix = manifest.package_id + "."
        if manifest.format_version == "1.1" and not module.concepts:
            raise PackageIntegrityError("format 1.1 packages require semantic concepts")

        for concept in module.concepts:
            if not concept.concept_id.startswith(prefix + "concept."):
                raise PackageIntegrityError(
                    f"concept outside package namespace: {concept.concept_id}"
                )
            self._assert_refs(
                "concept source", concept.concept_id, concept.source_refs, source_ids
            )
            self._assert_refs(
                "concept relation",
                concept.concept_id,
                [
                    *concept.broader_concept_ids,
                    *concept.related_concept_ids,
                    *concept.excluded_concept_ids,
                    *concept.applicable_context_concept_ids,
                ],
                concept_ids,
            )
        self._validate_concept_hierarchy(module)

        for metric in module.metrics:
            if not metric.metric_id.startswith(prefix):
                raise PackageIntegrityError(f"metric outside package namespace: {metric.metric_id}")
            self._assert_refs("metric source", metric.metric_id, metric.source_refs, source_ids)
            self._assert_refs(
                "metric dimension",
                metric.metric_id,
                [*metric.required_dimension_ids, *metric.optional_dimension_ids],
                dimensions,
            )
            metric_concept_refs = [
                *(concept_id for group in metric.subject_concept_groups for concept_id in group),
                *metric.context_concept_ids,
                *metric.excluded_concept_ids,
            ]
            self._assert_refs(
                "metric concept", metric.metric_id, metric_concept_refs, concept_ids
            )
            if manifest.format_version == "1.1" and not metric.subject_concept_groups:
                raise PackageIntegrityError(
                    f"format 1.1 metric lacks subject concepts: {metric.metric_id}"
                )

        elements_by_metric: dict[str, int] = defaultdict(int)
        element_codes_by_metric: dict[str, set[str]] = defaultdict(set)
        for element in module.elements:
            if element.metric_id not in metric_ids:
                raise PackageIntegrityError(f"unknown element metric: {element.metric_id}")
            if not element.element_id.startswith(element.metric_id + ".element."):
                raise PackageIntegrityError(f"element id is not scoped to metric: {element.element_id}")
            if element.element_code in element_codes_by_metric[element.metric_id]:
                raise PackageIntegrityError(
                    f"duplicate element code {element.element_code} in {element.metric_id}"
                )
            element_codes_by_metric[element.metric_id].add(element.element_code)
            elements_by_metric[element.metric_id] += 1
            self._assert_refs("element source", element.element_id, element.source_refs, source_ids)
            self._assert_refs(
                "element concept",
                element.element_id,
                [*element.concept_ids, *element.value_concept_root_ids],
                concept_ids,
            )

            contract = element.value_contract
            if contract.code_set_id and contract.code_set_id not in code_sets:
                raise PackageIntegrityError(
                    f"unknown code set {contract.code_set_id} in {element.element_id}"
                )
            binding = element.binding
            if binding.record_type not in record_fields:
                raise PackageIntegrityError(f"unknown record type in {element.element_id}")
            if binding.storage is BindingStorage.CORE_FIELD:
                if binding.target not in record_fields[binding.record_type]:
                    raise PackageIntegrityError(
                        f"unknown core field {binding.target} in {element.element_id}"
                    )
            elif binding.storage is BindingStorage.DIMENSION:
                if binding.target not in dimensions:
                    raise PackageIntegrityError(
                        f"unknown dimension {binding.target} in {element.element_id}"
                    )

        metrics_without_elements = sorted(metric_ids - set(elements_by_metric))
        if metrics_without_elements:
            raise PackageIntegrityError(f"metrics without elements: {metrics_without_elements}")

        for relation in module.relations:
            refs = [
                *relation.source_metric_ids,
                *relation.target_metric_ids,
                *relation.member_metric_ids,
            ]
            self._assert_refs("relation metric", relation.relation_id, refs, metric_ids)
            self._assert_refs("relation source", relation.relation_id, relation.source_refs, source_ids)
            self._validate_relation_shape(relation)

        for derivation in module.derivations:
            if derivation.target_metric_id not in metric_ids:
                raise PackageIntegrityError(
                    f"unknown derivation target: {derivation.target_metric_id}"
                )
            if len(derivation.operands) < 2:
                raise PackageIntegrityError(f"derivation needs two operands: {derivation.rule_id}")
            self._assert_refs(
                "derivation operand metric",
                derivation.rule_id,
                [
                    operand.source_metric_id
                    for operand in derivation.operands
                    if operand.source_metric_id is not None
                ],
                metric_ids,
            )
            self._assert_refs(
                "derivation source", derivation.rule_id, derivation.source_refs, source_ids
            )

        valid_rule_targets = metric_ids | element_ids | {manifest.package_id}
        for rule in module.validation_rules:
            if rule.target not in valid_rule_targets:
                raise PackageIntegrityError(f"unknown validation target: {rule.target}")

    @staticmethod
    def _portable_source_path(path: Path, core_path: Path, module_dir: Path) -> str:
        if path == core_path:
            return "core/core.json"
        return f"module/{path.relative_to(module_dir).as_posix()}"

    @staticmethod
    def _assert_unique(label: str, values: Iterable[object]) -> None:
        values = list(values)
        if len(values) != len(set(values)):
            raise PackageIntegrityError(f"duplicate {label} id")

    @staticmethod
    def _assert_refs(label: str, owner: str, refs: Iterable[str], valid: set[str]) -> None:
        unknown = sorted(set(refs) - valid)
        if unknown:
            raise PackageIntegrityError(f"unknown {label} on {owner}: {unknown}")

    @staticmethod
    def _index_code_sets(
        core_sets: list[CodeSetDefinition], module_sets: list[CodeSetDefinition]
    ) -> dict[str, CodeSetDefinition]:
        all_sets = [*core_sets, *module_sets]
        result = {item.code_set_id: item for item in all_sets}
        if len(result) != len(all_sets):
            raise PackageIntegrityError("module code set shadows a core code set")
        return result

    @staticmethod
    def _validate_relation_shape(relation: MetricRelation) -> None:
        if relation.relation_type is RelationType.ALTERNATIVE_GROUP:
            if len(relation.member_metric_ids) < 2:
                raise PackageIntegrityError(
                    f"alternative group needs at least two members: {relation.relation_id}"
                )
            if relation.source_metric_ids or relation.target_metric_ids:
                raise PackageIntegrityError(
                    f"alternative group must use member_metric_ids: {relation.relation_id}"
                )
        elif relation.relation_type is RelationType.CONDITIONAL_ACTIVATION:
            if not relation.target_metric_ids or relation.activation_predicate is None:
                raise PackageIntegrityError(
                    f"conditional relation lacks target or predicate: {relation.relation_id}"
                )
        elif not relation.source_metric_ids or not relation.target_metric_ids:
            raise PackageIntegrityError(
                f"relation needs source and target metrics: {relation.relation_id}"
            )

    @staticmethod
    def _validate_concept_hierarchy(module: ModuleSource) -> None:
        parents = {
            concept.concept_id: set(concept.broader_concept_ids)
            for concept in module.concepts
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(concept_id: str) -> None:
            if concept_id in visiting:
                raise PackageIntegrityError(
                    f"semantic concept hierarchy contains a cycle at {concept_id}"
                )
            if concept_id in visited:
                return
            visiting.add(concept_id)
            for parent_id in parents.get(concept_id, set()):
                visit(parent_id)
            visiting.remove(concept_id)
            visited.add(concept_id)

        for concept_id in parents:
            visit(concept_id)
