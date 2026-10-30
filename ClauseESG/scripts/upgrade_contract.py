#!/usr/bin/env python3
"""Upgrade current clause sources only; historical run snapshots are untouched."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT.parent / "ExtractESG/contracts/src")]
from esg_clauses.repository import ClauseRepository, _write_json


def upgrade(root):
    repository = ClauseRepository(root)
    changes = []
    with repository._locked(write=True):
        catalog = repository._catalog()
        for entry in catalog["clauses"]:
            clause = repository._get(entry["clause_key"], catalog)
            if clause.readiness == "structural":
                continue
            if clause.purposes is None:
                raise ValueError("Upgrade pre-purpose authoring sources explicitly first")
            axes = list(dict.fromkeys([
                *clause.purposes.enrichment.identity_axes,
                *clause.value.identity_axes,
                *clause.purposes.comparison.required_axes,
            ]))
            purposes = clause.purposes.model_copy(update={
                "capture": clause.purposes.capture.model_copy(update={"period_policy": "all_reported"}),
                "enrichment": clause.purposes.enrichment.model_copy(update={"identity_axes": axes}),
            })
            updated = clause.model_copy(update={"schema_version": "clause-contract-1.3", "purposes": purposes}).seal().verify()
            if updated.clause_digest != clause.clause_digest:
                _write_json(repository.root / entry["path"] / "clause.json", updated.model_dump(mode="json"))
                changes.append({"clause_key": clause.clause_key,
                    "from_digest": clause.clause_digest, "to_digest": updated.clause_digest,
                    "optional_read_axes_added": sorted(set(axes) - set(clause.purposes.enrichment.identity_axes))})
        repository._reindex()
    return {"schema_version": "clause-source-upgrade-1.0", "upgraded": len(changes),
            "frozen_runs_modified": 0, "changes": changes}


if __name__ == "__main__":
    result = upgrade(ROOT / "clauses")
    print(json.dumps({key: value for key, value in result.items() if key != "changes"}))
