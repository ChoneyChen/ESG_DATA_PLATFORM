from __future__ import annotations

import importlib.util
import math
import time
from dataclasses import dataclass
from typing import Protocol

from esg_v2.targeted.contracts import LocalInferenceRecord


class EmbeddingBackend(Protocol):
    model_id: str

    def encode_passages(self, texts: list[str]) -> list[list[float]]: ...

    def encode_queries(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbeddingBackend:
    def __init__(self, model_id: str, *, allow_download: bool):
        if importlib.util.find_spec("sentence_transformers") is None:
            raise RuntimeError("sentence-transformers is not installed; install esg-v2[semantic]")
        from sentence_transformers import SentenceTransformer

        self.model_id = model_id
        self.model = SentenceTransformer(model_id, local_files_only=not allow_download)

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        values = self.model.encode(
            [f"passage: {text}" for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, row)) for row in values]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        values = self.model.encode(
            [f"query: {text}" for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, row)) for row in values]


@dataclass
class SemanticSearchResult:
    rankings: dict[str, list[tuple[str, float]]]
    record: LocalInferenceRecord


def semantic_rankings(
    backend: EmbeddingBackend,
    *,
    atom_texts: dict[str, str],
    queries: dict[str, str],
    top_k: int,
) -> SemanticSearchResult:
    started = time.monotonic()
    atom_ids = list(atom_texts)
    passage_vectors = backend.encode_passages([atom_texts[atom_id] for atom_id in atom_ids])
    query_ids = list(queries)
    query_vectors = backend.encode_queries([queries[query_id] for query_id in query_ids])
    rankings: dict[str, list[tuple[str, float]]] = {}
    for query_id, query_vector in zip(query_ids, query_vectors, strict=True):
        scored = [
            (atom_id, _cosine(query_vector, passage_vector))
            for atom_id, passage_vector in zip(atom_ids, passage_vectors, strict=True)
        ]
        rankings[query_id] = sorted(scored, key=lambda item: (-item[1], item[0]))[:top_k]
    return SemanticSearchResult(
        rankings=rankings,
        record=LocalInferenceRecord(
            component="semantic_retrieval",
            backend="sentence-transformers",
            model_id=backend.model_id,
            status="succeeded",
            input_count=len(atom_ids) + len(query_ids),
            duration_ms=(time.monotonic() - started) * 1000,
            details={"atom_count": len(atom_ids), "query_count": len(query_ids)},
        ),
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
