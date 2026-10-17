"""A platform selection adds approved overlays without rewriting originals."""
from copy import deepcopy

from esg_standard_packages.extensions import CompiledExtension

TYPED_AXES = {'entity_ref','period_start','period_end','boundary_code','value_role','measurement_method'}


def build_extension_dictionary(original: dict, releases: list[dict], extension_ids: list[str], version: str) -> dict:
    dictionary = deepcopy(original)
    if version == original['dictionary_version']:
        raise ValueError('A changed platform selection requires a new dictionary version')
    by_id = {}
    for row in releases:
        release = CompiledExtension.model_validate(row.get('release', row))
        current = by_id.get(release.definition.extension_id)
        if current is None or tuple(map(int,release.definition.definition_version.split('.'))) > tuple(map(int,current.definition.definition_version.split('.'))):
            by_id[release.definition.extension_id] = release
    metrics = []
    for extension_id in extension_ids:
        release = by_id.get(extension_id)
        if release is None:
            raise ValueError(f'Platform selection requires a registered extension: {extension_id}')
        d = release.definition
        if not d.package_core:
            raise ValueError('A platform major extension must be package core')
        required = d.reported_required_axes
        comparison = d.comparison_contract
        metrics.append({
            'metric_id':d.extension_id,'definition_version':d.definition_version,
            'clause_kind':'project_extension','source_datapoint_id':None,'extension_id':d.extension_id,
            'related_source_datapoint_ids':[r.source_datapoint_id for r in d.original_clause_links],
            'parent_package_id':d.parent_package_id,'parent_package_version':d.parent_package_version,
            'label_zh':d.labels['zh'],'original_name':d.labels['en'],'topic':d.parent_package_id.split('.')[-1].split('-')[0].upper(),
            'description':d.description,'value_type':d.value_type,'canonical_unit':d.canonical_unit,
            'unit_dimension':d.unit_dimension,'value_constraints':d.value_constraints.model_dump(mode='json'),
            'readiness':'ready','definition_status':'approved_extension','package_core':d.package_core,'platform_major':True,
            'required_dimensions':[key for key in required if key not in TYPED_AXES],
            'required_context_fields':[key for key in required if key in TYPED_AXES],
            'fixed_dimensions':{key:values[0] for key,values in required.items() if len(values)==1 and key not in TYPED_AXES},
            'dimension_constraints':{key:{'allowed_values':values} for key,values in required.items() if key not in TYPED_AXES},
            'typed_context_constraints':{key:{'allowed_values':values} for key,values in required.items() if key in TYPED_AXES},
            'comparison_dimensions':comparison.required_axes,'comparison_allowed_values':comparison.allowed_values,
            'comparison_conditions':['主体、期间、边界及所列方法轴明确一致后才可比较；未知口径保留展示。'],
            'comparison_mode':'conditional_numeric','aggregation':'do_not_sum','value_role':'actual',
            'normalization':'compatible_unit_only','exclusions':d.exclusions,
            'standard_binding':{'package_id':d.parent_package_id,'package_version':d.parent_package_version,
                'metric_id':d.extension_id,'digest':d.parent_source_digest},
            'extension_binding':{'extension_id':d.extension_id,'definition_version':d.definition_version,
                'release_digest':release.release_digest,'definition_digest':release.definition_digest,
                'parent_package_id':d.parent_package_id,'parent_package_version':d.parent_package_version,
                'parent_source_digest':d.parent_source_digest},
            'allowed_value_origins':d.allowed_value_origins,
        })
    dictionary['dictionary_version'] = version
    dictionary['metrics'].extend(metrics)
    dictionary['extension_selection'] = {'extension_ids':extension_ids,'original_datapoint_count':len(original['metrics']),
        'extension_count':len(metrics),'selection_kind':'platform_major_extension'}
    return dictionary
