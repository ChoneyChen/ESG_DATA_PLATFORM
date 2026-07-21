"""Run embedding retrieval through a configured OpenAI-compatible cloud API.

Required environment variables (never put secrets in CSV or source files):
  EMBEDDING_API_URL   e.g. https://provider.example/v1/embeddings
  EMBEDDING_API_KEY
  EMBEDDING_MODEL

The endpoint must accept {"model": ..., "input": [...]} and return
{ "data": [{"index": 0, "embedding": [...]}, ...] }.
"""
from pathlib import Path
import csv
import json
import math
import os
import re
from urllib.request import Request, urlopen
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CLAUSE_DATA = ROOT / "data" / "clauses"
DICTIONARY_DATA = ROOT / "data" / "dictionaries"
IR = ROOT / "data" / "fixtures" / "yuexiu_2024_pages_62_67" / "document_ir.json"
OUT = ROOT / "outputs" / "document_ir_cloud_embedding_candidates.csv"

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def norm(value):
    return re.sub(r"\s+", " ", value or "").strip()

def embed(api_url, api_key, model, texts):
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = Request(api_url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"
    })
    with urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = sorted(payload["data"], key=lambda item: item.get("index", 0))
    return [item["embedding"] for item in data]

def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0

def main():
    api_url = os.getenv("EMBEDDING_API_URL", "")
    api_key = os.getenv("EMBEDDING_API_KEY", "")
    model = os.getenv("EMBEDDING_MODEL", "")
    if not all((api_url, api_key, model)):
        raise SystemExit("Missing EMBEDDING_API_URL, EMBEDDING_API_KEY, or EMBEDDING_MODEL; no cloud call was made.")
    if urlparse(api_url).scheme.lower() != "https":
        raise SystemExit("EMBEDDING_API_URL must use HTTPS; no cloud call was made.")
    if os.getenv("EMBEDDING_ALLOW_DATA_UPLOAD", "") != "YES":
        raise SystemExit(
            "Set EMBEDDING_ALLOW_DATA_UPLOAD=YES only after approving the provider and report-text upload; "
            "no cloud call was made."
        )
    with IR.open(encoding="utf-8") as f: document = json.load(f)
    clauses = read_csv(CLAUSE_DATA / "standard_clauses_sample.csv") + read_csv(CLAUSE_DATA / "hkex_climate_clauses_sample.csv") + read_csv(CLAUSE_DATA / "cross_framework_core_clauses_sample.csv")
    aliases = read_csv(DICTIONARY_DATA / "synonyms_sample.csv") + read_csv(DICTIONARY_DATA / "official_report_aliases_sample.csv")
    by_concept = {}
    for a in aliases: by_concept.setdefault(a["concept_id"], []).append(a["alias"])
    clause_texts = [norm(" ".join([c["title"], c["framework"], c["standard_id"], c["topic"], c["subtopic"], c["metric_type"], *by_concept.get(c["metric_type"], [])])) for c in clauses]
    page_texts = [norm(p.get("text", "")) for p in document["pages"]]
    vectors = embed(api_url, api_key, model, clause_texts + page_texts)
    clause_vectors = vectors[:len(clauses)]; page_vectors = vectors[len(clauses):]
    rows = []
    for page, pv in zip(document["pages"], page_vectors):
        ranked = sorted(enumerate((cosine(pv, cv) for cv in clause_vectors)), key=lambda x: -x[1])[:5]
        for rank, (i, score) in enumerate(ranked, 1):
            if score <= 0:
                continue
            rows.append({"document_ir_run_id": document["metadata"].get("run_id", ""), "page_id": page.get("page_id", ""), "page_index": str(page.get("page_index", "")), "printed_page_label": page.get("printed_page_label", ""), "candidate_clause_id": clauses[i]["id"], "candidate_title": clauses[i]["title"], "score": f"{score:.6f}", "rank": str(rank), "retrieval_method": "cloud_embedding", "embedding_model": model, "candidate_source_url": clauses[i]["source_url"], "candidate_source_locator": clauses[i]["source_locator"], "suggested_relation": "candidate_only", "review_state": "review_required"})
    OUT.parent.mkdir(exist_ok=True)
    fields = list(rows[0]) if rows else [
        "document_ir_run_id", "page_id", "page_index", "printed_page_label",
        "candidate_clause_id", "candidate_title", "score", "rank",
        "retrieval_method", "embedding_model", "candidate_source_url",
        "candidate_source_locator", "suggested_relation", "review_state",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(f"pages={len(page_texts)} clauses={len(clauses)} candidates={len(rows)} model={model}")
    print(OUT)

if __name__ == "__main__": main()
