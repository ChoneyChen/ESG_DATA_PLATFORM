"""One model-free adapter for current direct Core, at any execution stage."""
from pathlib import Path

from esg_targeted.contracts import EvidencePacket
from esg_targeted.evidence.target_cells import target_cell_contracts
from esg_targeted.ids import safe_filename
from esg_targeted.io import read_json
from esg_targeted.report_facts import adapt_direct_core_facts
from esg_targeted.results.spec import RECORD_LAYOUT


def adapt_durable_direct_core_facts(root: Path, records, package, *, request,
                                    bound_core_ids, model_executions=None):
    """Use accepted Core citations and its frozen packet; never infer a fact.

    Current shared projections take precedence. Rebuilding a direct projection
    is deterministic and uses the same target headers and model identity during
    an interrupted stage and finalization, so resume cannot create a new source.
    """
    unbound_metrics = {row['metric_id'] for name,key in (('quantitative_observations','observation_id'),
        ('qualitative_assertions','assertion_id')) for row in records.get(name,[])
        if row[key] not in bound_core_ids and row.get('value_origin') != 'derived'}
    if not unbound_metrics:
        return [], []
    targets = {}
    for path in sorted((root / 'packets').glob('*.regions.json')):
        if path.name.removesuffix('.regions.json') not in {safe_filename(mid) for mid in unbound_metrics}:
            continue
        for region in read_json(path).get('regions', []):
            packet = EvidencePacket.model_validate(region)
            targets.update({target.cell_id:target for target in target_cell_contracts(packet)})
    if model_executions is None:
        model_executions = []
        for metric_id in sorted(unbound_metrics):
            path = root / 'decisions' / f'{safe_filename(metric_id)}.json'
            if not path.is_file():
                continue
            for attempt in read_json(path).get('attempts', []):
                if attempt.get('provider'):
                    model_executions.append({'metric_id':metric_id,'provider':attempt['provider'],
                        'model_id':attempt.get('model_id') or 'unknown'})
    return adapt_direct_core_facts(records, package, bound_core_ids=bound_core_ids,
        request=request, model_executions=model_executions, target_cells=targets)


def partition_metric_records(records, metric_ids):
    """Partition evidence through real Core parents; evidence has no metric ID."""
    ids = set(metric_ids)
    result = {name:[row for row in records.get(name,[]) if row.get('metric_id') in ids]
              for name in RECORD_LAYOUT if name != 'evidence_references'}
    parent_ids = {value for rows in result.values() for row in rows for key,value in row.items()
                  if key in {'task_id','observation_id','assertion_id','attribute_id','dimension_value_id'}}
    result['evidence_references'] = [row for row in records.get('evidence_references', [])
                                   if row.get('source_record_id') in parent_ids]
    return result
