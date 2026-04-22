"""Unit tests for akgentic.tool.vector_store.protocol models and protocols."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    EmbeddingProvider,
    SearchHit,
    SearchResult,
    VectorStoreConfig,
    VectorStoreService,
)

# ---------------------------------------------------------------------------
# CollectionStatus enum tests (AC3)
# ---------------------------------------------------------------------------


class TestCollectionStatus:
    """Tests for CollectionStatus StrEnum."""

    def test_has_exactly_three_values(self) -> None:
        assert len(CollectionStatus) == 3

    def test_ready_value(self) -> None:
        assert CollectionStatus.READY == "ready"
        assert str(CollectionStatus.READY) == "ready"

    def test_indexing_value(self) -> None:
        assert CollectionStatus.INDEXING == "indexing"
        assert str(CollectionStatus.INDEXING) == "indexing"

    def test_error_value(self) -> None:
        assert CollectionStatus.ERROR == "error"
        assert str(CollectionStatus.ERROR) == "error"

    def test_values_list(self) -> None:
        values = {s.value for s in CollectionStatus}
        assert values == {"ready", "indexing", "error"}


# ---------------------------------------------------------------------------
# CollectionConfig tests (AC2, AC8)
# ---------------------------------------------------------------------------


class TestCollectionConfig:
    """Tests for CollectionConfig serialization and defaults."""

    def test_default_values(self) -> None:
        cfg = CollectionConfig()
        assert cfg.dimension == 1536
        assert cfg.backend == "inmemory"
        assert cfg.persistence == "actor_state"
        assert cfg.workspace_path is None

    def test_round_trip_serialization_defaults(self) -> None:
        cfg = CollectionConfig()
        data = cfg.model_dump()
        restored = CollectionConfig.model_validate(data)
        assert restored == cfg

    def test_non_default_values(self) -> None:
        cfg = CollectionConfig(
            dimension=768,
            backend="weaviate",
            persistence="workspace",
            workspace_path="/tmp/vectors",
        )
        assert cfg.dimension == 768
        assert cfg.backend == "weaviate"
        assert cfg.persistence == "workspace"
        assert cfg.workspace_path == "/tmp/vectors"

    def test_round_trip_serialization_non_defaults(self) -> None:
        cfg = CollectionConfig(
            dimension=768,
            backend="weaviate",
            persistence="workspace",
            workspace_path="/tmp/vectors",
        )
        data = cfg.model_dump()
        restored = CollectionConfig.model_validate(data)
        assert restored == cfg

    def test_dimension_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CollectionConfig(dimension=0)

    def test_dimension_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            CollectionConfig(dimension=-1)


# ---------------------------------------------------------------------------
# SearchHit tests (AC4, AC8)
# ---------------------------------------------------------------------------


class TestSearchHit:
    """Tests for SearchHit construction and serialization."""

    def test_construction(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="abc-123", text="hello world", score=0.95)
        assert hit.ref_type == "entity"
        assert hit.ref_id == "abc-123"
        assert hit.text == "hello world"
        assert hit.score == 0.95

    def test_round_trip_serialization(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="abc-123", text="hello world", score=0.95)
        data = hit.model_dump()
        restored = SearchHit.model_validate(data)
        assert restored == hit


# ---------------------------------------------------------------------------
# SearchResult tests (AC5, AC8)
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Tests for SearchResult construction and serialization."""

    def test_construction_with_hits(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="id-1", text="some text", score=0.9)
        result = SearchResult(
            hits=[hit], status=CollectionStatus.READY, indexing_pending=5
        )
        assert len(result.hits) == 1
        assert result.status == CollectionStatus.READY
        assert result.indexing_pending == 5

    def test_default_indexing_pending(self) -> None:
        result = SearchResult(hits=[], status=CollectionStatus.READY)
        assert result.indexing_pending == 0

    def test_round_trip_serialization(self) -> None:
        hit = SearchHit(ref_type="relation", ref_id="r-1", text="related", score=0.85)
        result = SearchResult(
            hits=[hit], status=CollectionStatus.INDEXING, indexing_pending=3
        )
        data = result.model_dump()
        restored = SearchResult.model_validate(data)
        assert restored == result

    def test_indexing_pending_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            SearchResult(hits=[], status=CollectionStatus.READY, indexing_pending=-1)

    def test_empty_hits_with_error_status(self) -> None:
        result = SearchResult(hits=[], status=CollectionStatus.ERROR)
        assert result.hits == []
        assert result.status == CollectionStatus.ERROR
        data = result.model_dump()
        restored = SearchResult.model_validate(data)
        assert restored == result


