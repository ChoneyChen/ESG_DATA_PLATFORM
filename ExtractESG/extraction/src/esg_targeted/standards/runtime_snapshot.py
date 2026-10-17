"""A frozen, optional extension overlay; base ESRS snapshots never change."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from esg_standard_packages.contracts import (
    CodeSetDefinition, CodeValue, DimensionDefinition, ElementDefinition,
    MeasurementContract, MetricDefinition, SemanticConcept, SemanticVersion,
    Sha256, StableId, StrictModel,
)
from esg_standard_packages.extensions import (
    CompiledExtension, ExtensionRegistry, ExtensionSelection,
    load_registered_extension, validate_release, validate_selection,
)
from esg_standard_packages.errors import PackageIntegrityError
from esg_standard_packages.io import canonical_json_bytes, load_json, sha256_bytes


class RuntimeStandardSnapshot(StrictModel):
    schema_version: Literal['runtime-standard-snapshot-1.0']
    parent_package_id: StableId
    parent_package_version: SemanticVersion
    parent_source_digest: Sha256
    selections: list[ExtensionSelection] = Field(min_length=1)
    extension_releases: list[CompiledExtension] = Field(min_length=1)
    runtime_digest: Sha256


class ExecutableExtensionMetric(MetricDefinition):
    """Execution adapter, never an original MetricDefinition authoring row."""
    source_datapoint_id: None = None
    clause_kind: Literal['project_extension'] = 'project_extension'
    extension_id: StableId
    extension_definition_version: SemanticVersion
    extension_release_digest: Sha256
    related_source_datapoint_ids: list[str]
    package_core: bool


class InternalMetricScope(StrictModel):
    metric_id: StableId
    source_datapoint_id: str
    clause_kind: Literal['original_datapoint'] = 'original_datapoint'
    dependency_role: Literal['calculation_operand', 'standard_dependency']
    required_by_extension_ids: list[StableId] = Field(default_factory=list)


class FrozenExtensionOperandRole(StrictModel):
    extension_id: StableId
    definition_version: SemanticVersion
    release_digest: Sha256
    derivation_rule_ids: list[StableId]
    role: Literal['reviewed_publication_operand'] = 'reviewed_publication_operand'
    internal_operand_metric_ids: list[StableId]
    selected_output_operand_metric_ids: list[StableId]


class InternalDependencyScope(StrictModel):
    schema_version: Literal['internal-dependency-facts-v2']
    scope_role: Literal['internal_dependency'] = 'internal_dependency'
    output_policy: Literal['excluded_from_selected_output_and_coverage'] = 'excluded_from_selected_output_and_coverage'
    parent_package_id: StableId
    parent_package_version: SemanticVersion
    parent_source_digest: Sha256
    runtime_standard_digest: Sha256
    selected_output_metric_ids: list[StableId]
    metric_ids: list[StableId]
    source_datapoint_ids: list[str]
    metrics: list[InternalMetricScope]
    required_by_extensions: list[FrozenExtensionOperandRole] = Field(default_factory=list)
    outcomes: list[dict]
    records: dict[str, list[dict]]
    core_bindings: list[dict]
    source_facts: list[dict]
    evidence_references: list[dict]
    contract_validation: dict

    @model_validator(mode='after')
    def enforce_scope(self):
        if self.metric_ids != [m.metric_id for m in self.metrics] or self.source_datapoint_ids != [m.source_datapoint_id for m in self.metrics]:
            raise ValueError('Internal scope identity lists differ from its original-clause whitelist')
        if len(set(self.metric_ids)) != len(self.metric_ids) or len(set(self.source_datapoint_ids)) != len(self.source_datapoint_ids):
            raise ValueError('Internal scope contains duplicate clause identities')
        allowed = set(self.metric_ids)
        if any(r.get('metric_id') not in allowed for name, rows in self.records.items()
               if name != 'evidence_references' for r in rows):
            raise ValueError('Core record is outside the internal original-clause scope')
        if any(o.get('metric_id') not in allowed for o in self.outcomes):
            raise ValueError('Internal outcome is outside its clause whitelist')
        if self.evidence_references != self.records.get('evidence_references', []):
            raise ValueError('Internal evidence must be the same physical references as its Core records')
        core_ids = {r.get('observation_id') or r.get('assertion_id') for name in ('quantitative_observations','qualitative_assertions')
                    for r in self.records.get(name, [])}
        source_ids = {s.get('source_fact_id') for s in self.source_facts}
        if any(b.get('core_record_id') not in core_ids or b.get('source_fact_id') not in source_ids or
               b.get('metric_id') not in allowed for b in self.core_bindings):
            raise ValueError('Internal binding must reference scoped Core and preserved source facts')
        if any(set(role.internal_operand_metric_ids) - allowed for role in self.required_by_extensions):
            raise ValueError('Frozen extension role claims an undeclared internal metric')
        return self


def configured_extension_root(standard_root: Path, override: Path | None = None) -> Path:
    return (override or Path(os.environ.get('ESG_EXTENSION_ROOT') or standard_root / 'extensions')).resolve()


def registered_extensions(standard_root, catalog, *, extension_root=None):
    """Read approved projections; proposals never appear as selectable releases."""
    root = configured_extension_root(standard_root, extension_root)
    registry = ExtensionRegistry.model_validate(load_json(root / 'registry.json'))
    releases = []
    for registration in registry.registrations:
        parent = catalog.load(registration.parent_package_id, registration.parent_package_version)
        releases.append(load_registered_extension(root, registration, parent=parent))
    return releases


def resolve_report_parents(request, standard_root, catalog):
    """Fill explicit extensions' parent rows, without selecting original outputs."""
    from esg_targeted.contracts import RequirementSelection
    if not request.extension_selections:
        return request
    releases = {(r.definition.extension_id, r.definition.definition_version, r.release_digest): r
                for r in registered_extensions(standard_root, catalog)}
    rows = list(request.standards)
    by_id = {row.package_id: row for row in rows}
    for selection in request.extension_selections:
        release = releases.get((selection.extension_id, selection.definition_version, selection.release_digest))
        if release is None:
            raise PackageIntegrityError('Selected extension has no approved registered release')
        definition = release.definition
        existing = by_id.get(definition.parent_package_id)
        if existing and existing.package_version != definition.parent_package_version:
            raise PackageIntegrityError('Extension and selected parent versions differ')
        if not existing:
            row = RequirementSelection(package_id=definition.parent_package_id,
                package_version=definition.parent_package_version, metric_ids=[])
            rows.append(row); by_id[row.package_id] = row
    return request.model_copy(update={'standards': rows})


