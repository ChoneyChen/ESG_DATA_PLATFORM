#!/usr/bin/env python3
"""Build the product dictionary without changing the frozen standard packages.

Original clause IDs identify source requirements. Semantic metric IDs identify
product concepts. Package-core membership and platform-major selection stay
separate; an executable subset requires the explicit E1/E2/E3 topic filter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
STANDARD_ROOT = PLATFORM_ROOT.parent / 'ExtractESG' / 'standard_packages'
sys.path.insert(0, str(STANDARD_ROOT / 'src'))
from esg_standard_packages.scope import compile_profile, load_registry  # noqa: E402
from esg_standard_packages.io import canonical_json_bytes  # noqa: E402
from esg_standard_packages.scientific_contract import compile_dictionary_metrics, compile_scientific_contract  # noqa: E402

VERSION = '1.5.0'
# Definition versions continue from the last sealed file dictionary, not from
# 1.0.0: a changed definition must never reuse a version already sealed.
# Dictionary versions are shared with platform extension selections (the
# live 1.3.0 is 1.2.0 plus an approved extension), so the next changed file
# dictionary must take a version above every sealed one.
PREVIOUS = 'catalog/metric-dictionary.v1.json'
CLAUSE_FIXED_AXES = Path('profiles/esrs/2023-set1/clause-fixed-axes.json')


def load_clause_fixed_axes(path: Path | None = None, *, standard_root: Path = STANDARD_ROOT) -> dict[str, dict]:
    """A clause's fixed values are authored once, next to the standard.

    The dictionary and the clause catalog both read this file; neither writes
    its own copy, so a clause fixed to 可再生 is fixed in exactly one place.
    """
    resolved = path or (Path(standard_root) / CLAUSE_FIXED_AXES)
    payload = json.loads(Path(resolved).read_text(encoding='utf-8'))
    return {key: dict(value) for key, value in (payload.get('axes') or {}).items()}


PLATFORM_POLICY = Path('contracts/platform-policy.esrs-2023-set1.json')


def load_platform_policy(path: Path | None = None, *, standard_root: Path = STANDARD_ROOT) -> dict[str, dict]:
    """The platform's comparison and presentation policy, declared once.

    Scientific definition — identity axes, fixed values, unit, aggregation and
    exclusions — comes from the standard package. This file only declares the
    platform layer, so no clause is authored in Python any more.
    """
    resolved = path or (Path(standard_root) / PLATFORM_POLICY)
    payload = json.loads(Path(resolved).read_text(encoding='utf-8'))
    metrics = payload.get('metrics') or {}
    if not metrics:
        raise ValueError(f'Platform policy declares no metric: {resolved}')
    return metrics


def _semantic_signature(value):
    if isinstance(value, dict):
        return {key: _semantic_signature(item) for key, item in value.items()
                if key != 'definition_version' and item is not None and item != [] and item != {}
                and not (key == 'semantic_contract_version' and item == 'legacy-unpartitioned-v1')}
    if isinstance(value, list):
        return [_semantic_signature(item) for item in value]
    return value


def _next_definition_version(previous, current):
    if previous is None:
        return '1.0.0'
    if _semantic_signature(previous) == _semantic_signature(current):
        return previous['definition_version']
    major, minor, patch = (int(part) for part in previous['definition_version'].split('.'))
    return f'{major}.{minor+1}.0'


def build_dictionary(standard_root=STANDARD_ROOT, *, dictionary_version=VERSION,
                     previous_dictionary=None, allow_contract_changes=False,
                     clause_fixed: dict[str, dict] | None = None,
                     platform_policy: dict[str, dict] | None = None):
    if clause_fixed is None:
        clause_fixed = load_clause_fixed_axes(standard_root=standard_root)
    policy = load_platform_policy(standard_root=standard_root) if platform_policy is None else platform_policy
    if previous_dictionary is None:
        previous_path = PLATFORM_ROOT / PREVIOUS
        previous_dictionary = json.loads(previous_path.read_text()) if previous_path.is_file() else None
    previous_metrics = {m['source_datapoint_id']: m for m in (previous_dictionary or {}).get('metrics', [])}
    registry, catalog, packages = load_registry(standard_root)
    profile_path = standard_root / 'profiles/esrs/2023-set1/platform-major.json'
    profile = json.loads(profile_path.read_text())
    selected = profile['source_datapoint_ids']
    if set(selected) != set(policy):
        raise ValueError(f'The platform policy must match every selected original ID: {sorted(set(selected) ^ set(policy))}')
    records = {r['id']: r for r in catalog['records'] if r['record_kind'] == '独立编号条目'}
    modules = {original: m for m in registry['modules'] for original in m['source_datapoint_ids']}
    compiled_metrics = compile_dictionary_metrics(selected=selected, registry=registry, catalog=catalog,
                                                  packages=packages, policies=policy,
                                                  previous_metrics=previous_metrics)
    metrics = []
    for original, compiled in zip(selected, compiled_metrics):
        record, module = records[original], modules[original]
        source_metric = compiled.pop('source_metric')
        ready = compiled['readiness'] == 'ready'
        contract = compiled['source_measurement_contract']
        previous = previous_metrics.get(original)
        metric = compiled
        # The clause owns its fixed values; this builder only reads them.
        metric['fixed_dimensions'] = dict((clause_fixed or {}).get(original, {}))
        source_row_hash = source_metric.provenance.row_sha256 if source_metric else hashlib.sha256(
            canonical_json_bytes(record['raw_values'])).hexdigest()
        if previous:
            # Source/adapter upgrades do not silently change the public display
            # or comparison policy. Only an explicitly allowed scientific change
            # (currently ratio aggregation) may take a new definition version.
            for key in ('description', 'normalization', 'exclusions', 'comparison_conditions'):
                metric[key] = previous.get(key)
        metric.update(source_datapoint_id=original,
                      original_name=record['name'], original_data_type=record['data_type'],
                      topic=record['standard'].strip(), readiness='ready' if ready else 'planned',
                      definition_status='authored_draft' if ready else 'planned_proposal',
                      package_core=True, platform_major=True,
                      standard_binding={
                          'package_id': module['package_id'],
                          'package_version': module['package_version'] if ready else None,
                          'metric_id': source_metric.metric_id if ready else None,
                          'digest': module['source_digest'] if ready else None,
                      },
                      source_measurement_contract=contract,
                      source_reference={
                          'catalog_id': catalog['catalog_id'],
                          'workbook_sha256': catalog['source_sha256'],
                          'row_sha256': source_row_hash,
                          'source_sheet': record['source_sheet'], 'source_row': record['source_row'],
                          'paragraph': record['paragraph'], 'related_ar': record['related_ar'],
                          'conditional_or_alternative': record['conditional_or_alternative'],
                          'may': record['may'],
                      })
        metric['definition_version'] = _next_definition_version(previous, metric)
        if previous:
            for key in ('metric_id', 'label_zh', 'value_type', 'canonical_unit', 'unit_dimension',
                        'required_dimensions', 'comparison_dimensions', 'fixed_dimensions', 'dimension_constraints',
                        'required_context_fields', 'typed_context_constraints', 'description', 'comparison_mode', 'value_role'):
                if metric[key] != previous[key]:
                    raise ValueError(f'Unapproved scientific/display change for {original}: {key}')
            if metric['aggregation'] != previous['aggregation'] and not allow_contract_changes:
                raise ValueError(f'Clause aggregation changed; explicitly author a new contract: {original}')
        metrics.append(metric)
    if len({m['metric_id'] for m in metrics}) != len(metrics):
        raise ValueError('Semantic metric IDs must be unique')
    dictionary = dict(dictionary_version=dictionary_version, comparison_contract_version='1.0.0',
                      profile_id=profile['profile_id'], profile_version=profile['profile_version'],
                      source_catalog_id=catalog['catalog_id'],
                      source_catalog_hash=hashlib.sha256((standard_root / 'catalogs/esrs/2023-set1/ig3-original.json').read_bytes()).hexdigest(),
                      source_sha256=catalog['source_sha256'],
                      selection_terms={'package_core': '标准包内部核心条款', 'platform_major': '平台当前选定的主要条款；主要条款属于包内核心，但不激活全部核心'},
                      metrics=metrics)
    compiled_profile = compile_profile(standard_root, profile_path, topics=['E1', 'E2', 'E3'])
    compiled_profile.update(dictionary_version=dictionary_version,
                            overall_source_datapoint_ids=selected,
                            ready_source_datapoint_ids=[m['source_datapoint_id'] for m in metrics if m['readiness'] == 'ready'],
                            pending_source_datapoint_ids=[m['source_datapoint_id'] for m in metrics if m['readiness'] == 'planned'])
    return dictionary, compiled_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=PLATFORM_ROOT / 'catalog')
    parser.add_argument('--dictionary-version', default=VERSION)
    parser.add_argument('--previous-dictionary', type=Path)
    parser.add_argument('--allow-contract-changes', action='store_true')
    args = parser.parse_args()
    previous = json.loads(args.previous_dictionary.read_text()) if args.previous_dictionary else None
    dictionary, profile = build_dictionary(dictionary_version=args.dictionary_version, previous_dictionary=previous,
                                          allow_contract_changes=args.allow_contract_changes)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Keep the outgoing dictionary as history before it is overwritten: the
    # next build needs its exact definitions to decide version bumps.
    current = args.output_dir / 'metric-dictionary.v1.json'
    if current.is_file():
        try:
            sealed = json.loads(current.read_text())
        except ValueError:
            sealed = None
        version = (sealed or {}).get('dictionary_version')
        if version and version != dictionary['dictionary_version']:
            target = args.output_dir / 'history' / f'metric-dictionary.{version}.json'
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(current.read_text())
    for name, value in [('metric-dictionary.v1.json', dictionary), ('extraction-profile.e1-e3.json', profile)]:
        (args.output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'dictionary_version': dictionary['dictionary_version'], 'major_count': len(dictionary['metrics']),
                      'ready_count': len(profile['ready_source_datapoint_ids']),
                      'pending_count': len(profile['pending_source_datapoint_ids']),
                      'extraction_output_count': profile['output_count']}))


if __name__ == '__main__':
    main()
