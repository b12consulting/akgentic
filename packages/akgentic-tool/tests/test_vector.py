"""Unit tests for akgentic.tool vector module (Story SR-1.3).

Covers:
  - Public API exports: VectorEntry, EmbeddingService, VectorIndex in __all__ (AC #1, #2)
  - VectorEntry model validation (AC #3)
  - EmbeddingService with mocked OpenAI / Azure clients (AC #4)
  - VectorIndex add / remove / len / search_cosine on small corpus (AC #5)
  - _check_vector_search_dependencies with simulated missing imports (AC #6)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from akgentic.tool.vector import (
    EmbeddingService,
    VectorEntry,
    VectorIndex,
    _check_vector_search_dependencies,
)

# ---------------------------------------------------------------------------
# AC #1, #2 — Public API exports
# ---------------------------------------------------------------------------


class TestPublicApiExports:
    """Verify top-level akgentic.tool exports the vector types."""

    def test_vector_entry_importable_from_top_level(self) -> None:
        import akgentic.tool as _tool  # noqa: PLC0415

        assert _tool.VectorEntry is VectorEntry

    def test_embedding_service_importable_from_top_level(self) -> None:
        import akgentic.tool as _tool  # noqa: PLC0415

        assert _tool.EmbeddingService is EmbeddingService

    def test_vector_index_importable_from_top_level(self) -> None:
        import akgentic.tool as _tool  # noqa: PLC0415

        assert _tool.VectorIndex is VectorIndex

    def test_all_three_in_dunder_all(self) -> None:
        import akgentic.tool as _tool  # noqa: PLC0415

        for name in ("VectorEntry", "EmbeddingService", "VectorIndex"):
            assert name in _tool.__all__, f"{name} missing from akgentic.tool.__all__"


# ---------------------------------------------------------------------------
# AC #3 — VectorEntry model validation
# ---------------------------------------------------------------------------


class TestVectorEntry:
    """AC #3: VectorEntry field validation."""

    def test_valid_entity_ref_type(self) -> None:
        entry = VectorEntry(ref_type="entity", ref_id="abc", text="foo", vector=[0.1, 0.2])
        assert entry.ref_type == "entity"

    def test_valid_task_ref_type(self) -> None:
        entry = VectorEntry(ref_type="task", ref_id="1", text="bar", vector=[0.5])
        assert entry.ref_type == "task"

    def test_valid_relation_ref_type(self) -> None:
        entry = VectorEntry(ref_type="relation", ref_id="r1", text="baz", vector=[0.3, 0.4])
        assert entry.ref_type == "relation"

    def test_ref_id_field_stored(self) -> None:
        entry = VectorEntry(ref_type="entity", ref_id="my-id", text="t", vector=[1.0])
        assert entry.ref_id == "my-id"

    def test_text_field_stored(self) -> None:
        entry = VectorEntry(ref_type="entity", ref_id="x", text="hello world", vector=[0.0])
        assert entry.text == "hello world"

    def test_vector_field_stored(self) -> None:
        vec = [0.1, 0.2, 0.3]
        entry = VectorEntry(ref_type="entity", ref_id="x", text="t", vector=vec)
        assert entry.vector == vec

    def test_missing_ref_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            VectorEntry(ref_type="entity", text="foo", vector=[])  # type: ignore[call-arg]

    def test_missing_text_raises(self) -> None:
        with pytest.raises(ValidationError):
            VectorEntry(ref_type="entity", ref_id="x", vector=[0.1])  # type: ignore[call-arg]

    def test_missing_vector_raises(self) -> None:
        with pytest.raises(ValidationError):
            VectorEntry(ref_type="entity", ref_id="x", text="t")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# AC #4 — EmbeddingService with mocked OpenAI client
# ---------------------------------------------------------------------------


class TestEmbeddingService:
    """AC #4: EmbeddingService with mocked providers."""

    def test_openai_provider_returns_float_vectors(self) -> None:
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
        with patch("openai.OpenAI") as mock_client_cls:
            mock_client_cls.return_value.embeddings.create.return_value = mock_response
            svc = EmbeddingService(model="text-embedding-3-small", provider="openai")
            result = svc.embed(["hello world"])
        assert result == [[0.1, 0.2, 0.3]]

    def test_openai_provider_uses_openai_class(self) -> None:
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.9])]
        with patch("openai.OpenAI") as mock_client_cls, patch("openai.AzureOpenAI") as mock_azure:
            mock_client_cls.return_value.embeddings.create.return_value = mock_response
            svc = EmbeddingService(model="text-embedding-3-small", provider="openai")
            svc.embed(["test"])
            mock_client_cls.assert_called_once()
            mock_azure.assert_not_called()

    def test_openai_provider_calls_with_model_name(self) -> None:
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1])]
        )
        with patch("openai.OpenAI", return_value=mock_client):
            svc = EmbeddingService(model="my-custom-model", provider="openai")
            svc.embed(["test"])
        mock_client.embeddings.create.assert_called_once_with(
            input=["test"], model="my-custom-model"
        )

    def test_azure_provider_uses_azure_openai_class(self) -> None:
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.9])]
        with patch("openai.AzureOpenAI") as mock_azure_cls, patch("openai.OpenAI") as mock_oai:
            mock_azure_cls.return_value.embeddings.create.return_value = mock_response
            svc = EmbeddingService(model="text-embedding-3-small", provider="azure")
            result = svc.embed(["test"])
            mock_azure_cls.assert_called_once()
            mock_oai.assert_not_called()
        assert result == [[0.9]]

    def test_embed_returns_list_of_float_vectors(self) -> None:
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1, 0.2]),
            MagicMock(embedding=[0.3, 0.4]),
        ]
        with patch("openai.OpenAI") as mock_cls:
            mock_cls.return_value.embeddings.create.return_value = mock_response
            svc = EmbeddingService(model="m", provider="openai")
            result = svc.embed(["a", "b"])
        assert len(result) == 2
        assert all(isinstance(v, list) for v in result)
        assert all(isinstance(x, float) for v in result for x in v)


