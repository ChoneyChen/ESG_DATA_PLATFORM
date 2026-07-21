"""Run a reproducible vector-retrieval baseline on the leader Document IR.

This is intentionally a TF-IDF sparse-vector baseline, not a neural embedding
model.  It provides a measurable vector-search interface without inventing
semantic matches or downloading an unverified model.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

ROOT = Path(__file__).resolve().parents[1]
CLAUSE_DATA = ROOT / "data" / "clauses"
DICTIONARY_DATA = ROOT / "data" / "dictionaries"
IR = ROOT / "data" / "fixtures" / "yuexiu_2024_pages_62_67" / "document_ir.json"
OUT = ROOT / "outputs" / "document_ir_vector_candidates.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


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

    aliases_by_concept: dict[str, list[str]] = {}
    for alias in aliases:
        aliases_by_concept.setdefault(alias["concept_id"], []).append(alias["alias"])

    clause_texts = []
    for clause in clauses:
        terms = [
            clause["title"], clause["framework"], clause["standard_id"],
            clause["topic"], clause["subtopic"], clause["metric_type"],
            *aliases_by_concept.get(clause["metric_type"], []),
        ]
        clause_texts.append(norm(" ".join(terms)))

    page_texts = [norm(page.get("text", "")) for page in document["pages"]]
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=1)
    matrix = vectorizer.fit_transform(clause_texts + page_texts)
    clause_matrix = matrix[: len(clauses)]
    page_matrix = matrix[len(clauses) :]
    scores = cosine_similarity(page_matrix, clause_matrix)

    rows = []
    for page_index, page in enumerate(document["pages"]):
        ranked = sorted(enumerate(scores[page_index]), key=lambda item: (-item[1], item[0]))
        for rank, (clause_index, score) in enumerate(ranked[:5], start=1):
            if score <= 0:
                continue
            clause = clauses[clause_index]
            rows.append({
                "document_ir_run_id": document["metadata"].get("run_id", ""),
                "page_id": page["page_id"],
                "page_index": str(page["page_index"]),
                "printed_page_label": str(page.get("printed_page_label", "")),
                "candidate_clause_id": clause["id"],
                "candidate_title": clause["title"],
                "score": f"{score:.6f}",
                "rank": str(rank),
                "retrieval_method": "tfidf_sparse_vector_baseline",
                "candidate_source_url": clause["source_url"],
                "candidate_source_locator": clause["source_locator"],
                "suggested_relation": "candidate_only",
                "review_state": "review_required",
            })

    OUT.parent.mkdir(exist_ok=True)
    fields = list(rows[0]) if rows else [
        "document_ir_run_id", "page_id", "page_index", "printed_page_label",
        "candidate_clause_id", "candidate_title", "score", "rank",
        "retrieval_method", "candidate_source_url", "candidate_source_locator",
        "suggested_relation", "review_state",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"pages={len(page_texts)} clauses={len(clauses)} candidates={len(rows)}")
    print("method=tfidf_sparse_vector_baseline (not neural embeddings)")
    print(OUT)


if __name__ == "__main__":
    main()
