"""Unit tests for InMemoryBackend."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from akgentic.tool.vector import VectorEntry
from akgentic.tool.vector_store.inmemory import InMemoryBackend
from akgentic.tool.vector_store.protocol import CollectionConfig, CollectionStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> InMemoryBackend:
    """Return a fresh InMemoryBackend instance."""
    return InMemoryBackend()


@pytest.fixture()
def config() -> CollectionConfig:
    """Return a default CollectionConfig."""
    return CollectionConfig()


def _make_entry(ref_id: str, vector: list[float], text: str = "test") -> VectorEntry:
    """Helper to create a VectorEntry."""
    return VectorEntry(ref_type="test", ref_id=ref_id, text=text, vector=vector)


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


class TestCreateCollection:
    """Tests for create_collection."""

    def test_creates_new_collection(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC3: create_collection creates a new collection."""
        backend.create_collection("col1", config)
        # Verify collection exists by searching (should return empty results)
        result = backend.search("col1", [0.0, 0.0, 0.0], top_k=5)
        assert result.hits == []
        assert result.status == CollectionStatus.READY

    def test_idempotent(self, backend: InMemoryBackend, config: CollectionConfig) -> None:
        """AC3: second call with same name is no-op, does not reset data."""
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [1.0, 0.0, 0.0])])

        # Second create_collection should be a no-op
        backend.create_collection("col1", config)

        result = backend.search("col1", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e1"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


class TestAdd:
    """Tests for add."""

    def test_inserts_entries_searchable(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC4: add inserts entries and they are searchable."""
        backend.create_collection("col1", config)
        entries = [
            _make_entry("e1", [1.0, 0.0, 0.0], text="hello"),
            _make_entry("e2", [0.0, 1.0, 0.0], text="world"),
        ]
        backend.add("col1", entries)
        result = backend.search("col1", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 2
        assert result.hits[0].ref_id == "e1"

    def test_nonexistent_collection_raises(self, backend: InMemoryBackend) -> None:
        """AC12: add to non-existent collection raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            backend.add("missing", [_make_entry("e1", [1.0])])


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    """Tests for remove."""

    def test_removes_entries(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC5: remove deletes entries by ref_ids."""
        backend.create_collection("col1", config)
        backend.add("col1", [
            _make_entry("e1", [1.0, 0.0]),
            _make_entry("e2", [0.0, 1.0]),
        ])
        backend.remove("col1", ["e1"])
        result = backend.search("col1", [1.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e2"

    def test_nonexistent_collection_raises(self, backend: InMemoryBackend) -> None:
        """AC12: remove from non-existent collection raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            backend.remove("missing", ["e1"])


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """Tests for search."""

    def test_returns_search_result_with_correct_fields(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC6: search returns SearchResult with correct SearchHit fields."""
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [1.0, 0.0], text="hello")])
        result = backend.search("col1", [1.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.ref_type == "test"
        assert hit.ref_id == "e1"
        assert hit.text == "hello"
        assert isinstance(hit.score, float)
        assert result.status == CollectionStatus.READY
        assert result.indexing_pending == 0

    def test_ranked_by_cosine_similarity(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC6: results ranked by cosine similarity (highest first)."""
        backend.create_collection("col1", config)
        backend.add("col1", [
            _make_entry("far", [0.0, 1.0, 0.0]),
            _make_entry("close", [1.0, 0.0, 0.0]),
            _make_entry("mid", [0.7, 0.7, 0.0]),
        ])
        result = backend.search("col1", [1.0, 0.0, 0.0], top_k=3)
        scores = [h.score for h in result.hits]
        assert scores == sorted(scores, reverse=True)
        assert result.hits[0].ref_id == "close"

    def test_empty_collection_returns_empty_hits(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """search on empty collection returns empty hits with READY status."""
        backend.create_collection("col1", config)
        result = backend.search("col1", [1.0, 0.0], top_k=5)
        assert result.hits == []
        assert result.status == CollectionStatus.READY

    def test_nonexistent_collection_raises(self, backend: InMemoryBackend) -> None:
        """AC12: search on non-existent collection raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            backend.search("missing", [1.0], top_k=5)

    def test_respects_top_k(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """search respects top_k limit."""
        backend.create_collection("col1", config)
        backend.add("col1", [
            _make_entry("e1", [1.0, 0.0]),
            _make_entry("e2", [0.9, 0.1]),
            _make_entry("e3", [0.8, 0.2]),
        ])
        result = backend.search("col1", [1.0, 0.0], top_k=2)
        assert len(result.hits) == 2


# ---------------------------------------------------------------------------
# actor_state persistence
# ---------------------------------------------------------------------------


class TestActorStatePersistence:
    """Tests for get_state / restore_state round-trip."""

    def test_round_trip(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """AC8: get_state / restore_state round-trip preserves data."""
        backend.create_collection("col1", config)
        backend.add("col1", [
            _make_entry("e1", [1.0, 0.0, 0.0], text="alpha"),
            _make_entry("e2", [0.0, 1.0, 0.0], text="beta"),
        ])

        state = backend.get_state()

        restored = InMemoryBackend()
        restored.restore_state(state)

        result = restored.search("col1", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 2
        assert result.hits[0].ref_id == "e1"
        assert result.hits[0].text == "alpha"

    def test_state_includes_config(
        self, backend: InMemoryBackend
    ) -> None:
        """get_state includes collection config."""
        cfg = CollectionConfig(dimension=768, persistence="workspace")
        backend.create_collection("col1", cfg)

        state = backend.get_state()
        assert state["collections"]["col1"]["config"]["dimension"] == 768
        assert state["collections"]["col1"]["config"]["persistence"] == "workspace"


# ---------------------------------------------------------------------------
# workspace persistence
# ---------------------------------------------------------------------------


class TestWorkspacePersistence:
    """Tests for save_collection / load_collection."""

    def test_round_trip(
        self, backend: InMemoryBackend, config: CollectionConfig, tmp_path: Path
    ) -> None:
        """AC9: save_collection / load_collection round-trip."""
        backend.create_collection("col1", config)
        backend.add("col1", [
            _make_entry("e1", [1.0, 0.0, 0.0], text="saved"),
            _make_entry("e2", [0.0, 1.0, 0.0], text="also saved"),
        ])
        backend.save_collection("col1", str(tmp_path))

        restored = InMemoryBackend()
        restored.load_collection("col1", config, str(tmp_path))

        result = restored.search("col1", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 2
        assert result.hits[0].ref_id == "e1"
        assert result.hits[0].text == "saved"

    def test_creates_vector_store_directory(
        self, backend: InMemoryBackend, config: CollectionConfig, tmp_path: Path
    ) -> None:
        """AC9: save_collection creates .vector_store/ directory."""
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [1.0, 0.0])])
        backend.save_collection("col1", str(tmp_path))

        assert (tmp_path / ".vector_store").is_dir()
        assert (tmp_path / ".vector_store" / "col1.npz").exists()
        assert (tmp_path / ".vector_store" / "col1.json").exists()

    def test_missing_file_starts_empty(
        self, config: CollectionConfig, tmp_path: Path
    ) -> None:
        """AC9: loading from missing file starts empty collection."""
        backend = InMemoryBackend()
        backend.load_collection("col1", config, str(tmp_path))

        result = backend.search("col1", [1.0, 0.0], top_k=5)
        assert result.hits == []
        assert result.status == CollectionStatus.READY

    def test_save_nonexistent_collection_raises(
        self, backend: InMemoryBackend, tmp_path: Path
    ) -> None:
        """save_collection on non-existent collection raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            backend.save_collection("missing", str(tmp_path))


# ---------------------------------------------------------------------------
# Multi-collection independence
# ---------------------------------------------------------------------------


class TestMultipleCollections:
    """Tests for collection independence."""

    def test_collections_are_independent(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """Adding to one collection does not affect another."""
        backend.create_collection("col1", config)
        backend.create_collection("col2", config)

        backend.add("col1", [_make_entry("e1", [1.0, 0.0])])
        backend.add("col2", [_make_entry("e2", [0.0, 1.0])])

        result1 = backend.search("col1", [1.0, 0.0], top_k=5)
        result2 = backend.search("col2", [0.0, 1.0], top_k=5)

        assert len(result1.hits) == 1
        assert result1.hits[0].ref_id == "e1"
        assert len(result2.hits) == 1
        assert result2.hits[0].ref_id == "e2"


# ---------------------------------------------------------------------------
# Cosine similarity correctness
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify InMemoryBackend satisfies VectorStoreService structurally."""

    def test_satisfies_vector_store_service_protocol(self) -> None:
        """AC1: InMemoryBackend structurally implements VectorStoreService."""
        from akgentic.tool.vector_store.protocol import VectorStoreService

        backend = InMemoryBackend()
        # Structural subtyping check: assign to protocol-typed variable
        svc: VectorStoreService = backend
        assert svc is backend


class TestRestoreStateEdgeCases:
    """Edge case tests for restore_state."""

    def test_restore_empty_state(self, backend: InMemoryBackend) -> None:
        """restore_state with empty collections dict clears existing data."""
        config = CollectionConfig()
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [1.0, 0.0])])

        backend.restore_state({"collections": {}})

        with pytest.raises(ValueError, match="does not exist"):
            backend.search("col1", [1.0, 0.0], top_k=5)

    def test_restore_state_missing_collections_key(self) -> None:
        """restore_state with empty dict treats it as no collections."""
        backend = InMemoryBackend()
        backend.restore_state({})
        # Backend should be empty — no collections at all
        with pytest.raises(ValueError, match="does not exist"):
            backend.search("anything", [1.0], top_k=1)


class TestCosineCorrectness:
    """Verify cosine similarity scores are mathematically correct."""

    def test_identical_vectors_score_one(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """Identical vectors should have cosine similarity ~1.0."""
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [1.0, 0.0, 0.0])])
        result = backend.search("col1", [1.0, 0.0, 0.0], top_k=1)
        assert math.isclose(result.hits[0].score, 1.0, abs_tol=1e-9)

    def test_orthogonal_vectors_score_zero(
        self, backend: InMemoryBackend, config: CollectionConfig
    ) -> None:
        """Orthogonal vectors should have cosine similarity ~0.0."""
        backend.create_collection("col1", config)
        backend.add("col1", [_make_entry("e1", [0.0, 1.0, 0.0])])
        result = backend.search("col1", [1.0, 0.0, 0.0], top_k=1)
        assert math.isclose(result.hits[0].score, 0.0, abs_tol=1e-9)
