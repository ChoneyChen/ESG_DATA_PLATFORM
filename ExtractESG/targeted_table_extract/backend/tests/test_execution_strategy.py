"""Synthetic-only strategy, authority and shared-source continuity checks."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from esg_targeted.contracts import ExtractionRequest
from esg_targeted.evidence.execution_strategy import ExecutionStrategyCompiler
from esg_targeted.evidence.selection import EvidenceSelectionCompiler
from esg_targeted.grounding.guard import GroundingGuard
from esg_targeted.report_facts import adapt_direct_core_facts
from esg_targeted.results.materializer import CoreResultMaterializer
from esg_targeted.results.validation import ResultContractValidator
from tests.test_inventory_and_guard import build_pipeline
from tests.test_workflow import FoundModel


def metric(metric_id="synthetic.materials.dp17"):
    return NS(metric_id=metric_id, data_class="quantitative", value_family="quantitative_mass",
        measurement_contract={"template": "dimensional_table", "unit_dimension": "mass"},
        cardinality={"minimum": 0, "maximum": None}, required_dimension_ids=["material"],
        identity_axes=["reporting_entity", "reporting_period", "material"])


def packet():
    return NS(source_document_id="synthetic-document", source_ir_run_id="synthetic-ir", source_ir_revision=1,
        budget={"selected_retrieval_objects": [{"kind": "table", "id": "table-a", "selected_group_ids": ["row-a"]}]},
        allowed_group_ids=["row-a"], spans=[NS(span_id="value-a", context_group_id="row-a", span_type="table_cell",
            has_table_structure=True, locators=[NS(ir_object_type="cell", ir_object_id="cell-a")])],
        candidates=[NS(span_id="value-a", candidate_type="quantity")])


def request():
    return ExtractionRequest.model_validate({"ir_run_id": "synthetic-ir", "package_id": "synthetic.package",
        "package_version": "1.0.0", "metric_ids": ["synthetic.materials.dp17"],
        "roles": {"semantic_brain": {"provider": "local_qwen", "model": "Synthetic-Qwen"},
            "specialist": {"provider": "local_nuextract", "model": "Synthetic-NuExtract"}}})


@pytest.mark.parametrize("identifier", ["synthetic.materials.dp17", "unrelated.metric.z9", "esrs.fake.dp900"])
def test_repeating_contract_routes_by_semantics_and_actual_cells_not_standard_number(identifier):
    original = metric(identifier)
    before = deepcopy(vars(original))
    route = ExecutionStrategyCompiler().compile(original, packet(), request())
    assert route.route == "table_series" and route.role == "specialist"
    assert route.provider == "local_nuextract" and route.metric_id == identifier
    assert vars(original) == before


@pytest.mark.parametrize("change", ["text", "figure", "no_candidates", "no_structure", "no_cell", "no_cell_id", "outside_group", "no_ir_identity"])
def test_table_reader_requires_actual_pinned_numeric_cells(change):
    source = packet()
    if change in {"text", "figure"}:
        source.budget["selected_retrieval_objects"][0]["kind"] = change
    elif change == "no_candidates": source.candidates = []
    elif change == "no_structure": source.spans[0].has_table_structure = False
    elif change == "no_cell": source.spans[0].locators[0].ir_object_type = "table"
    elif change == "no_cell_id": source.spans[0].locators[0].ir_object_id = ""
    elif change == "outside_group": source.allowed_group_ids = ["other-row"]
    else: source.source_ir_run_id = None
    route = ExecutionStrategyCompiler().compile(metric(), source, request())
    assert route.route == "source_agent" and route.role == "semantic_brain"


@pytest.mark.parametrize("change", ["scalar", "narrative", "no_dimensions", "no_numeric_family", "single_cardinality"])
def test_table_presence_alone_does_not_override_semantic_grain(change):
    definition = metric()
    if change == "scalar": definition.measurement_contract["template"] = "scalar"
    elif change == "narrative": definition.data_class = "qualitative"
    elif change == "no_dimensions": definition.required_dimension_ids = []
    elif change == "no_numeric_family": definition.value_family = None
    else: definition.cardinality["maximum"] = 1
    assert ExecutionStrategyCompiler().compile(definition, packet(), request()).route == "source_agent"


def test_descriptor_pins_semantic_source_and_selected_model_and_rejects_stale_checkpoint():
    compiler = ExecutionStrategyCompiler()
    definition, source, task = metric(), packet(), request()
    frozen = compiler.compile(definition, source, task, model_identity="Resolved-Synthetic-NuExtract")
    assert frozen.model == "Resolved-Synthetic-NuExtract"
    assert compiler.compile(definition, source, task, model_identity=frozen.model) == frozen
    checkpoint = {"execution_strategy": frozen.model_dump()}
    assert compiler.checkpoint_matches(checkpoint, frozen)
    assert not compiler.checkpoint_matches({}, frozen)
    source.source_ir_revision = 2
    new_source = compiler.compile(definition, source, task, model_identity=frozen.model)
    assert new_source.source_capability_digest != frozen.source_capability_digest
    assert not compiler.checkpoint_matches(checkpoint, new_source)
    source.source_ir_revision = 1
    definition.measurement_contract["unit_dimension"] = "volume"
    assert compiler.compile(definition, source, task).contract_digest != frozen.contract_digest
    task.roles.specialist.provider = "local_qwen"
    assert compiler.compile(definition, source, task).descriptor_digest != frozen.descriptor_digest


def test_descriptor_invalidates_when_actual_selected_numeric_cell_changes_at_same_ir_revision():
    compiler = ExecutionStrategyCompiler()
    original, source, task = metric(), packet(), request()
    frozen = compiler.compile(original, source, task)
    source.spans[0].locators[0].ir_object_id = "cell-b"
    changed = compiler.compile(original, source, task)
    assert changed.route == frozen.route == "table_series"
    assert changed.source_capability_digest != frozen.source_capability_digest
    assert not compiler.checkpoint_matches({"execution_strategy": frozen.model_dump()}, changed)


def test_direct_reader_can_be_declined_before_inference_without_failure_fallback():
    descriptor = ExecutionStrategyCompiler().compile(metric(), packet(), request(), permit_table_series=False)
    assert descriptor.route == "source_agent"


def test_neighbor_narrative_context_does_not_remove_actual_dimensional_table_capability():
    source = packet()
    source.budget["selected_retrieval_objects"].append({"kind": "text", "id": "table-footnote", "selected_group_ids": ["note-a"]})
    source.allowed_group_ids.append("note-a")
    assert ExecutionStrategyCompiler().compile(metric(), source, request()).route == "table_series"


@pytest.mark.parametrize("package_id,version,original,dimension", [
    ("esrs.2023-set1.e2-4", "2.1.2", "E2-4_03", "mass"),
    ("esrs.2023-set1.e3-4", "2.1.1", "E3-4_01", "volume"),
    ("esrs.2023-set1.e1-5", "2.1.2", "E1-5_09", "ratio"),
])
def test_direct_contract_retains_authoritative_quantity_type_definitions_and_identity(package_id, version, original, dimension):
    from esg_targeted.standards.catalog import StandardPackageCatalog
    from tests.helpers import standard_dist_root
    package = StandardPackageCatalog(standard_dist_root()).load(package_id, version)
    definition = next(item for item in package.metrics if item.source_datapoint_id == original)
    before = definition.model_dump(mode="json")
    card = EvidenceSelectionCompiler._metric_contract(definition)
    scientific = card["measurement_contract"]
    assert scientific["unit_dimension"].endswith("." + dimension)
    assert scientific["canonical_unit_id"] == definition.measurement_contract.canonical_unit_id
    assert scientific["exclusions"] == definition.measurement_contract.exclusions
    assert scientific.get("positive_definition") == definition.measurement_contract.positive_definition
    assert card["required_dimension_ids"] == definition.required_dimension_ids
    assert card["optional_dimension_ids"] == definition.optional_dimension_ids
    assert scientific["identity_axes"] == definition.measurement_contract.identity_axes
    assert definition.model_dump(mode="json") == before


@pytest.mark.parametrize("disclosed", ["12.5%", "12.5立方米", "12.5吨"])
def test_compiling_contract_preserves_exact_source_quantity_and_unit(tmp_path, disclosed):
    inventory, source, regions, _, definition = build_pipeline(tmp_path,
        rows=[("氮氧化物", disclosed, "2024年", "中国境内运营")])
    original = source.model_dump(mode="json")
    inventory_before = inventory.model_dump(mode="json")
    from esg_targeted.models.template import DirectFillTemplateCompiler
    for region in regions:
        DirectFillTemplateCompiler().compile(region)
    EvidenceSelectionCompiler._metric_contract(definition)
    assert source.model_dump(mode="json") == original
    assert inventory.model_dump(mode="json") == inventory_before
    assert any(disclosed in span.text for span in inventory.spans)


def test_grounded_direct_core_adapts_to_shared_sources_without_a_second_model_or_business_approval(tmp_path):
    _, _, regions, package, definition = build_pipeline(tmp_path)
    selected = next(region for region in regions if any(c.candidate_type == "quantity" for c in region.candidates))
    decision = FoundModel().decide(selected).decision
    guarded = GroundingGuard().validate(selected, decision)
    assert guarded.accepted
    result = CoreResultMaterializer().materialize(package=package, metric=definition, packet=selected,
        decision=decision, guard=guarded, attempts=1)
    from esg_targeted.results.spec import RECORD_LAYOUT
    records = {collection: getattr(result, collection) for collection in RECORD_LAYOUT}
    ResultContractValidator(package.core).validate(records)
    assert records["quantitative_observations"]
    assert all(row["review_status"] == "pending" for row in records["quantitative_observations"])
    from esg_targeted.evidence.target_cells import target_cell_contracts
    targets = {target.cell_id: target for target in target_cell_contracts(selected)}
    task = request()
    facts, bindings = adapt_direct_core_facts(records, package, bound_core_ids=set(), request=task,
        model_executions=[{"metric_id": definition.metric_id, "provider": "synthetic", "model_id": "synthetic-model"}],
        target_cells=targets)
    assert len(facts) == len(bindings) == len(records["quantitative_observations"])
    assert all(binding["route"] == "mature_direct_adapter" for binding in bindings)
    assert all(fact["document_id"] == selected.source_document_id and fact["physical_sources"] for fact in facts)
    # Standard-fixed classifications are mapping metadata, never invented report identity.
    assert all("pollution_medium" not in fact["identity_raw"] for fact in facts)
    assert all(fact["review_status"] != "approved" for fact in facts)
    again = adapt_direct_core_facts(records, package, bound_core_ids=set(), request=task,
        model_executions=[{"metric_id": definition.metric_id, "provider": "synthetic", "model_id": "synthetic-model"}], target_cells=targets)
    assert again == (facts, bindings)
    assert adapt_direct_core_facts(records, package,
        bound_core_ids={binding["core_record_id"] for binding in bindings}, request=task) == ([], [])


@pytest.mark.parametrize("pre_exhausted", [False, True])
def test_report_workflow_uses_compiled_specialist_and_persists_shared_sources_and_job_budget(tmp_path, monkeypatch, pre_exhausted):
    from esg_targeted.config import settings
    from esg_targeted.evidence.disclosure import DisclosureSession
    from esg_targeted.storage.artifacts import ArtifactStore
    from esg_targeted.storage.job_store import JobStore
    from esg_targeted.workflow import TargetedExtractionWorkflow
    from tests.helpers import build_ir_fixture, standard_dist_root
    from tests.test_workflow import FakeEmbeddingProvider
    ir_root = tmp_path / "ir"
    build_ir_fixture(ir_root)
    runtime = replace(settings, project_root=tmp_path, ir_roots=(ir_root,), standard_dist_root=standard_dist_root(),
        output_root=tmp_path / "outputs", state_db=tmp_path / "jobs.sqlite", index_cache_root=tmp_path / "indexes",
        qwen_model_path=tmp_path / "Synthetic-Qwen", nuextract_model_path=tmp_path / "Synthetic-NuExtract")
    runtime.ensure_runtime_dirs()
    task = ExtractionRequest.model_validate({"ir_run_id": "ir-fixture-e2-4", "package_id": "esrs.2023-set1.e2-4",
        "package_version": "2.1.2", "metric_ids": ["esrs.2023-set1.e2-4.dp02"], "report_session_id": "report-00000000000000000000000000000000",
        "roles": {"semantic_brain": {"provider": "local_qwen"}, "specialist": {"provider": "local_nuextract"}},
        "call_budget": {"max_model_calls": 1 if pre_exhausted else 128},
        "semantic_search": False, "visual_fallback": False})
    class DirectOnly(FoundModel):
        provider_name = "local_nuextract"
        model_id = "Synthetic-NuExtract"
        def __init__(self): self.calls = 0
        def decide(self, *args, **kwargs):
            self.calls += 1
            run = super().decide(*args, **kwargs)
            return run.model_copy(update={"provider": self.provider_name, "model_id": self.model_id})
        def generate_structured(self, **kwargs): raise AssertionError("Table reader cannot call the source Agent")
    model = DirectOnly()
    class Brain:
        provider_name = "local_qwen"
        def close(self): pass
        def decide(self, *args, **kwargs): raise AssertionError("Table strategy must use selected specialist")
        def generate_structured(self, **kwargs): raise AssertionError("Table strategy must use selected specialist")
    jobs, artifacts = JobStore(runtime.state_db), ArtifactStore(runtime.output_root)
    jobs.create("synthetic-job", task, str(artifacts.job_dir("synthetic-job")))
    if pre_exhausted:
        from esg_targeted.budget_scope import package_model_budget
        budget = package_model_budget(artifacts, task, "synthetic-job")
        call = budget.begin_call("synthetic-prior-call", input_tokens=10, max_output_tokens=10)
        budget.finish_call(call["call_id"], output_tokens=1)
    engine = TargetedExtractionWorkflow(runtime, jobs, artifacts, embedding_provider_factory=FakeEmbeddingProvider)
    monkeypatch.setattr(engine, "_build_semantic_models", lambda _: (Brain(), model))
    monkeypatch.setattr(DisclosureSession, "expand", lambda *args: pytest.fail("Direct strategy cannot pool narrative siblings"))
    engine.run("synthetic-job")
    assert (model.calls == 0 if pre_exhausted else model.calls > 0), jobs.get("synthetic-job").model_dump()
    checkpoint = artifacts.read_json("synthetic-job", "checkpoints/esrs.2023-set1.e2-4.dp02.json")
    descriptor = checkpoint["execution_strategy"]
    assert descriptor["route"] == "table_series" and descriptor["provider"] == "local_nuextract"
    assert descriptor["model"] == "Synthetic-NuExtract"
    assert artifacts.read_json("synthetic-job", "input/execution-plan.json")["execution_strategies"][definition_id(task)] == descriptor
    assert (artifacts.job_dir("synthetic-job") / "runtime-budget.json").is_file()
    assert not (artifacts.report_session_dir(task.report_session_id) / "runtime-budget.json").exists()
    if pre_exhausted:
        assert jobs.get("synthetic-job").status.value == "failed"
        assert jobs.get("synthetic-job").summary["failure_category"] == "budget"
        assert jobs.get("synthetic-job").summary["budget_boundary"] == "package"
        assert artifacts.read_json("synthetic-job", "failure.json")["budget_boundary"] == "package"
        assert checkpoint["outcome"]["failure_category"] == "budget"
    else:
        assert jobs.get("synthetic-job").status.value != "failed", jobs.get("synthetic-job").model_dump()


def definition_id(task):
    return task.metric_ids[0]