def load_extension_selections(standard_root: Path, parent, selections, *, extension_root=None):
    if not selections:
        return []
    root = configured_extension_root(standard_root, extension_root)
    registry = ExtensionRegistry.model_validate(load_json(root / 'registry.json'))
    releases = []
    for selection in selections:
        registration = next((r for r in registry.registrations if (r.extension_id, r.definition_version) ==
            (selection.extension_id, selection.definition_version)), None)
        if registration is None:
            raise PackageIntegrityError('Selected extension has no approved registered release')
        if (registration.parent_package_id, registration.parent_package_version) != (
                parent.manifest.package_id, parent.manifest.package_version):
            raise PackageIntegrityError('Selected extension belongs to another parent package/version')
        release = load_registered_extension(root, registration, parent=parent)
        validate_selection(selection, release, parent=parent)
        releases.append(release)
    return releases


def selections_for_parent(standard_root, parent, selections, *, extension_root=None):
    if not selections:
        return []
    root = configured_extension_root(standard_root, extension_root)
    registry = ExtensionRegistry.model_validate(load_json(root / 'registry.json'))
    selected = []
    for selection in selections:
        registered = next((r for r in registry.registrations if (r.extension_id, r.definition_version) ==
                          (selection.extension_id, selection.definition_version)), None)
        if registered is None:
            raise PackageIntegrityError('Selected extension has no approved registered release')
        if registered.parent_package_id == parent.manifest.package_id:
            if registered.parent_package_version != parent.manifest.package_version:
                raise PackageIntegrityError('Selected extension parent version differs')
            selected.append(selection)
    return selected


