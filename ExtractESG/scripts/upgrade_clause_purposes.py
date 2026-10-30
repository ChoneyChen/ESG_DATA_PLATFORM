#!/usr/bin/env python3
"""Explicit one-time source upgrade. Frozen run snapshots are never traversed."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "ClauseESG/src"))
sys.path.insert(0, str(ROOT / "contracts/src"))
from esg_clauses.repository import ClauseRepository, _write_json
from esg_clauses.contract import ClausePurposes, EnrichmentPolicy, TargetPolicy, ComparisonPolicy


def upgrade(root):
    repo = ClauseRepository(root)
    changed = []
    with repo._locked(write=True):
        catalog = repo._catalog()
        for entry in catalog["clauses"]:
            clause = repo._get(entry["clause_key"], catalog)
            if clause.schema_version == "clause-contract-1.2" and clause.value.comparison_axes is None:
                continue
            fields = clause.organization.fields
            purposes = ClausePurposes(
                enrichment=EnrichmentPolicy(identity_axes=list(dict.fromkeys(
                    f.binding.key for f in fields if f.binding.source in {"identity", "dimension"}
                ))),
                target_scope=TargetPolicy(discriminating_axes=list(dict.fromkeys([
                    "value_role", "aggregation", *[axis for scope in clause.output_scopes for axis in scope.axes]
                ]))),
                comparison=ComparisonPolicy(required_axes=clause.value.comparison_axes or []),
            )
            organization = clause.organization.model_copy(update={"fields": [
                f.model_copy(update={"required_for_confirmation": False})
                if f.binding.source in {"identity", "dimension"} else f for f in fields
            ]})
            updated = clause.model_copy(update={"schema_version": "clause-contract-1.2", "purposes": clause.purposes or purposes,
                "value": clause.value.model_copy(update={"comparison_axes": None}), "organization": organization}).seal().verify()
            _write_json(repo.root / entry["path"] / "clause.json", updated.model_dump(mode="json"))
            changed.append(clause.clause_key)
        repo._reindex()
    return {"upgraded": len(changed), "frozen_runs_modified": 0}


if __name__ == "__main__":
    print(json.dumps(upgrade(ROOT.parent / "ClauseESG/clauses")))
