#!/usr/bin/env python3
"""Validate/reindex independent clauses and publish machine-readable module contracts."""

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "ClauseESG/src"))
for name in ("contracts", "shared_runtime"):
    sys.path.insert(0, str(ROOT / name / "src"))
from esg_reports.contract import ReportResource, BatchManifest
from esg_clauses.repository import ClauseRepository
from esg_clauses.contract import ClauseContract
from esg_contracts.facts import SourceFact
from esg_contracts.results import FactBundle
from esg_contracts.exports import ExportManifest, IngestionReceipt
from esg_contracts.data_kinds.quantitative import QuantitativeValue
from esg_contracts.data_kinds.qualitative import QualitativeValue
from esg_data.organization import OrganizationSpec, OrganizedDataset
from esg_data.periods import ReportedPeriod
from esg_contracts.evidence import EvidenceStructure, ObjectSearchPage
from export_console_types import check as check_console_types


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    repo = ClauseRepository(ROOT.parent / "ClauseESG/clauses")
    if args.write:
        repo.reindex()
    rows = repo.catalog()["clauses"]
    for row in rows:
        repo.get(row["clause_key"]).verify()
    schemas = {
        "reported-period": ReportedPeriod,
        "evidence-structure": EvidenceStructure,
        "object-search-page": ObjectSearchPage,
        "extraction-export": ExportManifest,
        "extraction-receipt": IngestionReceipt,
        "fact-bundle": FactBundle,
        "report-resource": ReportResource,
        "report-resource-batch": BatchManifest,
        "clause": ClauseContract,
        "quantitative": QuantitativeValue,
        "qualitative": QualitativeValue,
        "source-fact": SourceFact,
        "organization-definition": OrganizationSpec,
        "organized-extraction": OrganizedDataset,
    }
    target = ROOT / "contracts/schemas"
    target.mkdir(exist_ok=True)
    for name, model in schemas.items():
        body = (
            json.dumps(
                model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n"
        )
        path = ROOT.parent / "ClauseESG/schemas/clause.schema.json" if name == "clause" else target / (name + ".schema.json")
        if args.write:
            path.write_text(body, encoding="utf-8")
        elif not path.is_file() or path.read_text() != body:
            raise SystemExit("Contract schema differs: " + str(path))
    console = check_console_types(args.write)
    print(
        json.dumps(
            {
                "clauses": len(rows),
                "blocked": sum(r["readiness"] == "blocked" for r in rows),
                "selection_sizes": {k: len(v) for k, v in repo.selections().items()},
                "schema_files": len(schemas),
                "console_types": console["models"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
