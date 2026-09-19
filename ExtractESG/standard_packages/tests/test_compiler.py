from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.errors import PackageIntegrityError


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core/1.0.0/core.json"
MODULE = ROOT / "packages/esrs/2023-set1/e2-4/1.0.0"
COMPILED = ROOT / "dist/esrs.2023-set1.e2-4/1.0.0/package.json"
CORE_1_1 = ROOT / "core/1.1.0/core.json"
E1_5_MODULE = ROOT / "packages/esrs/2023-set1/e1-5/1.0.0"
E1_6_MODULE = ROOT / "packages/esrs/2023-set1/e1-6/1.0.0"
E1_6_130_MODULE = ROOT / "packages/esrs/2023-set1/e1-6/1.3.0"
E1_5_COMPILED = ROOT / "dist/.retired/esrs.2023-set1.e1-5/1.0.0/package.json"
E1_6_COMPILED = ROOT / "dist/.retired/esrs.2023-set1.e1-6/1.0.0/package.json"


def test_e2_4_package_compiles_with_expected_contract() -> None:
    package = StandardPackageCompiler().compile(core_path=CORE, module_dir=MODULE)

    assert package.manifest.package_id == "esrs.2023-set1.e2-4"
    assert package.manifest.package_version == "1.0.0"
    assert package.manifest.status == "draft"
    assert len(package.metrics) == 20
    assert len(package.elements) == 127
    assert len(package.elements_by_metric) == 20
    assert package.format_version == "1.1"
    assert len(package.concepts) == 36
    assert len(package.relations) == 3
    assert len(package.derivations) == 4


def test_dp02_is_bound_without_e2_specific_core_columns() -> None:
    package = StandardPackageCompiler().compile(core_path=CORE, module_dir=MODULE)
    prefix = "esrs.2023-set1.e2-4.dp02.element."
    elements = {
        item.element_code: item
        for item in package.elements
        if item.element_id.startswith(prefix)
    }

    assert elements["pollution_medium"].binding.storage == "dimension"
    assert elements["pollution_medium"].value_contract.fixed_value == "air"
    assert elements["emission_amount"].binding.target == "value_raw"
    assert elements["emission_amount"].value_contract.primary_type == "decimal"
    assert elements["mass_unit"].binding.target == "unit_raw"
    assert elements["mass_unit"].value_contract.primary_type == "identifier"
    assert elements["consolidation_scope"].binding.storage == "attribute"
    assert elements["pollutant"].value_concept_root_ids == [
        "esrs.2023-set1.e2-4.concept.pollutant"
    ]


def test_dp02_semantic_vocabulary_supports_specific_pollutants() -> None:
    package = StandardPackageCompiler().compile(core_path=CORE, module_dir=MODULE)
    concepts = {item.concept_id: item for item in package.concepts}
    sulfur_dioxide = concepts[
        "esrs.2023-set1.e2-4.concept.pollutant.sulfur-dioxide"
    ]
    lexical_variants = {
        *sulfur_dioxide.labels.values(),
        *(item for values in sulfur_dioxide.aliases.values() for item in values),
        *sulfur_dioxide.abbreviations,
        *sulfur_dioxide.formulas,
    }
    assert {"二氧化硫", "SO2", "SO₂", "Sulfur dioxide"} <= lexical_variants
    assert sulfur_dioxide.broader_concept_ids == [
        "esrs.2023-set1.e2-4.concept.pollutant"
    ]
    assert sulfur_dioxide.applicable_context_concept_ids == [
        "esrs.2023-set1.e2-4.concept.air-pollution"
    ]


