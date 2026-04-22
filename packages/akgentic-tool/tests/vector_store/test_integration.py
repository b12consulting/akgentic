"""Integration tests for VectorStoreActor — end-to-end lifecycle validation.

Covers: full lifecycle (create/add/search/remove), INDEXING-to-READY transition,
multiple collection independence, embed delegation, idempotent create_collection,
remove verification, and workspace npz save/load round-trip.

Pattern: Direct instantiation of VectorStoreActor (no Pykka actor system),
same approach as test_actor.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from akgentic.tool.vector import VectorEntry
from akgentic.tool.vector_store.actor import (
    VS_ACTOR_NAME,
    VS_ACTOR_ROLE,
    VectorStoreActor,
)
from akgentic.tool.vector_store.embedding_actor import EmbeddingResult
from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    VectorStoreConfig,
)

# Resolve forward reference for VectorEntry in EmbeddingResult
EmbeddingResult.model_rebuild()

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_actor() -> VectorStoreActor:
    """Instantiate a VectorStoreActor directly — no Pykka actor system."""
    actor = VectorStoreActor()
    actor.config = VectorStoreConfig(name=VS_ACTOR_NAME, role=VS_ACTOR_ROLE)
    actor.on_start()
    actor.createActor = MagicMock()  # type: ignore[assignment]
    actor.proxy_tell = MagicMock()  # type: ignore[assignment]
    return actor


@pytest.fixture()
def actor() -> VectorStoreActor:
    """Provide a fresh VectorStoreActor for each test."""
    return _make_actor()


def _entry(
    ref_id: str,
    ref_type: str = "entity",
    text: str = "sample",
    vector: list[float] | None = None,
) -> VectorEntry:
    """Create a VectorEntry with an optional pre-populated vector."""
    return VectorEntry(
        ref_type=ref_type,
        ref_id=ref_id,
        text=text,
        vector=vector or [],
    )


# ---------------------------------------------------------------------------
# AC3: Integration Test — Full Lifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """AC3: create -> add pre-embedded -> search -> remove -> search."""

    def test_full_lifecycle_create_add_search_remove(
        self, actor: VectorStoreActor
    ) -> None:
        """End-to-end: create collection, add, search, remove, verify."""
        config = CollectionConfig(dimension=3)
        actor.create_collection("test_col", config)
        assert actor.state.collection_statuses["test_col"] == CollectionStatus.READY

        # Add pre-embedded entries
        entries = [
            _entry("e1", text="hello", vector=[1.0, 0.0, 0.0]),
            _entry("e2", text="world", vector=[0.0, 1.0, 0.0]),
            _entry("e3", text="foo", vector=[0.0, 0.0, 1.0]),
        ]
        actor.add("test_col", entries)

        # Search — query close to e1
        result = actor.search("test_col", [0.9, 0.1, 0.0], top_k=3)
        assert result.status == CollectionStatus.READY
        assert len(result.hits) == 3
        assert result.hits[0].ref_id == "e1"
        assert result.hits[0].ref_type == "entity"
        assert result.hits[0].text == "hello"
        assert result.hits[0].score > 0.0

        # Remove e1
        actor.remove("test_col", ["e1"])

        # Search again — e1 should be gone
        result2 = actor.search("test_col", [0.9, 0.1, 0.0], top_k=3)
        ref_ids = [h.ref_id for h in result2.hits]
        assert "e1" not in ref_ids
        assert len(result2.hits) == 2


# ---------------------------------------------------------------------------
# AC4: Integration Test — INDEXING to READY
# ---------------------------------------------------------------------------


class TestIndexingToReady:
    """AC4: add without vectors -> INDEXING -> deliver EmbeddingResult -> READY."""

    def test_indexing_to_ready_lifecycle(self, actor: VectorStoreActor) -> None:
        """Async lifecycle: add needs-embedding -> INDEXING -> result -> READY."""
        config = CollectionConfig(dimension=3)
        actor.create_collection("async_col", config)

        # Add entries without vectors (triggers embedding path)
        entries = [
            _entry("e1", text="hello"),
            _entry("e2", text="world"),
        ]
        actor.add("async_col", entries)

        # Verify INDEXING status
        assert (
            actor.state.collection_statuses["async_col"] == CollectionStatus.INDEXING
        )
        assert actor.state.indexing_pending["async_col"] == 2

        # Deliver EmbeddingResult manually
        embedded_entries = [
            VectorEntry(
                ref_type="entity", ref_id="e1", text="hello", vector=[1.0, 0.0, 0.0]
            ),
            VectorEntry(
                ref_type="entity", ref_id="e2", text="world", vector=[0.0, 1.0, 0.0]
            ),
        ]
        result_msg = EmbeddingResult(
            collection="async_col", entries=embedded_entries, request_id="req-1"
        )
        actor.receiveMsg_EmbeddingResult(result_msg)

        # Verify READY status
        assert actor.state.collection_statuses["async_col"] == CollectionStatus.READY

        # Search returns the newly-embedded entries
        search_result = actor.search("async_col", [0.9, 0.1, 0.0], top_k=2)
        assert len(search_result.hits) == 2
        assert search_result.hits[0].ref_id == "e1"


# ---------------------------------------------------------------------------
# AC5: Integration Test — Multiple Collections
# ---------------------------------------------------------------------------


class TestMultipleCollections:
    """AC5: Multiple collections with different configs are independent."""

    def test_multiple_collections_independence(
        self, actor: VectorStoreActor
    ) -> None:
        """Two collections with different dimensions stay independent."""
        config_kg = CollectionConfig(dimension=3)
        config_plan = CollectionConfig(dimension=4)

        actor.create_collection("kg", config_kg)
        actor.create_collection("planning", config_plan)

        # Add entries to each
        actor.add("kg", [_entry("kg1", text="knowledge", vector=[1.0, 0.0, 0.0])])
        actor.add(
            "planning",
            [_entry("p1", text="plan", vector=[1.0, 0.0, 0.0, 0.0])],
        )

        # Search kg — should only find kg entries
        kg_result = actor.search("kg", [1.0, 0.0, 0.0], top_k=5)
        assert len(kg_result.hits) == 1
        assert kg_result.hits[0].ref_id == "kg1"

        # Search planning — should only find planning entries
        plan_result = actor.search("planning", [1.0, 0.0, 0.0, 0.0], top_k=5)
        assert len(plan_result.hits) == 1
        assert plan_result.hits[0].ref_id == "p1"


# ---------------------------------------------------------------------------
# AC6: Integration Test — embed()
# ---------------------------------------------------------------------------


class TestEmbedReturnsVectors:
    """AC6: embed() delegates to EmbeddingService and returns vectors."""

    def test_embed_returns_vectors(self, actor: VectorStoreActor) -> None:
        """Mock EmbeddingService.embed -> verify correct dimension vectors."""
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1, 0.2, 0.3]]
        actor._embedding_svc = mock_svc

        result = actor.embed(["hello"])

        mock_svc.embed.assert_called_once_with(["hello"])
        assert len(result) == 1
        assert len(result[0]) == 3
        assert result[0] == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# AC7: Integration Test — Idempotent create_collection
# ---------------------------------------------------------------------------


class TestIdempotentCreateCollection:
    """AC7: Second create_collection is a no-op (data preserved)."""

    def test_idempotent_create_collection(self, actor: VectorStoreActor) -> None:
        """create_collection twice -> entries from first call preserved."""
        config = CollectionConfig(dimension=3)
        actor.create_collection("idem_col", config)

        # Add entries
        actor.add(
            "idem_col", [_entry("e1", text="hello", vector=[1.0, 0.0, 0.0])]
        )

        # Call create_collection again with same name
        actor.create_collection("idem_col", config)

        # Entries should still be present
        result = actor.search("idem_col", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e1"


# ---------------------------------------------------------------------------
# AC8: Integration Test — remove
# ---------------------------------------------------------------------------


class TestRemoveEntries:
    """AC8: Add entries, remove subset, verify only remaining returned."""

    def test_remove_entries(self, actor: VectorStoreActor) -> None:
        """Remove subset of entries and verify search results."""
        config = CollectionConfig(dimension=3)
        actor.create_collection("rm_col", config)

        entries = [
            _entry("e1", text="alpha", vector=[1.0, 0.0, 0.0]),
            _entry("e2", text="beta", vector=[0.0, 1.0, 0.0]),
            _entry("e3", text="gamma", vector=[0.0, 0.0, 1.0]),
        ]
        actor.add("rm_col", entries)

        # Remove e1 and e3
        actor.remove("rm_col", ["e1", "e3"])

        # Search — only e2 should remain
        result = actor.search("rm_col", [0.0, 1.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e2"
        assert result.hits[0].text == "beta"


# ---------------------------------------------------------------------------
# AC9: Integration Test — Workspace npz Save/Load
# ---------------------------------------------------------------------------


class TestWorkspacePersistenceSaveLoad:
    """AC9: Workspace persistence round-trip via npz/json on disk."""

    def test_workspace_persistence_save_load(
        self, tmp_path: Path
    ) -> None:
        """Create workspace collection, add entries, verify disk files, reload."""
        actor = _make_actor()
        config = CollectionConfig(
            dimension=3,
            persistence="workspace",
            workspace_path=str(tmp_path),
        )
        actor.create_collection("ws_col", config)

        # Add pre-embedded entries
        entries = [
            _entry("e1", text="hello", vector=[1.0, 0.0, 0.0]),
            _entry("e2", text="world", vector=[0.0, 1.0, 0.0]),
        ]
        actor.add("ws_col", entries)

        # Verify files on disk
        vs_dir = tmp_path / ".vector_store"
        assert (vs_dir / "ws_col.npz").exists()
        assert (vs_dir / "ws_col.json").exists()

        # Search original actor to confirm data
        result1 = actor.search("ws_col", [1.0, 0.0, 0.0], top_k=5)
        assert len(result1.hits) == 2
        assert result1.hits[0].ref_id == "e1"

        # Create a new actor and load from disk
        actor2 = _make_actor()
        config2 = CollectionConfig(
            dimension=3,
            persistence="workspace",
            workspace_path=str(tmp_path),
        )
        actor2.create_collection("ws_col", config2)

        # Search restored actor — all entries should be present
        result2 = actor2.search("ws_col", [1.0, 0.0, 0.0], top_k=5)
        assert len(result2.hits) == 2
        assert result2.hits[0].ref_id == "e1"
        assert result2.hits[0].text == "hello"
        assert result2.hits[0].score > 0.0

        # Verify scores match
        assert abs(result1.hits[0].score - result2.hits[0].score) < 1e-6

    def test_workspace_save_triggered_on_remove(
        self, tmp_path: Path
    ) -> None:
        """Remove triggers save — verify updated files on disk."""
        actor = _make_actor()
        config = CollectionConfig(
            dimension=3,
            persistence="workspace",
            workspace_path=str(tmp_path),
        )
        actor.create_collection("ws_rm", config)

        entries = [
            _entry("e1", text="hello", vector=[1.0, 0.0, 0.0]),
            _entry("e2", text="world", vector=[0.0, 1.0, 0.0]),
        ]
        actor.add("ws_rm", entries)

        # Remove one entry
        actor.remove("ws_rm", ["e1"])

        # Load from disk in new actor — only e2 should be present
        actor2 = _make_actor()
        config2 = CollectionConfig(
            dimension=3,
            persistence="workspace",
            workspace_path=str(tmp_path),
        )
        actor2.create_collection("ws_rm", config2)

        result = actor2.search("ws_rm", [0.0, 1.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e2"

    def test_workspace_save_on_embedding_result(
        self, tmp_path: Path
    ) -> None:
        """EmbeddingResult delivery triggers workspace save."""
        actor = _make_actor()
        config = CollectionConfig(
            dimension=3,
            persistence="workspace",
            workspace_path=str(tmp_path),
        )
        actor.create_collection("ws_embed", config)

        # Simulate async add (needs embedding)
        entries = [_entry("e1", text="hello")]
        actor.add("ws_embed", entries)

        # Deliver embedding result
        embedded = [
            VectorEntry(
                ref_type="entity", ref_id="e1", text="hello", vector=[1.0, 0.0, 0.0]
            ),
        ]
        result_msg = EmbeddingResult(
            collection="ws_embed", entries=embedded, request_id="req-1"
        )
        actor.receiveMsg_EmbeddingResult(result_msg)

        # Verify files exist
        vs_dir = tmp_path / ".vector_store"
        assert (vs_dir / "ws_embed.npz").exists()

        # Load in new actor
        actor2 = _make_actor()
        actor2.create_collection("ws_embed", config)
        result = actor2.search("ws_embed", [1.0, 0.0, 0.0], top_k=5)
        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "e1"