# ---------------------------------------------------------------------------
# AC #5 — VectorIndex add / remove / len / search_cosine
# ---------------------------------------------------------------------------


class TestVectorIndex:
    """AC #5: VectorIndex add/remove/len/search_cosine on small corpus."""

    def test_add_increases_len(self) -> None:
        index = VectorIndex()
        entry = VectorEntry(ref_type="task", ref_id="1", text="foo", vector=[1.0, 0.0])
        index.add(entry)
        assert len(index) == 1

    def test_add_multiple_increases_len(self) -> None:
        index = VectorIndex()
        for i in range(3):
            index.add(VectorEntry(ref_type="task", ref_id=str(i), text="t", vector=[float(i), 0.0]))
        assert len(index) == 3

    def test_remove_decreases_len(self) -> None:
        index = VectorIndex()
        entry = VectorEntry(ref_type="task", ref_id="1", text="foo", vector=[1.0, 0.0])
        index.add(entry)
        index.remove({"1"})
        assert len(index) == 0

    def test_remove_specific_entry(self) -> None:
        index = VectorIndex()
        index.add(VectorEntry(ref_type="task", ref_id="1", text="a", vector=[1.0, 0.0]))
        index.add(VectorEntry(ref_type="task", ref_id="2", text="b", vector=[0.0, 1.0]))
        index.remove({"1"})
        assert len(index) == 1

    def test_search_cosine_returns_correct_entry(self) -> None:
        index = VectorIndex()
        index.add(VectorEntry(ref_type="task", ref_id="1", text="auth", vector=[1.0, 0.0]))
        index.add(VectorEntry(ref_type="task", ref_id="2", text="deploy", vector=[0.0, 1.0]))
        results = index.search_cosine([1.0, 0.0], top_k=1)
        assert results[0][0] == "1"

    def test_search_cosine_empty_index_returns_empty_list(self) -> None:
        index = VectorIndex()
        assert index.search_cosine([1.0, 0.0], top_k=1) == []

    def test_search_cosine_top_k_limits_results(self) -> None:
        index = VectorIndex()
        for i in range(5):
            index.add(VectorEntry(ref_type="task", ref_id=str(i), text="t", vector=[float(i), 1.0]))
        results = index.search_cosine([1.0, 0.0], top_k=2)
        assert len(results) == 2

    def test_remove_nonexistent_id_is_noop(self) -> None:
        index = VectorIndex()
        index.add(VectorEntry(ref_type="task", ref_id="1", text="foo", vector=[1.0, 0.0]))
        index.remove({"nonexistent"})
        assert len(index) == 1

    def test_remove_on_empty_index_is_noop(self) -> None:
        index = VectorIndex()
        index.remove({"any-id"})  # Must not raise
        assert len(index) == 0

    def test_search_cosine_returns_ref_id_and_score_tuples(self) -> None:
        index = VectorIndex()
        index.add(VectorEntry(ref_type="task", ref_id="x", text="t", vector=[1.0]))
        results = index.search_cosine([1.0], top_k=1)
        assert len(results) == 1
        ref_id, score = results[0]
        assert isinstance(ref_id, str)
        assert isinstance(score, float)


# ---------------------------------------------------------------------------
# AC #6 — _check_vector_search_dependencies with missing import mocks
# ---------------------------------------------------------------------------


class TestCheckVectorSearchDependencies:
    """AC #6: dependency guard raises with correct message."""

    def test_raises_when_numpy_missing(self) -> None:
        with patch.dict(sys.modules, {"numpy": None}):
            with pytest.raises(ImportError, match="numpy"):
                _check_vector_search_dependencies()

    def test_raises_numpy_error_mentions_pip_install(self) -> None:
        with patch.dict(sys.modules, {"numpy": None}):
            with pytest.raises(ImportError, match="pip install akgentic-tool\\[vector_search\\]"):
                _check_vector_search_dependencies()

    def test_raises_when_openai_missing(self) -> None:
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ImportError, match="openai"):
                _check_vector_search_dependencies()

    def test_raises_openai_error_mentions_pip_install(self) -> None:
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ImportError, match="pip install akgentic-tool\\[vector_search\\]"):
                _check_vector_search_dependencies()

    def test_passes_when_both_available(self) -> None:
        # Should not raise when numpy and openai are installed (dev environment)
        _check_vector_search_dependencies()
