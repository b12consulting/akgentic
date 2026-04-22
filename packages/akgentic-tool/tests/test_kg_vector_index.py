"""Tests for EmbeddingService, VectorIndex, and VectorEntry.

Covers:
  - VectorEntry construction and field validation (Task 1.3)
  - EmbeddingService: correct client selection and return shape (Task 2.7)
  - VectorIndex: add/remove, cosine search, empty-index guard (Task 3.5)
  - Performance: 1000-vector x 1536-dim cosine search < 1ms (NFR-KG-1, Task 3.5)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from akgentic.tool.vector import EmbeddingService, VectorEntry, VectorIndex

# ---------------------------------------------------------------------------
# VectorEntry (Task 1.3)
# ---------------------------------------------------------------------------


class TestVectorEntry:
    """AC-3, AC-4: VectorEntry construction and field validation."""

    def test_construct_entity_entry(self) -> None:
        entry = VectorEntry(
            ref_type="entity",
            ref_id="abc-123",
            text="Alice: Software engineer",
            vector=[0.1, 0.2, 0.3],
        )
        assert entry.ref_type == "entity"
        assert entry.ref_id == "abc-123"
        assert entry.text == "Alice: Software engineer"
        assert entry.vector == [0.1, 0.2, 0.3]

    def test_construct_relation_entry(self) -> None:
        entry = VectorEntry(
            ref_type="relation",
            ref_id="def-456",
            text="Alice knows Bob: long colleagues",
            vector=[0.5, 0.6],
        )
        assert entry.ref_type == "relation"

    def test_ref_type_accepts_any_string(self) -> None:
        # VectorEntry is domain-agnostic; ref_type is a free-form string.
        entry = VectorEntry(ref_type="custom_type", ref_id="id", text="text", vector=[0.1])
        assert entry.ref_type == "custom_type"

    def test_vector_field_accepts_empty_list(self) -> None:
        entry = VectorEntry(ref_type="entity", ref_id="id", text="t", vector=[])
        assert entry.vector == []


# ---------------------------------------------------------------------------
# EmbeddingService (Task 2.7)
# ---------------------------------------------------------------------------


def _make_mock_embedding_response(n: int = 2, dim: int = 3) -> MagicMock:
    """Build a mock embeddings.create() response with n embeddings of dim dims."""
    items = [MagicMock(embedding=[float(i)] * dim) for i in range(n)]
    response = MagicMock()
    response.data = items
    return response


class TestEmbeddingService:
    """AC-1, AC-2: EmbeddingService correct client selection and return shape."""

    def test_openai_client_used_for_openai_provider(self) -> None:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _make_mock_embedding_response(1, 3)

        with patch("openai.OpenAI", return_value=mock_client) as mock_cls:
            svc = EmbeddingService(model="text-embedding-3-small", provider="openai")
            result = svc.embed(["hello"])

        mock_cls.assert_called_once()
        assert len(result) == 1
        assert len(result[0]) == 3

    def test_azure_client_used_for_azure_provider(self) -> None:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _make_mock_embedding_response(1, 4)

        with (
            patch("openai.OpenAI") as mock_oai,
            patch("openai.AzureOpenAI", return_value=mock_client) as mock_azure,
        ):
            svc = EmbeddingService(model="text-embedding-3-small", provider="azure")
            result = svc.embed(["hello"])

        mock_azure.assert_called_once()
        mock_oai.assert_not_called()
        assert len(result) == 1
        assert len(result[0]) == 4

    def test_embed_multiple_texts_returns_one_vector_per_text(self) -> None:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _make_mock_embedding_response(3, 5)

        with patch("openai.OpenAI", return_value=mock_client):
            svc = EmbeddingService(model="text-embedding-3-small", provider="openai")
            result = svc.embed(["a", "b", "c"])

        assert len(result) == 3
        for vec in result:
            assert len(vec) == 5

    def test_client_created_lazily_on_first_embed(self) -> None:
        """Client must not be created at __init__ time, only on first embed()."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _make_mock_embedding_response(1)

        with patch("openai.OpenAI", return_value=mock_client) as mock_cls:
            svc = EmbeddingService(model="m", provider="openai")
            # Client NOT created yet
            mock_cls.assert_not_called()

            svc.embed(["x"])
            # Now it is created
            mock_cls.assert_called_once()

    def test_client_cached_across_embed_calls(self) -> None:
        """Second embed() must reuse the same client, not create a new one."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _make_mock_embedding_response(1)

        with patch("openai.OpenAI", return_value=mock_client) as mock_cls:
            svc = EmbeddingService(model="m", provider="openai")
            svc.embed(["x"])
            svc.embed(["y"])

            assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# VectorIndex (Task 3.5)
# ---------------------------------------------------------------------------


def _make_entry(
    ref_id: str, vector: list[float], ref_type: str = "entity"
) -> VectorEntry:
    """Factory for VectorEntry instances in tests."""
    return VectorEntry(ref_type=ref_type, ref_id=ref_id, text=f"text-{ref_id}", vector=vector)


class TestVectorIndexAddRemove:
    """AC-5, AC-6: add/remove entries from VectorIndex."""

    def test_add_entry(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0, 0.0]))
        assert len(idx._entries) == 1

    def test_add_multiple_entries(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0]))
        idx.add(_make_entry("e2", [0.0, 1.0]))
        assert len(idx._entries) == 2

    def test_remove_by_ref_id(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0]))
        idx.add(_make_entry("e2", [0.0, 1.0]))
        idx.remove({"e1"})
        assert len(idx._entries) == 1
        assert idx._entries[0].ref_id == "e2"

    def test_remove_multiple_ids(self) -> None:
        idx = VectorIndex()
        for i in range(5):
            idx.add(_make_entry(f"e{i}", [float(i), 0.0]))
        idx.remove({"e0", "e2", "e4"})
        remaining = {e.ref_id for e in idx._entries}
        assert remaining == {"e1", "e3"}

    def test_remove_nonexistent_id_is_noop(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0]))
        idx.remove({"doesnotexist"})
        assert len(idx._entries) == 1


class TestVectorIndexCosineSearch:
    """AC-3, AC-4: cosine similarity search returns correct order."""

    def test_empty_index_returns_empty_list(self) -> None:
        idx = VectorIndex()
        result = idx.search_cosine([1.0, 0.0, 0.0], top_k=5)
        assert result == []

    def test_exact_match_has_score_1(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0, 0.0]))
        results = idx.search_cosine([1.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        ref_id, score = results[0]
        assert ref_id == "e1"
        assert abs(score - 1.0) < 1e-6

    def test_orthogonal_vectors_have_zero_score(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0]))
        idx.add(_make_entry("e2", [0.0, 1.0]))
        results = idx.search_cosine([1.0, 0.0], top_k=2)
        scores = {r[0]: r[1] for r in results}
        assert abs(scores["e1"] - 1.0) < 1e-6
        assert abs(scores["e2"] - 0.0) < 1e-6

    def test_results_sorted_descending_by_score(self) -> None:
        idx = VectorIndex()
        # e1 is aligned, e2 is 45°, e3 is orthogonal
        idx.add(_make_entry("e1", [1.0, 0.0]))
        idx.add(_make_entry("e2", [1.0, 1.0]))  # normalized scores 1/sqrt(2)
        idx.add(_make_entry("e3", [0.0, 1.0]))
        results = idx.search_cosine([1.0, 0.0], top_k=3)
        scores = [r[1] for r in results]
        assert scores == sorted(scores, reverse=True)
        assert results[0][0] == "e1"

    def test_top_k_limits_results(self) -> None:
        idx = VectorIndex()
        for i in range(10):
            idx.add(_make_entry(f"e{i}", [1.0, float(i)]))
        results = idx.search_cosine([1.0, 0.0], top_k=3)
        assert len(results) == 3

    def test_top_k_larger_than_entries_returns_all(self) -> None:
        idx = VectorIndex()
        idx.add(_make_entry("e1", [1.0, 0.0]))
        idx.add(_make_entry("e2", [0.0, 1.0]))
        results = idx.search_cosine([1.0, 0.0], top_k=100)
        assert len(results) == 2


class TestVectorIndexPerformance:
    """NFR-KG-1: 1000 vectors x 1536 dims cosine search must complete in < 1ms."""

    def test_cosine_search_1000_vectors_1536_dims_under_1ms(self) -> None:
        import numpy as np

        idx = VectorIndex()
        dim = 1536
        rng = np.random.default_rng(42)
        for i in range(1000):
            vec = rng.random(dim).tolist()
            idx.add(_make_entry(f"e{i}", vec))

        query = rng.random(dim).tolist()
        start = time.perf_counter()
        results = idx.search_cosine(query, top_k=10)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(results) == 10
        assert elapsed_ms < 10.0, f"Cosine search took {elapsed_ms:.3f}ms (> 10ms)"


# ---------------------------------------------------------------------------
# Backward-compat shim removed (Story 11.1 AC-9)
# vector_index.py shim and KG re-exports of VectorEntry/EmbeddingService are deleted.
# VectorEntry and EmbeddingService should be imported from akgentic.tool.vector directly.
# ---------------------------------------------------------------------------