def freeze_runtime_snapshot(parent, selections, releases) -> RuntimeStandardSnapshot:
    if not selections or len(selections) != len(releases):
        raise PackageIntegrityError('Runtime extension selections and releases must match')
    if len({s.extension_id for s in selections}) != len(selections):
        raise PackageIntegrityError('Select exactly one release per extension identity')
    for selection, release in zip(selections, releases):
        validate_selection(selection, release, parent=parent)
    body = dict(schema_version='runtime-standard-snapshot-1.0', parent_package_id=parent.manifest.package_id,
        parent_package_version=parent.manifest.package_version, parent_source_digest=parent.compilation.source_digest,
        selections=[s.model_dump(mode='json') for s in selections], extension_releases=[r.model_dump(mode='json') for r in releases])
    return RuntimeStandardSnapshot(**body, runtime_digest=sha256_bytes(canonical_json_bytes(body)))


def validate_runtime_snapshot(value, parent, *, expected_selections=None):
    snapshot = value if isinstance(value, RuntimeStandardSnapshot) else RuntimeStandardSnapshot.model_validate(value)
    recreated = freeze_runtime_snapshot(parent, snapshot.selections, snapshot.extension_releases)
    if snapshot != recreated:
        raise PackageIntegrityError('Frozen runtime standard snapshot identity/digest differs')
    if expected_selections is not None and list(expected_selections) != snapshot.selections:
        raise PackageIntegrityError('Frozen extension snapshot differs from the extraction request')
    return snapshot


