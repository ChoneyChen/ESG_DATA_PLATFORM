#!/usr/bin/env python3
"""Publish database-owned value contracts; consumers install the generated types SDK."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"src"),str(ROOT.parent/"ExtractESG/contracts/src"),str(ROOT.parent/"tools")]
from contract_types import declarations
from esg_database.contracts import CaptureFactPage
from esg_database.products_contracts import (Observation,ProjectionSummary,ProjectionStatus,SourceSelectionPolicy,DatasetRequest,
    DatasetPreview,PublishRequest,WithdrawalRequest,RestatementRequest,DatasetVersion,ObservationPage,DatasetPage,ProjectionPage,ProductDefinition,CaptureProductState,PreviewQuery,ObservationSource)

MODELS={model.__name__:model for model in (CaptureFactPage,Observation,ProjectionSummary,ProjectionStatus,SourceSelectionPolicy,DatasetRequest,
    DatasetPreview,PublishRequest,WithdrawalRequest,RestatementRequest,DatasetVersion,ObservationPage,DatasetPage,ProjectionPage,ProductDefinition,CaptureProductState,PreviewQuery,ObservationSource)}


def check(write=False):
    root=ROOT/"contracts"
    files={root/"index.d.ts":declarations(MODELS,"database/scripts/export_contracts.py")}
    for name,model in MODELS.items():
        files[root/"schemas"/(name+".schema.json")]=json.dumps(model.model_json_schema(),ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    for path,body in files.items():
        if write:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body,encoding="utf-8")
        elif not path.is_file() or path.read_text()!=body:
            raise SystemExit("Database contract differs: "+str(path))
    return {"models":len(MODELS),"files":len(files)}


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--write",action="store_true")
    print(json.dumps(check(parser.parse_args().write)))
