"""Attach source-text snippets to retrieval candidates for human review.

This script only quotes text already present in the leader's Document IR. It
does not decide whether a disclosure satisfies a standard.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUSE_DATA = ROOT / "data" / "clauses"
DICTIONARY_DATA = ROOT / "data" / "dictionaries"
IR_DIR = ROOT / "data" / "fixtures" / "yuexiu_2024_pages_62_67"
INFILE = ROOT / "outputs" / "retrieval_method_comparison_review_queue.csv"
OUTFILE = ROOT / "outputs" / "evidence_review_queue.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def main() -> None:
    with (IR_DIR / "document_ir.json").open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    pages = {str(p.get("printed_page_label", "")): p for p in document["pages"]}

    clauses = []
    clauses += read_csv(CLAUSE_DATA / "standard_clauses_sample.csv")
    clauses += read_csv(CLAUSE_DATA / "hkex_climate_clauses_sample.csv")
    clauses += read_csv(CLAUSE_DATA / "cross_framework_core_clauses_sample.csv")
    clause_by_id = {row["id"]: row for row in clauses}
    aliases = read_csv(DICTIONARY_DATA / "synonyms_sample.csv")
    alias_path = DICTIONARY_DATA / "official_report_aliases_sample.csv"
    if alias_path.exists():
        aliases += read_csv(alias_path)
    aliases_by_concept: dict[str, list[str]] = {}
    for row in aliases:
        aliases_by_concept.setdefault(row["concept_id"], []).append(row["alias"])

    output = []
    for row in read_csv(INFILE):
        page = pages.get(row["printed_page_label"])
        clause = clause_by_id.get(row["candidate_clause_id"], {})
        page_index = int(page["page_index"]) + 1 if page else 0
        source = IR_DIR / "canonical_markdown" / f"page_{page_index:04d}.md"
        text = source.read_text(encoding="utf-8") if source.exists() else ""
        terms = [clause.get("title", ""), *aliases_by_concept.get(clause.get("metric_type", ""), [])]
        terms = [clean(term) for term in terms if clean(term)]
        match_line = 0
        snippet = ""
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(term.lower() in line.lower() for term in terms):
                match_line = line_no
                snippet = clean(line)
                break
        enriched = dict(row)
        enriched["ir_source_file"] = source.relative_to(ROOT).as_posix()
        enriched["ir_source_line"] = str(match_line) if match_line else ""
        enriched["evidence_snippet"] = snippet
        enriched["evidence_status"] = "source_text_match" if snippet else "no_direct_text_match"
        output.append(enriched)

    fields = list(output[0]) if output else []
    with OUTFILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    matched = sum(row["evidence_status"] == "source_text_match" for row in output)
    print(f"candidates={len(output)} source_text_matches={matched} no_direct_text_match={len(output)-matched}")
    print(OUTFILE)


if __name__ == "__main__":
    main()