class RuntimeStandardView:
    """Duck-typed execution view with base metadata and separate overlay identity."""
    def __init__(self, parent, snapshot):
        self.base_package = parent
        self.runtime_snapshot = validate_runtime_snapshot(snapshot, parent)
        self.metrics = list(parent.metrics)
        self.elements = list(parent.elements)
        self.dimensions = list(parent.dimensions)
        self.code_sets = list(parent.code_sets)
        self.concepts = list(parent.concepts)
        self.elements_by_metric = {k: list(v) for k, v in parent.elements_by_metric.items()}
        self.extension_computations = []
        self.derived_only_metric_ids = set()
        self.extension_by_id = {}
        for selection, release in zip(self.runtime_snapshot.selections, self.runtime_snapshot.extension_releases):
            self._add_extension(selection, release)

    def __getattr__(self, name):
        return getattr(self.base_package, name)

    def model_dump(self, **kwargs):
        # Existing snapshot writers cannot accidentally overwrite the base with
        # the execution view. The overlay always has its own artifact.
        return self.base_package.model_dump(**kwargs)

    @property
    def runtime_digest(self):
        return self.runtime_snapshot.runtime_digest

    def _add_extension(self, selection, release):
        definition = release.definition
        eid = definition.extension_id
        self.extension_by_id[eid] = release
        concept_id = eid + '.concept.measurement'
        self.concepts.append(SemanticConcept(concept_id=concept_id, labels=definition.labels,
            description=definition.description, source_refs=[]))
        ids = []
        required_dimensions, optional_dimensions = [], []
        def element(code, label, role, target, *, storage='dimension', required=False, unit=False, fixed=None, values=None):
            element_id = eid + '.element.' + code.replace('_', '-')
            contract = {'primary_type': 'decimal' if role == 'value' else 'string', 'fallback_types': []}
            if role == 'value': contract['fallback_types'] = ['decimal_range']
            if unit or role == 'value': contract['unit_dimension'] = definition.unit_dimension
            if fixed is not None:
                codes_id = eid + '.codeset.' + code.replace('_', '-')
                self.code_sets.append(CodeSetDefinition(code_set_id=codes_id,
                    values=[CodeValue(code=v,labels={'zh':v,'en':v}) for v in values]))
                contract.update(primary_type='enum', fallback_types=['text'], code_set_id=codes_id, fixed_value=fixed)
            if storage == 'dimension':
                dimension_id = eid + '.dimension.' + code.replace('_', '-')
                self.dimensions.append(DimensionDefinition(dimension_id=dimension_id, value_type='string',
                    labels={'zh':label,'en':code}, description='Preserve the supported source context; do not impute missing values.'))
                target = dimension_id
                (required_dimensions if required else optional_dimensions).append(dimension_id)
            item = ElementDefinition(element_id=element_id, metric_id=eid, element_code=code,
                labels={'zh':label,'en':code}, description='Project extension field: preserve the report expression and physical evidence.',
                semantic_role=role, requirement_level='required' if required else 'optional',
                cardinality={'minimum':1 if required else 0,'maximum':1}, null_allowed=not required,
                value_contract=contract, binding={'record_type':'quantitative_observation','storage':storage,'target':target},
                source_refs=[], concept_ids=[concept_id] if role == 'value' else [])
            self.elements.append(item); ids.append(element_id)
        element('value', definition.labels['zh'], 'value', 'value_raw', storage='core_field', required=True)
        element('unit', '原单位', 'unit', 'unit_raw', storage='core_field', required=True, unit=True)
        element('reporting_period', '报告期间', 'period', 'reporting_period_raw', storage='core_field')
        element('reporting_boundary', '报告边界', 'scope', 'reporting_boundary', storage='core_field')
        element('reporting_entity', '事实主体', 'dimension', 'reporting_entity')
        element('source_row_label', '来源原标题', 'subject', 'source_row_label', storage='attribute')
        for axis, allowed in definition.reported_required_axes.items():
            # A single target code describes the selected task. The model must
            # still classify the source as supporting this concept; constants
            # never establish a missing report method or gross amount basis.
            element(axis, axis, 'dimension', axis, required=True,
                    fixed=allowed[0] if len(allowed) == 1 else None, values=allowed)
        for axis in definition.reported_context_axes:
            element(axis, axis, 'method' if axis in {'scope_2_method','accounting_standard'} else 'context', axis)
        semantic_axes = ['source_row_topic','reporting_period','reporting_entity','entity_aggregation_role',
                         *definition.reported_required_axes, *definition.reported_context_axes]
        # Keep the execution contract of old frozen parents byte-compatible.
        # New parents explicitly separate the target concept from reading
        # guidance and exclusions, including independently registered overlays.
        semantic_contract = {}
        if all(m.measurement_contract is None or
               m.measurement_contract.semantic_contract_version == 'partitioned-semantics-v1'
               for m in self.base_package.metrics):
            semantic_contract = dict(semantic_contract_version='partitioned-semantics-v1',
                positive_definition=definition.labels['zh'],
                interpretation_guidance=[definition.description, definition.rationale],
                distinguish_from=[m.labels['zh'] for m in self.base_package.metrics
                    if m.source_datapoint_id in {r.source_datapoint_id for r in definition.original_clause_links}])
        metric = ExecutableExtensionMetric(metric_id=eid, extension_id=eid,
            extension_definition_version=definition.definition_version, extension_release_digest=release.release_digest,
            related_source_datapoint_ids=[r.source_datapoint_id for r in definition.original_clause_links],
            package_core=definition.package_core, disclosure_requirement=self.manifest.standard.disclosure_requirement,
            paragraph='', application_requirements=[], labels=definition.labels, official_data_type='Project numeric extension',
            data_class='quantitative', value_family='quantitative_ghg' if definition.unit_dimension.endswith('ghg-emissions') else 'quantitative_numeric',
            obligation_class='voluntary', allowed_value_origins=selection.value_origins, cardinality={'minimum':0,'maximum':None},
            required_dimension_ids=required_dimensions, optional_dimension_ids=optional_dimensions, description=definition.description,
            reporting_requirement='Project extension, not an official ESRS original clause. '+definition.rationale,
            source_refs=[], subject_concept_groups=[[concept_id]], fact_grain='one_source_value_cell_per_period_entity',
            measurement_kind='quantitative_ghg' if definition.unit_dimension.endswith('ghg-emissions') else 'quantitative_numeric',
            required_semantic_discriminators=['reporting_period','reporting_entity',*definition.reported_required_axes],
            confusable_metric_ids=[m.metric_id for m in self.base_package.metrics if m.source_datapoint_id in
                                  {r.source_datapoint_id for r in definition.original_clause_links}],
            evidence_form=['structured_table_row','text'], extraction_strategy='reported' if 'reported' in selection.value_origins else 'derived',
            identity_axes=semantic_axes, metric_family=eid+'.family', evidence_roles=['primary_measurement','linked_method_context'],
            depends_on=[], derivation_policy='publication_reviewed_operands_only', disclosure_topic=eid+'.topic',
            disclosure_role='anchor' if definition.package_core else 'detail', provenance=None,
            measurement_contract=MeasurementContract(template='numeric',value_role='actual',unit_dimension=definition.unit_dimension,
                canonical_unit_id=definition.canonical_unit_id,normalization='compatible_unit_only',identity_axes=semantic_axes,
                comparison_axes=semantic_axes,comparison_mode='conditional_numeric',aggregation='do_not_sum',
                exclusions=definition.exclusions,comparison_requirements=['Published comparison requires every registered reviewed axis; unknown method or amount basis remains display-only.'],
                **semantic_contract))
        self.metrics.append(metric); self.elements_by_metric[eid] = ids
        if 'reported' not in selection.value_origins:
            self.derived_only_metric_ids.add(eid)
        if 'derived' in selection.value_origins:
            selection_plan = validate_selection(selection, release, parent=self.base_package)
            self.extension_computations.append({**selection_plan, 'state':'awaiting_reviewed_operands',
                'reason_codes':['publication_operand_review_required'], 'value_origin':'derived',
                'calculation_stage':'reviewed_publication', 'output_review_status':'pending'})