def test_unknown_and_cyclic_concept_references_are_rejected(tmp_path: Path) -> None:
    module_copy = tmp_path / "unknown"
    shutil.copytree(MODULE, module_copy)
    metrics_path = module_copy / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics[0]["subject_concept_groups"] = [["esrs.unknown.concept"]]
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PackageIntegrityError, match="unknown metric concept"):
        StandardPackageCompiler().compile(core_path=CORE, module_dir=module_copy)

    cycle_copy = tmp_path / "cycle"
    shutil.copytree(MODULE, cycle_copy)
    concepts_path = cycle_copy / "concepts.json"
    concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
    concepts[0]["broader_concept_ids"] = [concepts[1]["concept_id"]]
    concepts[1]["broader_concept_ids"] = [concepts[0]["concept_id"]]
    concepts_path.write_text(json.dumps(concepts, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PackageIntegrityError, match="hierarchy contains a cycle"):
        StandardPackageCompiler().compile(core_path=CORE, module_dir=cycle_copy)


def test_microplastic_relation_is_alternative_not_arithmetic() -> None:
    package = StandardPackageCompiler().compile(core_path=CORE, module_dir=MODULE)
    relation = next(
        item
        for item in package.relations
        if item.relation_id.endswith("microplastics-alternative-group")
    )

    assert relation.relation_type == "alternative_group"
    assert relation.member_metric_ids == [
        "esrs.2023-set1.e2-4.dp05",
        "esrs.2023-set1.e2-4.dp06",
        "esrs.2023-set1.e2-4.dp07",
    ]
    assert all(item.target_metric_id != "esrs.2023-set1.e2-4.dp05" for item in package.derivations)


def test_compilation_is_byte_deterministic(tmp_path: Path) -> None:
    compiler = StandardPackageCompiler()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    compiler.compile_to_path(core_path=CORE, module_dir=MODULE, output_path=first)
    compiler.compile_to_path(core_path=CORE, module_dir=MODULE, output_path=second)

    assert first.read_bytes() == second.read_bytes()


def test_compiled_artifact_validates() -> None:
    package = StandardPackageCompiler().validate_compiled(COMPILED)
    assert package.compilation.source_digest


def test_unknown_binding_target_is_rejected(tmp_path: Path) -> None:
    module_copy = tmp_path / "module"
    shutil.copytree(MODULE, module_copy)
    elements_path = module_copy / "elements.json"
    elements = json.loads(elements_path.read_text(encoding="utf-8"))
    elements[0]["binding"] = {
        "record_type": "reporting_task",
        "storage": "core_field",
        "target": "field_that_does_not_exist",
    }
    elements_path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PackageIntegrityError, match="unknown core field"):
        StandardPackageCompiler().compile(core_path=CORE, module_dir=module_copy)


def test_unknown_relation_metric_is_rejected(tmp_path: Path) -> None:
    module_copy = tmp_path / "module"
    shutil.copytree(MODULE, module_copy)
    relations_path = module_copy / "relations.json"
    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    relations[0]["member_metric_ids"].append("esrs.2023-set1.e2-4.dp99")
    relations_path.write_text(json.dumps(relations, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PackageIntegrityError, match="unknown relation metric"):
        StandardPackageCompiler().compile(core_path=CORE, module_dir=module_copy)


def test_tampered_compiled_digest_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    payload = json.loads(COMPILED.read_text(encoding="utf-8"))
    payload["compilation"]["source_digest"] = "0" * 64
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PackageIntegrityError, match="source digest"):
        StandardPackageCompiler().validate_compiled(target)


def test_tampered_element_index_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    payload = json.loads(COMPILED.read_text(encoding="utf-8"))
    payload["elements_by_metric"]["esrs.2023-set1.e2-4.dp02"] = []
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PackageIntegrityError, match="element index"):
        StandardPackageCompiler().validate_compiled(target)


@pytest.mark.parametrize(
    ("module", "package_id", "metric_count", "element_count", "concept_count"),
    [
        (E1_5_MODULE, "esrs.2023-set1.e1-5", 23, 167, 22),
        (E1_6_MODULE, "esrs.2023-set1.e1-6", 35, 182, 34),
    ],
)
def test_e1_packages_compile_with_complete_datapoint_contracts(
    module: Path,
    package_id: str,
    metric_count: int,
    element_count: int,
    concept_count: int,
) -> None:
    package = StandardPackageCompiler().compile(core_path=CORE_1_1, module_dir=module)

    assert package.manifest.package_id == package_id
    assert package.manifest.core_schema_version == "1.1.0"
    assert package.manifest.status == "draft"
    assert len(package.metrics) == metric_count
    assert len(package.elements) == element_count
    assert len(package.elements_by_metric) == metric_count
    assert len(package.concepts) == concept_count
    assert {item.source_datapoint_id for item in package.metrics} == {
        f"{package.manifest.standard.disclosure_requirement}_{index:02d}"
        for index in range(1, metric_count + 1)
    }


def test_core_1_1_distinguishes_energy_ghg_currency_and_intensity_units() -> None:
    core = StandardPackageCompiler().load_core(CORE_1_1)
    units = {item.unit_id: item for item in core.common_units}

    assert units["extractesg.core.unit.megawatt-hour"].dimension.endswith(".energy")
    assert units["extractesg.core.unit.tonne-co2e"].dimension.endswith(".ghg-emissions")
    assert units["extractesg.core.unit.eur"].dimension.endswith(".currency")
    assert units["extractesg.core.unit.mwh-per-monetary-unit"].dimension.endswith(
        ".energy-intensity"
    )
    assert units["extractesg.core.unit.tco2e-per-monetary-unit"].dimension.endswith(
        ".ghg-intensity"
    )


def test_e1_5_energy_intensity_corrects_non_authoritative_ig3_type() -> None:
    package = StandardPackageCompiler().compile(
        core_path=CORE_1_1, module_dir=E1_5_MODULE
    )
    metric = next(item for item in package.metrics if item.source_datapoint_id == "E1-5_18")
    elements = {
        item.element_code: item
        for item in package.elements
        if item.metric_id == metric.metric_id
    }

    assert metric.official_data_type == "Energy intensity (MWh per monetary unit)"
    assert metric.value_family == "energy_intensity"
    assert elements["energy_intensity"].value_contract.unit_dimension.endswith(
        ".energy-intensity"
    )
    assert elements["denominator_currency"].value_contract.code_set_id == (
        "extractesg.core.codeset.currency"
    )


