"""Write one directory package per clause from immutable packages and policy.

    python scripts/build_clause_catalog.py           # write clause packages
    python scripts/build_clause_catalog.py --check   # fail if any file is stale

Each clause owns its contract, retrieval terms, evidence requirements,
acceptance samples and provenance. Packages only reference clause identity and
order; editing a clause touches one directory.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from esg_standard_packages.clause_build import build_profiles, load_dictionary  # noqa: E402
from esg_standard_packages.io import pretty_json_bytes  # noqa: E402

GENERATOR = "clause-package-builder-1.0"


def clause_id(package_id: str, version: str, source_id: str) -> str:
    return f"{package_id}@{version}:{source_id}"


def split_profile(entry: dict, *, package_id: str, version: str, source_digest: str,
                  standard: dict, generated_at: str) -> dict[str, dict]:
    profile = entry["profile"]
    clause = {key: profile[key] for key in (
        "schema_version", "clause_id", "package_id", "package_version", "source_datapoint_id",
        "metric_id", "labels", "description", "positive_definition", "distinguish_from",
        "exclusions", "value_type", "canonical_unit", "unit_dimension", "value_origin",
        "output_scopes", "publication", "contract_digest")}
    clause["disclosure_requirement"] = entry["disclosure_requirement"]
    clause["obligation_class"] = entry["obligation_class"]
    clause["disclosure_role"] = entry["disclosure_role"]
    retrieval = {"schema_version": "clause-retrieval-1.0", "clause_id": profile["clause_id"],
                 **profile["retrieval"]}
    evidence = {"schema_version": "clause-evidence-1.0", "clause_id": profile["clause_id"],
                "requirements": profile["evidence"]}
    acceptance = {"schema_version": "clause-acceptance-profile-1.0", "clause_id": profile["clause_id"],
                  **profile["acceptance"]}
    provenance = {"schema_version": "clause-provenance-1.0", "clause_id": profile["clause_id"],
                  "package_id": package_id, "package_version": version,
                  "package_source_digest": source_digest, "paragraph": entry["paragraph"],
                  "source_refs": entry["source_refs"],
                  # The standard identity is the package; repeating its manifest in
                  # every clause would multiply the catalog without adding evidence.
                  "standard_ref": {"disclosure_requirement": standard.get("disclosure_requirement"),
                                   "standard_family": standard.get("standard_family")},
                  "generated_by": GENERATOR, "generated_at": generated_at,
                  "contract_digest": profile["contract_digest"]}
    return {"clause": clause, "retrieval": retrieval, "evidence": evidence,
            "acceptance": acceptance, "provenance": provenance}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standards-root", default=str(ROOT))
    parser.add_argument("--dictionary",
                        default=str(ROOT.parents[1] / "PlatESG/catalog/metric-dictionary.v1.json"))
    parser.add_argument("--out", default=str(ROOT / "clauses"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--include-drafts", action="store_true",
                        help="Apply validated clause drafts as overrides before writing")
    args = parser.parse_args()
    root, out = Path(args.standards_root), Path(args.out)
    dictionary = load_dictionary(Path(args.dictionary))
    overrides = {}
    if args.include_drafts:
        from esg_standard_packages.clause_drafts import ClauseDraftStore
        store = ClauseDraftStore(root)
        for manifest in store.list():
            if manifest.get("status") != "validated":
                continue
            base = manifest.get("base") or {}
            parts = store.load(manifest["draft_id"])
            overrides[(base.get("package_id"), base.get("package_version"),
                       base.get("source_datapoint_id"))] = parts
        print(f"drafts_applied={len(overrides)}")
    payloads = build_profiles(root, dictionary, overrides)
    generated_at = "2026-10-28T00:00:00+00:00"
    catalog_packages, stale, written = [], 0, 0
    for item in payloads:
        package_dir = out / item["package_id"] / item["package_version"]
        clauses = []
        for entry in item["profiles"]:
            profile = entry["profile"]
            parts = split_profile(entry, package_id=item["package_id"], version=item["package_version"],
                                  source_digest=item["source_digest"], standard=item["standard"],
                                  generated_at=generated_at)
            relative = Path(item["package_id"]) / item["package_version"] / profile["source_datapoint_id"]
            for name, body in parts.items():
                target = out / relative / f"{name}.json"
                content = pretty_json_bytes(body)
                if not target.exists() or target.read_bytes() != content:
                    stale += 1
                    if not args.check:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                        written += 1
            clauses.append({"source_datapoint_id": profile["source_datapoint_id"],
                            "metric_id": profile["metric_id"],
                            "path": str(relative.as_posix()),
                            "contract_digest": profile["contract_digest"]})
        catalog_packages.append({"package_id": item["package_id"],
                                 "package_version": item["package_version"],
                                 "source_digest": item["source_digest"],
                                 "clause_count": len(clauses),
                                 "skipped": item["skipped"],
                                 "clauses": clauses})
    catalog = {"schema_version": "clause-catalog-1.0", "generated_by": GENERATOR,
               "generated_at": generated_at, "drafts_applied": sorted(
                   f"{key[0]}@{key[1]}:{key[2]}" for key in overrides), "packages": catalog_packages}
    catalog_path = out / "catalog.json"
    content = pretty_json_bytes(catalog)
    if not catalog_path.exists() or catalog_path.read_bytes() != content:
        stale += 1
        if not args.check:
            catalog_path.parent.mkdir(parents=True, exist_ok=True)
            catalog_path.write_bytes(content)
            written += 1
    total = sum(item["clause_count"] for item in catalog_packages)
    print(f"packages={len(catalog_packages)} clauses={total} files_written={written} stale={stale}")
    if args.check and stale:
        print(f"STALE: {stale} clause files differ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