def execution_digest(package):
    return getattr(package, 'runtime_digest', package.compilation.source_digest)


def standard_dependency_ids(package, selected_ids):
    metrics = {m.metric_id:m for m in package.metrics}
    dependencies, pending = set(), list(selected_ids)
    while pending:
        metric = metrics[pending.pop()]
        if metric.derivation_policy != 'reported_then_derived_when_dependencies_complete':
            continue
        needed = set(metric.depends_on)
        needed.update(o.source_metric_id for rule in package.derivations if rule.target_metric_id == metric.metric_id
                      and rule.execution_stage == 'data_processing' for o in rule.operands if o.source_metric_id)
        for mid in needed - set(selected_ids) - dependencies:
            if mid not in metrics:
                raise PackageIntegrityError('Unknown frozen standard dependency')
            dependencies.add(mid); pending.append(mid)
    return dependencies


def declared_internal_ids(package, selected_ids):
    extension_operands = {mid for recipe in getattr(package,'extension_computations', [])
                          for mid in recipe['internal_operand_metric_ids']}
    return (extension_operands | standard_dependency_ids(package,set(selected_ids) | extension_operands)) - set(selected_ids)


def selected_output_counts(package, selected_ids):
    by_id = {m.metric_id:m for m in package.metrics}
    originals = sum(by_id[mid].source_datapoint_id is not None for mid in selected_ids if mid in by_id)
    return {'original_output_count': originals, 'extension_output_count':len(selected_ids)-originals}


def base_package(package):
    return getattr(package, 'base_package', package)


def selected_output_ids(request, package):
    if request.metric_ids:
        return list(dict.fromkeys([*request.metric_ids, *(s.extension_id for s in request.extension_selections)]))
    if getattr(request, 'extension_selections', []):
        return [s.extension_id for s in request.extension_selections]
    return [m.metric_id for m in package.metrics]


def freeze_extraction_request(parent, request, standard_root):
    if not request.extension_selections:
        if request.runtime_standard_digest is not None:
            raise PackageIntegrityError('Base-only request cannot claim an extension runtime digest')
        return request, None
    releases = load_extension_selections(standard_root, parent, request.extension_selections)
    snapshot = freeze_runtime_snapshot(parent, request.extension_selections, releases)
    if request.runtime_standard_digest not in (None, snapshot.runtime_digest):
        raise PackageIntegrityError('Submitted runtime digest differs from approved extension selection')
    view = RuntimeStandardView(parent, snapshot)
    output_ids = selected_output_ids(request, view)
    if set(output_ids) - {m.metric_id for m in view.metrics}:
        raise PackageIntegrityError('Unknown selected execution metric')
    if set(request.internal_metric_ids) - declared_internal_ids(view,output_ids):
        raise PackageIntegrityError('Explicit internal metrics are outside frozen selected dependency rules')
    return request.model_copy(update={'runtime_standard_digest':snapshot.runtime_digest,
                                      'metric_ids':output_ids}), snapshot


def load_runtime_package(parent, *, artifact_root=None, request=None):
    snapshot_path = Path(artifact_root) / 'input/runtime-standard-snapshot.json' if artifact_root else None
    if snapshot_path and snapshot_path.is_file():
        snapshot = validate_runtime_snapshot(load_json(snapshot_path), parent,
            expected_selections=getattr(request, 'extension_selections', None))
        if request and request.runtime_standard_digest != snapshot.runtime_digest:
            raise PackageIntegrityError('Request runtime digest differs from its frozen standard snapshot')
        return RuntimeStandardView(parent, snapshot)
    if request and getattr(request, 'extension_selections', []):
        raise PackageIntegrityError('Extension request requires its frozen runtime standard snapshot')
    return parent