# ---------------------------------------------------------------------------
# VectorStoreConfig tests (AC7, AC8)
# ---------------------------------------------------------------------------


class TestVectorStoreConfig:
    """Tests for VectorStoreConfig defaults and serialization."""

    def test_default_values(self) -> None:
        cfg = VectorStoreConfig()
        assert cfg.embedding_model == "text-embedding-3-small"
        assert cfg.embedding_provider == "openai"
        assert cfg.weaviate_url is None
        assert cfg.weaviate_api_key is None

    def test_round_trip_serialization_defaults(self) -> None:
        cfg = VectorStoreConfig()
        data = cfg.model_dump()
        restored = VectorStoreConfig.model_validate(data)
        assert restored == cfg

    def test_all_optional_fields_populated(self) -> None:
        cfg = VectorStoreConfig(
            embedding_model="text-embedding-ada-002",
            embedding_provider="azure",
            weaviate_url="http://localhost:8080",
            weaviate_api_key="secret-key-123",
        )
        assert cfg.embedding_model == "text-embedding-ada-002"
        assert cfg.embedding_provider == "azure"
        assert cfg.weaviate_url == "http://localhost:8080"
        assert cfg.weaviate_api_key == "secret-key-123"

    def test_round_trip_serialization_all_fields(self) -> None:
        cfg = VectorStoreConfig(
            embedding_model="text-embedding-ada-002",
            embedding_provider="azure",
            weaviate_url="http://localhost:8080",
            weaviate_api_key="secret-key-123",
        )
        data = cfg.model_dump()
        restored = VectorStoreConfig.model_validate(data)
        assert restored == cfg


# ---------------------------------------------------------------------------
# EmbeddingProvider protocol tests (AC6, AC11)
# ---------------------------------------------------------------------------


class TestEmbeddingProvider:
    """Tests for EmbeddingProvider structural subtyping."""

    def test_structural_subtyping(self) -> None:
        """A class with a matching embed method satisfies EmbeddingProvider."""

        class _FakeEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[0.1, 0.2] for _ in texts]

        embedder: EmbeddingProvider = _FakeEmbedder()
        result = embedder.embed(["test"])
        assert result == [[0.1, 0.2]]


# ---------------------------------------------------------------------------
# VectorStoreService protocol tests (AC1, AC11)
# ---------------------------------------------------------------------------


class TestVectorStoreService:
    """Tests for VectorStoreService structural subtyping."""

    def test_structural_subtyping(self) -> None:
        """A class implementing all 4 methods satisfies VectorStoreService."""
        from akgentic.tool.vector import VectorEntry

        class _FakeStore:
            def create_collection(self, name: str, config: CollectionConfig) -> None:
                pass

            def add(self, collection: str, entries: list[VectorEntry]) -> None:
                pass

            def remove(self, collection: str, ref_ids: list[str]) -> None:
                pass

            def search(
                self, collection: str, query_vector: list[float], top_k: int
            ) -> SearchResult:
                return SearchResult(hits=[], status=CollectionStatus.READY)

        store: VectorStoreService = _FakeStore()
        store.create_collection("test", CollectionConfig())
        result = store.search("test", [0.1, 0.2], top_k=5)
        assert result.hits == []
        assert result.status == CollectionStatus.READY