def test_e1_6_scope_methods_phase_in_and_alternatives_are_machine_readable() -> None:
    package = StandardPackageCompiler().compile(
        core_path=CORE_1_1, module_dir=E1_6_MODULE
    )
    dp09 = {
        item.element_code: item
        for item in package.elements
        if item.metric_id == "esrs.2023-set1.e1-6.dp09"
    }
    alternative = next(
        item for item in package.relations if item.relation_id.endswith("classification-alternative")
    )
    phase_in = next(
        item for item in package.relations if item.relation_id.endswith("phase-in-under-750")
    )

    assert dp09["ghg_scope"].value_contract.fixed_value == "scope2"
    assert dp09["scope2_method"].value_contract.fixed_value == "location_based"
    assert alternative.relation_type == "alternative_group"
    assert alternative.member_metric_ids == [
        "esrs.2023-set1.e1-6.dp04",
        "esrs.2023-set1.e1-6.dp05",
    ]
    assert "esrs.2023-set1.e1-6.dp11" in phase_in.target_metric_ids
    assert phase_in.activation_predicate.path == (
        "report_context.e1_6_scope3_total_phase_in_exempt"
    )


def test_e1_6_1_3_structure_members_are_evidenced_assertions() -> None:
    package = StandardPackageCompiler().compile(
        core_path=CORE_1_1, module_dir=E1_6_130_MODULE
    )
    elements = {
        (item.metric_id, item.element_code): item for item in package.elements
    }

    for datapoint, member_code in [
        ("dp04", "scope3_category"),
        ("dp05", "scope3_category"),
        ("dp06", "value_chain_stage"),
    ]:
        member = elements[(f"esrs.2023-set1.e1-6.{datapoint}", member_code)]
        assert member.binding.record_type.value == "qualitative_assertion"
        assert member.cardinality.maximum == 1
        assert (
            f"esrs.2023-set1.e1-6.{datapoint}", "statement"
        ) in elements


def test_new_structure_package_rejects_repeated_task_dimension(tmp_path: Path) -> None:
    module_copy = tmp_path / "e1-6-1.3.0"
    shutil.copytree(E1_6_130_MODULE, module_copy)
    elements_path = module_copy / "elements.json"
    elements = json.loads(elements_path.read_text(encoding="utf-8"))
    member = next(
        item for item in elements
        if item["metric_id"].endswith("dp04")
        and item["element_code"] == "scope3_category"
    )
    member["binding"]["record_type"] = "reporting_task"
    member["cardinality"]["maximum"] = None
    elements_path.write_text(
        json.dumps(elements, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(PackageIntegrityError, match="structure list members"):
        StandardPackageCompiler().compile(
            core_path=CORE_1_1, module_dir=module_copy
        )


def test_e1_derivations_reference_source_metrics_and_support_sum() -> None:
    e1_5 = StandardPackageCompiler().compile(
        core_path=CORE_1_1, module_dir=E1_5_MODULE
    )
    e1_6 = StandardPackageCompiler().compile(
        core_path=CORE_1_1, module_dir=E1_6_MODULE
    )
    energy_total = next(item for item in e1_5.derivations if item.target_metric_id.endswith("dp01"))
    location_total = next(item for item in e1_6.derivations if item.target_metric_id.endswith("dp12"))

    assert energy_total.operation == "sum"
    assert {item.source_metric_id for item in energy_total.operands} == {
        "esrs.2023-set1.e1-5.dp02",
        "esrs.2023-set1.e1-5.dp03",
        "esrs.2023-set1.e1-5.dp05",
    }
    assert location_total.operation == "sum"
    assert {item.source_metric_id for item in location_total.operands} == {
        "esrs.2023-set1.e1-6.dp07",
        "esrs.2023-set1.e1-6.dp09",
        "esrs.2023-set1.e1-6.dp11",
    }


@pytest.mark.parametrize("compiled", [E1_5_COMPILED, E1_6_COMPILED])
def test_e1_compiled_artifacts_validate(compiled: Path) -> None:
    assert StandardPackageCompiler().validate_compiled(compiled).compilation.source_digest


@pytest.mark.parametrize("module", [E1_5_MODULE, E1_6_MODULE])
def test_e1_compilation_is_byte_deterministic(module: Path, tmp_path: Path) -> None:
    first = tmp_path / f"{module.parent.name}-first.json"
    second = tmp_path / f"{module.parent.name}-second.json"
    compiler = StandardPackageCompiler()
    compiler.compile_to_path(core_path=CORE_1_1, module_dir=module, output_path=first)
    compiler.compile_to_path(core_path=CORE_1_1, module_dir=module, output_path=second)
    assert first.read_bytes() == second.read_bytes()
