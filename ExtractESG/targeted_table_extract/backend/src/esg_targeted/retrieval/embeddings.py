from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

import numpy as np

from esg_targeted.contracts import EvidenceSpan
from esg_targeted.evidence.quality import is_retrieval_eligible
from esg_targeted.io import write_json


LEGACY_DENSE_INDEX_POLICY = "retrieval-eligible-v1"
SEMANTIC_REPRESENTATIVE_INDEX_POLICY = "semantic-representatives-v2"
MAX_SEMANTIC_REPRESENTATIVE_CHARS = 768


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...

    def embed_queries(self, texts: list[str]) -> np.ndarray: ...

    def close(self) -> None: ...


class QwenMlxEmbeddingProvider:
    dimensions = 1024

    def __init__(self, model_path: Path, *, batch_size: int = 16) -> None:
        self.model_path = model_path
        self.batch_size = batch_size
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from mlx_embeddings import load

        self._model, self._tokenizer = load(str(self.model_path))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimensions), dtype=np.float32)
        unique_texts = list(dict.fromkeys(texts))
        unique_matrix = self._embed_batched(unique_texts)
        index_by_text = {text: index for index, text in enumerate(unique_texts)}
        return np.stack([unique_matrix[index_by_text[text]] for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        instructed = [
            "Instruct: Retrieve passages from an ESG report that satisfy the standard disclosure "
            f"requirement.\nQuery: {text}"
            for text in texts
        ]
        return self._embed_batched(instructed)

    def _embed_batched(self, texts: list[str]) -> np.ndarray:
        batches = [
            self._embed(texts[start : start + self.batch_size])
            for start in range(0, len(texts), self.batch_size)
        ]
        return (
            np.concatenate(batches, axis=0)
            if batches
            else np.zeros((0, self.dimensions), dtype=np.float32)
        )

    def _embed(self, texts: list[str]) -> np.ndarray:
        self._ensure_loaded()
        import mlx.core as mx
        from mlx_embeddings import generate

        outputs = generate(self._model, self._tokenizer, texts=texts, max_length=512)
        mx.eval(outputs.text_embeds)
        return np.asarray(outputs.text_embeds, dtype=np.float32)

    def close(self) -> None:
        if self._model is None:
            return
        import mlx.core as mx

        self._model = None
        self._tokenizer = None
        mx.clear_cache()


class CachedEmbeddingIndex:
    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider
        self.cache_hit = False
        self.index_policy = SEMANTIC_REPRESENTATIVE_INDEX_POLICY
        self.retrieval_eligible_span_count = 0
        self.semantic_indexed_span_count = 0

    def build_or_load(
        self,
        spans: list[EvidenceSpan],
        matrix_path: Path,
        metadata_path: Path,
    ) -> np.ndarray:
        digest = self._digest(spans)
        model_path = str(getattr(self.provider, "model_path", "test-provider"))
        if matrix_path.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                metadata.get("index_policy")
                in {
                    LEGACY_DENSE_INDEX_POLICY,
                    SEMANTIC_REPRESENTATIVE_INDEX_POLICY,
                }
                and metadata.get("digest") == digest
                and metadata.get("span_ids") == [span.span_id for span in spans]
                and metadata.get("model_path") == model_path
            ):
                matrix = np.load(matrix_path)
                if matrix.shape == (len(spans), self.provider.dimensions):
                    self.cache_hit = True
                    self.index_policy = str(metadata["index_policy"])
                    self.retrieval_eligible_span_count = int(
                        metadata.get("eligible_span_count", len(spans))
                    )
                    self.semantic_indexed_span_count = int(
                        metadata.get(
                            "semantic_indexed_span_count",
                            self.retrieval_eligible_span_count,
                        )
                    )
                    return matrix

        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        retrieval_eligible_indexes = [
            index for index, span in enumerate(spans) if is_retrieval_eligible(span)
        ]
        semantic_indexes = self._semantic_embedding_indexes(spans)
        matrix = np.zeros((len(spans), self.provider.dimensions), dtype=np.float32)
        if semantic_indexes:
            semantic_matrix = self.provider.embed_documents(
                [spans[index].context_text for index in semantic_indexes]
            )
            matrix[semantic_indexes] = semantic_matrix
        tmp_path = matrix_path.with_suffix(".tmp.npy")
        np.save(tmp_path, matrix)
        tmp_path.replace(matrix_path)
        self.cache_hit = False
        self.index_policy = SEMANTIC_REPRESENTATIVE_INDEX_POLICY
        self.retrieval_eligible_span_count = len(retrieval_eligible_indexes)
        self.semantic_indexed_span_count = len(semantic_indexes)
        write_json(
            metadata_path,
            {
                "digest": digest,
                "span_ids": [span.span_id for span in spans],
                "shape": list(matrix.shape),
                "index_policy": SEMANTIC_REPRESENTATIVE_INDEX_POLICY,
                "eligible_span_count": len(retrieval_eligible_indexes),
                "semantic_indexed_span_count": len(semantic_indexes),
                "model_path": model_path,
            },
        )
        return matrix

    @staticmethod
    def _semantic_embedding_indexes(spans: list[EvidenceSpan]) -> list[int]:
        """Choose one lossless semantic representative per nested IR view.

        Evidence Inventory intentionally retains overlapping representations for
        audit and exact lexical matching: a table row plus every cell, and a text
        block plus its sentences.  Embedding every representation repeats nearly
        identical model work.  The semantic index uses the complete table row when
        it fits the embedding window, or its cells when it does not; likewise it
        uses a short block, or the block's sentences when the block is long.

        Non-selected spans stay in the matrix with a zero vector, so lexical search,
        provenance, packet construction and all stable span ids remain unchanged.
        """

        groups: dict[str, list[tuple[int, EvidenceSpan]]] = {}
        for index, span in enumerate(spans):
            if is_retrieval_eligible(span):
                groups.setdefault(span.context_group_id, []).append((index, span))

        selected: list[int] = []
        for group in groups.values():
            table_rows = [item for item in group if item[1].span_type == "table_row"]
            table_cells = [item for item in group if item[1].span_type == "table_cell"]
            if table_rows:
                short_rows = [
                    item
                    for item in table_rows
                    if len(item[1].context_text) <= MAX_SEMANTIC_REPRESENTATIVE_CHARS
                ]
                long_rows = [item for item in table_rows if item not in short_rows]
                selected.extend(index for index, _ in short_rows)
                if long_rows:
                    selected.extend(
                        index for index, _ in (table_cells or long_rows)
                    )
                continue

            blocks = [item for item in group if item[1].span_type == "block"]
            sentences = [item for item in group if item[1].span_type == "sentence"]
            if blocks:
                short_blocks = [
                    item
                    for item in blocks
                    if len(item[1].context_text) <= MAX_SEMANTIC_REPRESENTATIVE_CHARS
                ]
                long_blocks = [item for item in blocks if item not in short_blocks]
                selected.extend(index for index, _ in short_blocks)
                if long_blocks:
                    selected.extend(
                        index for index, _ in (sentences or long_blocks)
                    )
                continue

            selected.extend(index for index, _ in group)

        return sorted(set(selected))

    @staticmethod
    def _digest(spans: list[EvidenceSpan]) -> str:
        digest = hashlib.sha256()
        for span in spans:
            digest.update(span.span_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(span.context_text.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()
