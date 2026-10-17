"""Catalog integrity and explicit extraction scopes, separate from standard law.

Core clauses belong to a package. Major clauses belong to a product profile.
Pending modules fail explicitly; compiling a subset requires an explicit topic
filter. Context and operands never change output membership or its denominator.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .compiler import StandardPackageCompiler
from .contracts import ContextRequirementReference, SharedRequirementReference, SemanticVersion, Sha256, StableId, StrictModel
from .errors import PackageIntegrityError
from .io import load_json


class ExtractionProfile(StrictModel):
    profile_id: StableId
    profile_version: SemanticVersion
    label: str
    selection_kind: Literal['platform_major', 'package_core', 'full']
    catalog_id: StableId
    source_datapoint_ids: list[str] = Field(min_length=1)
    selection_reason: str

    @model_validator(mode='after')
    def unique_selection(self):
        if len(set(self.source_datapoint_ids)) != len(self.source_datapoint_ids):
            raise ValueError('Duplicate original datapoint in profile')
        return self


class ModuleRegistration(StrictModel):
    package_id: StableId
    disclosure_requirement: str
    source_sheet: str
    state: Literal['compiled_draft', 'planned']
    executable: bool
    source_datapoint_ids: list[str] = Field(min_length=1)
    core_source_datapoint_ids: list[str]
    core_analysis_state: Literal['authored_draft', 'pending_full_module_analysis']
    major_source_datapoint_ids: list[str]
    source_directory: str | None
    package_version: SemanticVersion | None
    source_digest: Sha256 | None
    runtime_path: str | None
    next_steps: list[str]

    @model_validator(mode='after')
    def valid_state(self):
        if self.executable != (self.state == 'compiled_draft'):
            raise ValueError('Planned modules are not executable')
        if self.executable and not all([self.source_directory, self.package_version, self.source_digest, self.runtime_path, self.core_source_datapoint_ids]):
            raise ValueError('Executable module needs a complete compiled identity')
        if not self.executable and any([self.source_directory, self.package_version, self.source_digest, self.runtime_path]):
            raise ValueError('Placeholder cannot masquerade as a runtime package')
        if not set(self.major_source_datapoint_ids).issubset(self.core_source_datapoint_ids):
            raise ValueError('Major clauses must be package-core clauses')
        if not set(self.core_source_datapoint_ids).issubset(self.source_datapoint_ids):
            raise ValueError('Core clause outside its package')
        return self


class FrameworkPlaceholder(StrictModel):
    framework_id: StableId
    state: Literal['awaiting_source_catalog']
    executable: Literal[False]
    next_step: str


class PrinciplePlaceholder(StrictModel):
    framework_id: StableId
    module_id: StableId
    state: Literal['principles_reserved']
    executable: Literal[False]
    reason: str


class StandardRegistry(StrictModel):
    format_version: Literal['1.0']
    catalog_id: StableId
    source_sha256: Sha256
    modules: list[ModuleRegistration]
    shared_requirement_references: list[SharedRequirementReference]
    topic_shared_requirements: list[SharedRequirementReference]
    other_frameworks: list[FrameworkPlaceholder]
    principles: list[PrinciplePlaceholder]


def validate_registry(registry, catalog, packages):
    registry=StandardRegistry.model_validate(registry).model_dump(mode='json')
    if registry['catalog_id'] != catalog['catalog_id'] or registry['source_sha256'] != catalog['source_sha256']:
        raise PackageIntegrityError('Registry catalog identity mismatch')
    independent = [r for r in catalog['records'] if r['record_kind'] == '独立编号条目']
    expected = {r['id']:r for r in independent}
    if len(expected) != len(independent):raise PackageIntegrityError('Duplicate original datapoint in catalog')
    seen={}; by_package={p.manifest.package_id:p for p in packages.values()}
    package_ids=[m['package_id'] for m in registry['modules']]
    drs=[m['disclosure_requirement'] for m in registry['modules']]
    if len(set(package_ids))!=len(package_ids) or len(set(drs))!=len(drs):raise PackageIntegrityError('One disclosure requirement must own exactly one package')
    for raw in registry['modules']:
        module=ModuleRegistration.model_validate(raw)
        for original in module.source_datapoint_ids:
            if original in seen:raise PackageIntegrityError('Original datapoint assigned to multiple packages: '+original)
            if original not in expected or expected[original]['dr'] != module.disclosure_requirement:
                raise PackageIntegrityError('Original datapoint/DR mismatch: '+original)
            seen[original]=module.package_id
        if module.executable:
            if module.package_id not in by_package:raise PackageIntegrityError('Missing executable package: '+module.package_id)
            package=by_package[module.package_id]
            if set(m.source_datapoint_id for m in package.metrics) != set(module.source_datapoint_ids):
                raise PackageIntegrityError('Package source coverage mismatch: '+module.package_id)
            if package.manifest.package_version!=module.package_version or package.compilation.source_digest!=module.source_digest:
                raise PackageIntegrityError('Pinned package identity mismatch: '+module.package_id)
            if package.manifest.core_source_datapoint_ids!=module.core_source_datapoint_ids:
                raise PackageIntegrityError('Package-core selection mismatch: '+module.package_id)
            for metric in package.metrics:
                row=expected[metric.source_datapoint_id]
                if metric.provenance is None or metric.provenance.raw_values != row['raw_values'] or metric.provenance.source_row != row['source_row'] or metric.provenance.source_sheet != row['source_sheet']:
                    raise PackageIntegrityError('Original provenance differs from catalog: '+metric.source_datapoint_id)
                for field in ['conditional_or_alternative','may','eu_regulations','phase_in_column_k','phase_in_column_l']:
                    if getattr(metric.provenance,field)!=row[field]:raise PackageIntegrityError('Original legal facet differs from catalog: '+metric.source_datapoint_id)
                if metric.provenance.original_data_type!=row['data_type'] or metric.provenance.source_headers!=row['source_headers']:
                    raise PackageIntegrityError('Original type/header differs from catalog: '+metric.source_datapoint_id)
            for ref in package.manifest.shared_requirements: _validate_shared(ref, catalog, by_package, seen_dr=module.disclosure_requirement)
            expected_shared={r['id'] for r in catalog['records'] if r['record_kind']!='独立编号条目' and r['dr']==module.disclosure_requirement}
            if {r.source_datapoint_id for r in package.manifest.shared_requirements}!=expected_shared:raise PackageIntegrityError('Missing original shared reference in module')
            for ref in package.manifest.context_requirements:_validate_context(ref,by_package)
    if set(seen) != set(expected):raise PackageIntegrityError('Registry must account for every original independent datapoint')
    for ref in registry['topic_shared_requirements']:
        # Future topics can refer to already-built MDR, preserving blank source DR.
        _validate_shared(SharedRequirementReference.model_validate(ref),catalog,by_package,seen_dr=None)
    expected_shared={r['id'] for r in catalog['records'] if r['record_kind']!='独立编号条目'}
    references=registry['shared_requirement_references']
    if {r['source_datapoint_id'] for r in references}!=expected_shared or len(references)!=len(expected_shared):raise PackageIntegrityError('Registry must preserve all original MDR reference records')
    for ref in references:
        _validate_shared(SharedRequirementReference.model_validate(ref),catalog,by_package,seen_dr=ref['original_disclosure_requirement'])


def _validate_shared(ref,catalog,packages,seen_dr):
    import re
    row=next((r for r in catalog['records'] if r['id']==ref.source_datapoint_id),None)
    if row is None or row['record_kind']=='独立编号条目' or row['dr']!=seen_dr:
        raise PackageIntegrityError('Shared reference is not an original MDR reference')
    if row['source_sheet']!=ref.source_sheet or row['source_row']!=ref.source_row or row['dr']!=ref.original_disclosure_requirement:
        raise PackageIntegrityError('Shared source provenance mismatch')
    if ref.target_package_id not in packages:raise PackageIntegrityError('Missing shared target package')
    package=packages[ref.target_package_id]
    if package.manifest.package_version!=ref.target_package_version:
        raise PackageIntegrityError('Shared target version mismatch')
    if not set(ref.target_source_datapoint_ids).issubset({m.source_datapoint_id for m in package.metrics}):
        raise PackageIntegrityError('Shared reference contains nonexistent MDR datapoint')
    match=re.fullmatch(r'([^.]+)\.(MDR-[PAT])_(\d+)-(\d+)',ref.source_datapoint_id)
    if match is None:raise PackageIntegrityError('Malformed original MDR reference')
    topic,dr,start,end=match.groups()
    expected=[f'{dr}_{i:02}' for i in range(int(start),int(end)+1)]
    if ref.topic_binding!=topic or ref.target_source_datapoint_ids!=expected or package.manifest.standard.disclosure_requirement!=dr:
        raise PackageIntegrityError('Shared reference range/topic mismatch')
    purpose=('adopted_' if int(start)==1 else 'absent_')+{'P':'policy','A':'action','T':'target'}[dr[-1]]
    if ref.purpose!=purpose:raise PackageIntegrityError('Shared adoption branch mismatch')


def _validate_context(ref,packages):
    package=packages.get(ref.target_package_id)
    if package is None or package.manifest.package_version!=ref.target_package_version:
        raise PackageIntegrityError('Missing pinned ESRS 2/MDR context package')
    if not set(ref.target_source_datapoint_ids).issubset({m.source_datapoint_id for m in package.metrics}):
        raise PackageIntegrityError('Unknown context datapoint')


def load_registry(root:Path):
    root=root.resolve()
    registry_path=root/'registry/esrs/2023-set1/registry.json'
    if not registry_path.is_file():registry_path=root/'catalogs/esrs/2023-set1/registry.json'
    catalog_path=root/'catalog/esrs/2023-set1/ig3-original.json'
    if not catalog_path.is_file():catalog_path=root/'catalogs/esrs/2023-set1/ig3-original.json'
    registry=load_json(registry_path)
    catalog=load_json(catalog_path)
    compiler=StandardPackageCompiler();packages={}
    for raw in registry['modules']:
        r=ModuleRegistration.model_validate(raw)
        if not r.executable:continue
        path=(root/r.runtime_path).resolve()
        if not path.is_relative_to(root.resolve()):raise PackageIntegrityError('Runtime path outside standard root')
        packages[r.disclosure_requirement]=compiler.validate_compiled(path)
    validate_registry(registry,catalog,packages)
    return registry,catalog,packages


def compile_profile(root:Path,profile_path:Path,*,topics:list[str]|None=None):
    profile=ExtractionProfile.model_validate(load_json(profile_path))
    registry,catalog,packages=load_registry(root)
    if profile.catalog_id!=catalog['catalog_id']:raise PackageIntegrityError('Profile catalog identity mismatch')
    originals={r['id']:r for r in catalog['records'] if r['record_kind']=='独立编号条目'}
    unknown=set(profile.source_datapoint_ids)-set(originals)
    if unknown:raise PackageIntegrityError('Unknown original datapoints: '+', '.join(sorted(unknown)))
    if topics:
        valid_topics={r['source_sheet'].removeprefix('ESRS ') for r in catalog['records']}
        if set(topics)-valid_topics:raise PackageIntegrityError('Unknown topic filter')
    selected=[i for i in profile.source_datapoint_ids if not topics or originals[i]['source_sheet'].removeprefix('ESRS ') in topics]
    if not selected:raise PackageIntegrityError('Explicit filter selected no datapoints')
    registry_by_original={i:r for r in registry['modules'] for i in r['source_datapoint_ids']}
    if profile.selection_kind in {'platform_major','package_core'}:
        noncore=[i for i in selected if i not in registry_by_original[i]['core_source_datapoint_ids']]
        if noncore:raise PackageIntegrityError('Selected major/core clause outside package-core selection: '+', '.join(noncore))
    pending=[i for i in selected if not registry_by_original[i]['executable']]
    if pending:raise PackageIntegrityError('Selected modules are planned; no silent scope reduction: '+', '.join(pending))
    grouped=defaultdict(list)
    for original in selected:grouped[originals[original]['dr']].append(original)
    routes=[];context=[]
    for dr,ids in grouped.items():
        package=packages[dr];by_original={m.source_datapoint_id:m for m in package.metrics};by_id={m.metric_id:m for m in package.metrics}
        output=[by_original[i].metric_id for i in ids];execution=set(output);queue=list(output)
        while queue:
            metric_id=queue.pop();dependencies=set(by_id[metric_id].depends_on)
            for rule in package.derivations:
                if rule.target_metric_id==metric_id:
                    dependencies.update(o.source_metric_id for o in rule.operands if o.source_metric_id)
            for dependency in sorted(dependencies-execution):execution.add(dependency);queue.append(dependency)
        # Only explicit explanation edges: no inverse topic expansion or all-anchor selection.
        local_context=set()
        for relation in package.relations:
            if relation.relation_type.value in {'explains','contextualizes'} and set(relation.target_metric_ids)&execution:
                local_context.update(relation.source_metric_ids)
        shared=resolve_shared_requirements(package,registry,packages)
        context_refs=resolve_context_requirements(package,ids,packages)
        routes.append(dict(package_id=package.manifest.package_id,package_version=package.manifest.package_version,source_digest=package.compilation.source_digest,
                           source_datapoint_ids=ids,output_metric_ids=output,execution_metric_ids=sorted(execution),internal_dependency_metric_ids=sorted(execution-set(output)),context_metric_ids=sorted(local_context-execution),shared_requirement_references=shared,context_requirement_references=context_refs))
        context.extend([dict(package_id=package.manifest.package_id,metric_ids=sorted(local_context-execution),purpose='semantic_context_only')])
    return dict(profile_id=profile.profile_id,profile_version=profile.profile_version,selection_kind=profile.selection_kind,catalog_id=profile.catalog_id,
                catalog_source_sha256=catalog['source_sha256'],explicit_topic_filter=topics or [],
                source_datapoint_ids=selected,output_count=len(selected),excluded_by_explicit_filter=[i for i in profile.source_datapoint_ids if i not in selected],
                routes=routes,context=context,
                session_standards=[dict(package_id=r['package_id'],package_version=r['package_version'],metric_ids=r['output_metric_ids']) for r in routes],
                note='session_standards可填入现有ReportSession请求；上下文只提供定义/口径，不自动成为输出或覆盖分母。')


def resolve_context_requirements(package,source_datapoint_ids,packages):
    """Public interface for callers: pinned IDs, applicability, digest and role.

    No extraction or fact construction occurs here. A caller may read these
    semantic definitions or gather explicitly applicable report context later.
    """
    selected=set(source_datapoint_ids)
    if not selected.issubset({m.source_datapoint_id for m in package.metrics}):raise PackageIntegrityError('Context caller selected unknown original clause')
    by_id={p.manifest.package_id:p for p in packages.values()}
    resolved=[]
    for ref in package.manifest.context_requirements:
        applies=selected&set(ref.applies_to_source_datapoint_ids)
        if not applies:continue
        _validate_context(ref,by_id);target=by_id[ref.target_package_id]
        lookup={m.source_datapoint_id:m.metric_id for m in target.metrics}
        resolved.append(dict(**ref.model_dump(mode='json'),source_digest=target.compilation.source_digest,
                             metric_ids=[lookup[i] for i in ref.target_source_datapoint_ids],
                             selected_applicable_source_datapoint_ids=sorted(applies)))
    return resolved


def resolve_shared_requirements(package,registry,packages,*,adopted:bool|None=None):
    """Resolve MDR branches without guessing policy/action/target adoption.

    Blank-DR absence references remain blank in provenance. Their use here is
    an explicit topic/purpose association, not a change to the original catalog.
    Unknown adoption returns guarded references; no branch is activated.
    """
    if adopted is not None and type(adopted) is not bool:raise PackageIntegrityError('Adoption state must be explicit boolean or unknown')
    by_id={p.manifest.package_id:p for p in packages.values()}
    local=list(package.manifest.shared_requirements)
    topics={r.topic_binding for r in local}
    nouns={r.purpose.split('_',1)[1] for r in local}
    local += [SharedRequirementReference.model_validate(r) for r in registry['topic_shared_requirements']
              if r['topic_binding'] in topics and r['purpose'].split('_',1)[1] in nouns]
    result=[]
    for ref in local:
        required=ref.purpose.startswith('adopted_')
        if adopted is not None and adopted!=required:continue
        target=by_id.get(ref.target_package_id)
        if target is None or target.manifest.package_version!=ref.target_package_version:raise PackageIntegrityError('Missing pinned MDR target')
        lookup={m.source_datapoint_id:m.metric_id for m in target.metrics}
        if not set(ref.target_source_datapoint_ids)<=set(lookup):raise PackageIntegrityError('Unknown shared target clause')
        noun=ref.purpose.split('_',1)[1]
        result.append(dict(**ref.model_dump(mode='json'),metric_ids=[lookup[i] for i in ref.target_source_datapoint_ids],source_digest=target.compilation.source_digest,
                           activation_predicate={'path':f'report_context.{noun}_adopted','operator':'eq','value':required},
                           activation_state='awaiting_report_context' if adopted is None else 'applicable',
                           required_object_axes=['sustainability_topic',noun+'_ref'],output_policy='context_only'))
    return result
