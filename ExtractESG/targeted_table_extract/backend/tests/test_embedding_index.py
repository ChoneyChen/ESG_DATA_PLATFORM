from __future__ import annotations

import json

import numpy as np

from esg_targeted.contracts import EvidenceInventory, EvidenceSpan, MetricQuery, QueryIntent
from esg_targeted.retrieval.embeddings import (
    CachedEmbeddingIndex,
    LEGACY_DENSE_INDEX_POLICY,
    SEMANTIC_REPRESENTATIVE_INDEX_POLICY,
)
from esg_targeted.storage.artifacts import ArtifactStore
from esg_targeted.storage.index_assets import SemanticIndexAssetCatalog
from esg_targeted.retrieval.hybrid import HybridRetriever


class RecordingProvider:
    dimensions = 3

    def __init__(self, model_path) -> None:
        self.model_path = model_path
        self.document_batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        self.document_batches.append(list(texts))
        return np.ones((len(texts), self.dimensions), dtype=np.float32)


def _span(sequence: int, span_type: str, group: str, context: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"span-{sequence}",
        document_id="doc-1",
        ir_run_id="ir-1",
        ir_revision=1,
        span_type=span_type,
        text=context,
        context_text=context,
        page_index=1,
        object_ids=[f"object-{sequence}"],
        locators=[],
        context_group_id=group,
    )


def test_semantic_index_embeds_one_representative_per_nested_ir_view(tmp_path) -> None:
    spans = [
        _span(1, "table_row", "table-row:1", "pollutant | 1.2 t"),
        _span(2, "table_cell", "table-row:1", "pollutant"),
        _span(3, "table_cell", "table-row:1", "1.2 t"),
        _span(4, "block", "block:1", "short paragraph"),
        _span(5, "sentence", "block:1", "short sentence"),
        _span(6, "block", "block:2", "长" * 900),
        _span(7, "sentence", "block:2", "长段落第一句"),
        _span(8, "sentence", "block:2", "长段落第二句"),
        _span(9, "figure_text", "figure:1", "figure caption"),
    ]
    provider = RecordingProvider(tmp_path / "model")
    cache = CachedEmbeddingIndex(provider)

    matrix = cache.build_or_load(
        spans,
        tmp_path / "embeddings.npy",
        tmp_path / "metadata.json",
    )

    assert provider.document_batches == [
        [
            spans[0].context_text,
            spans[3].context_text,
            spans[6].context_text,
            spans[7].context_text,
            spans[8].context_text,
        ]
    ]
    assert np.count_nonzero(np.linalg.norm(matrix, axis=1)) == 5
    assert cache.semantic_indexed_span_count == 5
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["index_policy"] == SEMANTIC_REPRESENTATIVE_INDEX_POLICY
    assert metadata["semantic_indexed_span_count"] == 5


def test_existing_dense_index_remains_a_valid_cache_hit(tmp_path) -> None:
    spans = [_span(1, "block", "block:1", "existing indexed paragraph")]
    provider = RecordingProvider(tmp_path / "model")
    matrix_path = tmp_path / "embeddings.npy"
    metadata_path = tmp_path / "metadata.json"
    expected = np.full((1, provider.dimensions), 2.0, dtype=np.float32)
    np.save(matrix_path, expected)
    metadata_path.write_text(
        json.dumps(
            {
                "digest": CachedEmbeddingIndex._digest(spans),
                "span_ids": [spans[0].span_id],
                "shape": [1, provider.dimensions],
                "index_policy": LEGACY_DENSE_INDEX_POLICY,
                "eligible_span_count": 1,
                "model_path": str(provider.model_path),
            }
        ),
        encoding="utf-8",
    )

    cache = CachedEmbeddingIndex(provider)
    actual = cache.build_or_load(spans, matrix_path, metadata_path)

    assert cache.cache_hit
    assert cache.index_policy == LEGACY_DENSE_INDEX_POLICY
    assert provider.document_batches == []
    np.testing.assert_array_equal(actual, expected)


def test_extraction_result_cleanup_does_not_delete_semantic_index_asset(tmp_path) -> None:
    index_root = tmp_path / "semantic-indexes"
    catalog = SemanticIndexAssetCatalog(index_root)
    inventory_id = "inventory-abc123"
    index_dir = index_root / inventory_id
    index_dir.mkdir(parents=True)
    (index_dir / "embeddings.npy").write_bytes(b"index")
    (index_dir / "metadata.json").write_text("{}", encoding="utf-8")
    catalog.record(
        inventory_id,
        {
            "document_id": "doc-1",
            "ir_run_id": "ir-1",
            "ir_revision": 1,
            "index_policy": SEMANTIC_REPRESENTATIVE_INDEX_POLICY,
        },
    )
    artifacts = ArtifactStore(tmp_path / "results")
    artifacts.write_json("tx-1", "results/summary.json", {"status": "completed"})

    artifacts.delete_job("tx-1")

    assert (index_dir / "embeddings.npy").is_file()
    assert catalog.list()[0]["inventory_id"] == inventory_id


def test_zero_placeholder_vectors_do_not_receive_semantic_rank() -> None:
    spans = [
        _span(1, "table_row", "table-row:1", "pollutant emission"),
        _span(2, "table_cell", "table-row:1", "pollutant emission"),
    ]
    inventory = EvidenceInventory(
        inventory_id="inventory-1",
        document_id="doc-1",
        ir_run_id="ir-1",
        ir_revision=1,
        spans=spans,
        candidates=[],
        page_images={},
        stats={},
    )
    matrix = np.asarray([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float32)
    query = MetricQuery(
        metric_id="metric-1",
        query_text="pollutant",
        lexical_text="pollutant",
        semantic_text="pollutant",
        lexical_terms=["pollutant"],
        data_class="quantitative",
        element_ids=[],
        intent=QueryIntent(),
    )

    hits = HybridRetriever(inventory, embedding_matrix=matrix).rank_all(
        query,
        query_vector=np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
    )
    by_span = {item.span_id: item for item in hits}

    assert by_span["span-1"].semantic_rank == 1
    assert by_span["span-2"].semantic_rank is None
    assert by_span["span-2"].semantic_score is None
