#!/usr/bin/env python3
"""Evaluate a frozen result through its API against explicitly annotated evidence."""
import argparse
import json
from pathlib import Path
from urllib.request import build_opener, ProxyHandler


def evaluate(bundle, benchmark):
    source = bundle['document'].get('source_sha256') or (bundle['document'].get('report_resource') or {}).get('sha256')
    if source != benchmark['source_sha256']:
        raise ValueError('Benchmark belongs to a different PDF')
    by_id = {f['source_fact_id']: f for f in bundle['facts']}
    links = {m['source_fact_id']: m for m in bundle['clause_mappings'] if m['clause_key'] == benchmark['clause_key']}
    rows = []
    for expected in benchmark['expected']:
        matches = [f for f in by_id.values() if expected['label'] in (f.get('source_row_label') or '') and
                   f['value'].get('value_raw') == expected['value_raw'] and
                   f['identity']['reporting_period'].get('value') == expected['period'] and
                   (not expected.get('column_group') or expected['column_group'] in ' '.join(e['excerpt'] for e in f['evidence'] if e['role']=='column_header')) and
                   any(e.get('page_index') == expected.get('page_index') and e.get('role') == 'value' for e in f['evidence'])]
        rows.append({**expected, 'retained': len(matches) == 1,
            'unit_correct': len(matches) == 1 and matches[0]['value'].get('unit_raw') == expected['unit_raw'],
            'period_correct': len(matches) == 1 and matches[0]['identity']['reporting_period'].get('value') == expected['period'],
            'relation_correct': len(matches) == 1 and links.get(matches[0]['source_fact_id'], {}).get('match') == expected['relation']})
    false_digits = [f['source_fact_id'] for f in by_id.values() for bad in benchmark['forbidden_values']
                    if f['value'].get('value_raw') == bad['raw'] and any(e['object_id'] == bad['object_id'] and e['role'] == 'value' for e in f['evidence'])]
    return {'schema_version': 'scientific-benchmark-result-1.0', 'run_id': bundle['run_id'],
        'source_sha256': source, 'expected_measurements': len(rows),
        'retained_measurements': sum(r['retained'] for r in rows),
        'correct_target_relations': sum(r['retained'] and r['relation_correct'] for r in rows if r['relation'] == 'match'),
        'correct_other_relations': sum(r['retained'] and r['relation_correct'] for r in rows if r['relation'] == 'different'),
        'false_formula_or_footnote_facts': false_digits, 'rows': rows, 'limits': benchmark['limits']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:18180')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--benchmark', type=Path, default=Path(__file__).resolve().parents[1]/'extraction/tests/fixtures/scientific/catl-air-2023-2025.json')
    args = parser.parse_args()
    import re
    if not re.fullmatch(r'extract-[a-zA-Z0-9_-]+', args.run_id):
        raise ValueError('Invalid run identity')
    with build_opener(ProxyHandler({})).open(args.base_url.rstrip('/')+'/api/runs/'+args.run_id+'/facts', timeout=15) as response:
        bundle = json.load(response)
    fixture = args.benchmark
    result = evaluate(bundle, json.loads(fixture.read_text()))
    content = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.write_text(content)
    print(content)


if __name__ == '__main__':
    main()
