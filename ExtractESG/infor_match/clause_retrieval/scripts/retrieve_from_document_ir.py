"""Run the first-pass clause candidate retrieval on the leader's Document IR.

The Document IR is treated as a read-only input. This script creates candidates
only; it does not assert that the company satisfies any standard.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUSE_DATA = ROOT / "data" / "clauses"
DICTIONARY_DATA = ROOT / "data" / "dictionaries"
IR = ROOT / "data" / "fixtures" / "yuexiu_2024_pages_62_67" / "document_ir.json"
OUT = ROOT / "outputs" / "document_ir_retrieval_candidates.csv"


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().strip())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    with IR.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    clauses = read_csv(CLAUSE_DATA / "standard_clauses_sample.csv")
    clauses += read_csv(CLAUSE_DATA / "hkex_climate_clauses_sample.csv")
    clauses += read_csv(CLAUSE_DATA / "cross_framework_core_clauses_sample.csv")
    aliases = read_csv(DICTIONARY_DATA / "synonyms_sample.csv")
    alias_path = DICTIONARY_DATA / "official_report_aliases_sample.csv"
    if alias_path.exists():
        aliases += read_csv(alias_path)
    rows: list[dict[str, str]] = []
    for page in document["pages"]:
        query = norm(page.get("text", ""))
        if not query:
            continue
        scored = []
        for clause in clauses:
            matched = []
            points = 0.0
            for alias in aliases:
                if alias["concept_id"] != clause["metric_type"]:
                    continue
                term = norm(alias["alias"])
                if term and term in query:
                    points += 3.0
                    matched.append(alias["alias"])
            title = norm(clause["title"])
            if title and title in query:
                points += 5.0
                matched.append(clause["title"])
            if points:
                scored.append((points, clause, list(dict.fromkeys(matched))))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        for rank, (points, clause, matched) in enumerate(scored[:5], start=1):
            rows.append(
                {
                    "document_ir_run_id": document["metadata"].get("run_id", ""),
                    "page_id": page["page_id"],
                    "page_index": str(page["page_index"]),
                    "printed_page_label": str(page.get("printed_page_label", "")),
                    "candidate_clause_id": clause["id"],
                    "candidate_title": clause["title"],
                    "score": f"{points:.1f}",
                    "rank": str(rank),
                    "matched_terms": " | ".join(matched),
                    "candidate_source_url": clause["source_url"],
                    "candidate_source_locator": clause["source_locator"],
                    "suggested_relation": "candidate_only",
                    "review_state": "review_required",
                }
            )
    OUT.parent.mkdir(exist_ok=True)
    fields = [
        "document_ir_run_id", "page_id", "page_index", "printed_page_label",
        "candidate_clause_id", "candidate_title", "score", "rank",
        "matched_terms", "candidate_source_url", "candidate_source_locator",
        "suggested_relation", "review_state",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"pages={len(document['pages'])} clauses={len(clauses)} candidates={len(rows)}")
    print(OUT)


if __name__ == "__main__":
    main()
