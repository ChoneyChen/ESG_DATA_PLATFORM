#!/usr/bin/env python3
"""Validate/reindex independent clauses and publish machine-readable module contracts."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for name in ('contracts','shared_runtime'):sys.path.insert(0,str(ROOT/name/'src'))
from esg_reports.contract import ReportResource,BatchManifest
from esg_clauses.repository import ClauseRepository
from esg_clauses.contract import ClauseContract
from esg_targeted.pipeline_contracts import SourceFact
from esg_targeted.data_kinds.quantitative import QuantitativeValue
from esg_targeted.data_kinds.qualitative import QualitativeValue

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');args=parser.parse_args()
    repo=ClauseRepository(ROOT/'standard')
    if args.write:repo.reindex()
    rows=repo.catalog()['clauses']
    for row in rows:repo.get(row['clause_key']).verify()
    schemas={'report-resource':ReportResource,'report-resource-batch':BatchManifest,'clause':ClauseContract,'quantitative':QuantitativeValue,'qualitative':QualitativeValue,'source-fact':SourceFact}
    target=ROOT/'contracts/schemas';target.mkdir(exist_ok=True)
    for name,model in schemas.items():
        body=json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2,sort_keys=True)+'\n';path=target/(name+'.schema.json')
        if args.write:path.write_text(body,encoding='utf-8')
        elif not path.is_file() or path.read_text()!=body:raise SystemExit('Contract schema differs: '+str(path))
    print(json.dumps({'clauses':len(rows),'blocked':sum(r['readiness']=='blocked' for r in rows),'selection_sizes':{k:len(v) for k,v in repo.selections().items()},'schema_files':len(schemas)},ensure_ascii=False))
if __name__=='__main__':main()
