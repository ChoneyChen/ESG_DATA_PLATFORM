"""Compare lexical and sparse-vector candidates and create a review queue."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def read(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def key(row: dict[str, str]) -> tuple[str, str]:
    return row["printed_page_label"], row["candidate_clause_id"]


def main() -> None:
    lexical = {key(row): row for row in read(OUT / "document_ir_retrieval_candidates.csv")}
    vector = {key(row): row for row in read(OUT / "document_ir_vector_candidates.csv")}
    all_keys = sorted(set(lexical) | set(vector), key=lambda item: (int(item[0] or 0), item[1]))

    rows = []
    for item in all_keys:
        lrow = lexical.get(item, {})
        vrow = vector.get(item, {})
        methods = []
        if lrow:
            methods.append("lexical")
        if vrow:
            methods.append("tfidf_vector")
        rows.append({
            "printed_page_label": item[0],
            "candidate_clause_id": item[1],
            "candidate_title": lrow.get("candidate_title") or vrow.get("candidate_title", ""),
            "lexical_score": lrow.get("score", ""),
            "lexical_rank": lrow.get("rank", ""),
            "vector_score": vrow.get("score", ""),
            "vector_rank": vrow.get("rank", ""),
            "retrieved_by": "+".join(methods),
            "agreement": "both_methods" if len(methods) == 2 else "single_method",
            "review_state": "review_required",
            "evidence_locator": "",
            "review_decision": "",
            "review_notes": "",
        })

    path = OUT / "retrieval_method_comparison_review_queue.csv"
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["printed_page_label"])
        writer.writeheader()
        writer.writerows(rows)

    both = sum(row["agreement"] == "both_methods" for row in rows)
    single = len(rows) - both
    print(f"unique_candidates={len(rows)} both_methods={both} single_method={single}")
    print(path)


if __name__ == "__main__":
    main()