def execution_catalog_dict(package):
    body = package.model_dump(mode='json')
    if isinstance(package, RuntimeStandardView):
        body.update({key: [item.model_dump(mode='json') for item in getattr(package, key)]
                     for key in ('metrics', 'elements', 'dimensions', 'code_sets', 'concepts')})
        body['elements_by_metric'] = package.elements_by_metric
    return body


def metric_binding_identity(package, metric_id):
    """Base identity remains stable; an extension additionally pins its release."""
    release = getattr(package, 'extension_by_id', {}).get(metric_id)
    if not release:
        return {}
    return {'clause_kind': 'project_extension', 'extension_id': metric_id,
            'extension_definition_version': release.definition.definition_version,
            'extension_release_digest': release.release_digest,
            'runtime_standard_digest': package.runtime_digest,
            'related_source_datapoint_ids': [r.source_datapoint_id for r in release.definition.original_clause_links]}


def runtime_bundle_fields(package):
    if not isinstance(package, RuntimeStandardView):
        return {}
    return {'runtime_standard_snapshot': package.runtime_snapshot.model_dump(mode='json'),
        'execution_metric_catalog': [m.model_dump(mode='json') for m in package.metrics],
        'extension_computations': package.extension_computations}


def build_internal_dependency_scope(package, internal_ids, selected_ids, records, outcomes, bindings, source_facts,
                                    *, pending_states=None):
    from esg_targeted.results.validation import ResultContractValidator
    metrics = {m.metric_id:m for m in package.metrics}
    declarations = []
    recipes = getattr(package, 'extension_computations', [])
    for mid in sorted(internal_ids):
        metric = metrics[mid]
        if metric.source_datapoint_id is None:
            raise PackageIntegrityError('Internal operand whitelist requires real original clause IDs')
        required_by = [r['extension_id'] for r in recipes if mid in r['internal_operand_metric_ids']]
        declarations.append(InternalMetricScope(metric_id=mid,source_datapoint_id=metric.source_datapoint_id,
            dependency_role='calculation_operand' if required_by else 'standard_dependency',
            required_by_extension_ids=required_by))
    roles = [FrozenExtensionOperandRole(extension_id=r['extension_id'],definition_version=r['definition_version'],
        release_digest=r['release_digest'],derivation_rule_ids=r['selected_derivation_rule_ids'],
        internal_operand_metric_ids=sorted(set(r['internal_operand_metric_ids']) & set(internal_ids)),
        selected_output_operand_metric_ids=sorted(set(r['internal_operand_metric_ids']) & set(selected_ids))) for r in recipes]
    core_ids = {f.get('observation_id') or f.get('assertion_id') for name in ('quantitative_observations','qualitative_assertions')
                for f in records.get(name, [])}
    bound = [b for b in bindings if b['core_record_id'] in core_ids]
    source_ids = {b['source_fact_id'] for b in bound}
    scoped_sources = {f['source_fact_id']:f for f in source_facts if f['source_fact_id'] in source_ids}
    # A partial scope declares the whole frozen dependency whitelist, while
    # only committed Core is materialized. Missing operands are explicit work
    # states, never synthetic reporting tasks or zero-valued observations.
    scoped_outcomes = {o['metric_id']:o for o in outcomes if o['metric_id'] in internal_ids}
    for mid in sorted(set(internal_ids) - set(scoped_outcomes)):
        state = (pending_states or {}).get(mid, {})
        scoped_outcomes[mid] = {'metric_id':mid,'source_datapoint_id':metrics[mid].source_datapoint_id,
            'status':state.get('status','not_executed'),'found_status':'not_evaluated',
            'guard_accepted':False,'attempts':state.get('attempts',0),
            'uncertainty_reason':state.get('reason','Internal operand has no committed, current Core yet.')}
    scope = InternalDependencyScope(schema_version='internal-dependency-facts-v2',
        parent_package_id=package.manifest.package_id,parent_package_version=package.manifest.package_version,
        parent_source_digest=package.compilation.source_digest,runtime_standard_digest=execution_digest(package),
        selected_output_metric_ids=sorted(selected_ids),
        metric_ids=[d.metric_id for d in declarations],source_datapoint_ids=[d.source_datapoint_id for d in declarations],
        metrics=declarations,required_by_extensions=roles,records=records,
        outcomes=[scoped_outcomes[mid] for mid in sorted(scoped_outcomes)],
        core_bindings=bound,source_facts=list(scoped_sources.values()),evidence_references=records.get('evidence_references', []),
        contract_validation=ResultContractValidator(package.core).validate(records))
    return scope.model_dump(mode='json')


def validate_internal_dependency_scope(value, package):
    from esg_targeted.results.validation import ResultContractValidator
    scope = InternalDependencyScope.model_validate(value)
    if (scope.parent_package_id,scope.parent_package_version,scope.parent_source_digest,scope.runtime_standard_digest) != (
            package.manifest.package_id,package.manifest.package_version,package.compilation.source_digest,execution_digest(package)):
        raise PackageIntegrityError('Internal dependency scope differs from frozen standard identities')
    by_id = {m.metric_id:m for m in package.metrics}
    if any(mid not in by_id or by_id[mid].source_datapoint_id != original for mid, original in zip(scope.metric_ids,scope.source_datapoint_ids)):
        raise PackageIntegrityError('Internal dependency scope original IDs differ from frozen parent')
    selected = set(scope.metric_ids)
    if hasattr(package, 'runtime_snapshot') and selected != declared_internal_ids(package,scope.selected_output_metric_ids):
        raise PackageIntegrityError('Internal original-clause whitelist differs from frozen selected dependency rules')
    expected = {r['extension_id']:r for r in getattr(package, 'extension_computations', [])}
    if {r.extension_id for r in scope.required_by_extensions} != set(expected):
        raise PackageIntegrityError('Internal dependency roles differ from selected frozen extension rules')
    for role in scope.required_by_extensions:
        recipe = expected[role.extension_id]
        if (role.definition_version,role.release_digest,role.derivation_rule_ids) != (
                recipe['definition_version'],recipe['release_digest'],recipe['selected_derivation_rule_ids']):
            raise PackageIntegrityError('Internal dependency role release/rule identity differs')
        operands = set(recipe['internal_operand_metric_ids'])
        if set(role.internal_operand_metric_ids) != operands & selected or set(role.selected_output_operand_metric_ids) != operands - selected:
            raise PackageIntegrityError('Internal and output operand roles differ from the frozen derivation')
    for declaration in scope.metrics:
        required_by = [r['extension_id'] for r in getattr(package,'extension_computations', [])
                       if declaration.metric_id in r['internal_operand_metric_ids']]
        if declaration.required_by_extension_ids != required_by or declaration.dependency_role != ('calculation_operand' if required_by else 'standard_dependency'):
            raise PackageIntegrityError('Internal original-clause role differs from frozen rule membership')
    ResultContractValidator(package.core).validate(scope.records)
    return scope


def deferred_extension_result(package, metric, ir, job_id):
    """A computation request has no extracted value until reviewed publication."""
    from esg_targeted.contracts import MaterializedTaskResult, TaskOutcome
    from esg_targeted.ids import stable_id
    reason = 'Selected extension calculation awaits reviewed, normalized publication operands; no report value was synthesized.'
    task_id = stable_id('task', job_id, metric.metric_id, package.runtime_digest)
    block = {'code': 'publication_operand_review_required', 'stage': 'reviewed_publication',
             'reason': reason, 'metric_id': metric.metric_id}
    task = dict(task_id=task_id, report_id=ir.document_id, document_id=ir.document_id,
        ir_run_id=ir.run_id, ir_revision=ir.ir_revision, package_id=package.manifest.package_id,
        package_version=package.manifest.package_version, metric_id=metric.metric_id,
        materiality_status='not_assessed', applicability_status='not_assessed', found_status='uncertain',
        result_count=0, evidence_count=0, uncertainty_reason=reason, review_status='human_required', task_status='partial')
    outcome = TaskOutcome(task_id=task_id, metric_id=metric.metric_id, status='awaiting_reviewed_operands',
        found_status='uncertain', review_status='human_required', guard_accepted=False, attempts=0,
        result_counts={'reporting_task':1, 'quantitative_observation':0, 'qualitative_assertion':0,
                      'attribute_value':0, 'dimension_value':0, 'evidence_reference':0}, uncertainty_reason=reason,
        dependency_status='blocked', dependency_blocks=[block])
    return MaterializedTaskResult(outcome=outcome, reporting_tasks=[task], quantitative_observations=[],
        qualitative_assertions=[], attribute_values=[], dimension_values=[], evidence_references=[])
