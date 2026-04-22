"""Unit tests for VectorStoreActor.

Covers: actor lifecycle (on_start), all proxy method delegation, error
handling (RetriableError, catch/log/swallow), state persistence round-trip,
collection status tracking, and graceful degradation when backend is
unavailable.

Pattern: Instantiate VectorStoreActor() directly, set config, call
on_start(). Same approach as test_kg_actor.py and test_planning_actor.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from akgentic.tool.errors import RetriableError
from akgentic.tool.vector_store.actor import (
    VS_ACTOR_NAME,
    VS_ACTOR_ROLE,
    VectorStoreActor,
    VectorStoreState,
)
from akgentic.tool.vector_store.embedding_actor import (
    EmbeddingError,
    EmbeddingResult,
)
from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    SearchResult,
    VectorStoreConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor() -> VectorStoreActor:
    """Create and initialise a VectorStoreActor for testing."""
    actor = VectorStoreActor()
    actor.config = VectorStoreConfig(name=VS_ACTOR_NAME, role=VS_ACTOR_ROLE)
    actor.on_start()
    return actor


def _mock_backend() -> MagicMock:
    """Return a MagicMock that mimics InMemoryBackend."""
    backend = MagicMock()
    backend.get_state.return_value = {"collections": {}}
    return backend


def _mock_entry(
    ref_id: str = "e1",
    ref_type: str = "entity",
    text: str = "hello",
    vector: list[float] | None = None,
) -> MagicMock:
    """Return a MagicMock that mimics VectorEntry.

    Args:
        ref_id: Entry reference ID.
        ref_type: Entry reference type.
        text: Entry text.
        vector: Embedding vector; empty list means needs-embedding.
    """
    entry = MagicMock()
    entry.ref_id = ref_id
    entry.ref_type = ref_type
    entry.text = text
    entry.vector = vector if vector is not None else []
    return entry


# ---------------------------------------------------------------------------
# VectorStoreState (AC11)
# ---------------------------------------------------------------------------


class TestVectorStoreState:
    """AC11: VectorStoreState(BaseState) construction and serialisation."""

    def test_construction_defaults(self) -> None:
        """State has empty defaults."""
        state = VectorStoreState()
        assert state.backend_state == {}
        assert state.collection_statuses == {}

    def test_serialisation_round_trip(self) -> None:
        """State round-trips through Pydantic serialisation."""
        state = VectorStoreState(
            backend_state={"collections": {"c1": {"config": {}, "entries": []}}},
            collection_statuses={"c1": CollectionStatus.READY},
        )
        data = state.model_dump()
        restored = VectorStoreState.model_validate(data)
        assert restored.backend_state == state.backend_state
        assert restored.collection_statuses == state.collection_statuses

    def test_collection_configs_round_trip(self) -> None:
        """collection_configs survives Pydantic serialisation."""
        state = VectorStoreState(
            collection_configs={
                "c1": {
                    "dimension": 128,
                    "backend": "inmemory",
                    "persistence": "workspace",
                    "workspace_path": "/tmp/ws",
                }
            },
        )
        data = state.model_dump()
        restored = VectorStoreState.model_validate(data)
        assert restored.collection_configs == state.collection_configs
        assert restored.collection_configs["c1"]["persistence"] == "workspace"

    def test_collection_configs_defaults_empty(self) -> None:
        """collection_configs defaults to empty dict."""
        state = VectorStoreState()
        assert state.collection_configs == {}


# ---------------------------------------------------------------------------
# Actor lifecycle (AC1, AC2, AC3, AC10)
# ---------------------------------------------------------------------------


class TestActorLifecycle:
    """AC1-3, AC10: Actor class, constants, on_start, runtime state."""

    def test_on_start_initialises_state_with_observer(self) -> None:
        """AC3: on_start sets state with observer wired."""
        actor = _make_actor()
        assert isinstance(actor.state, VectorStoreState)
        # Observer is wired — notify_state_change should not raise
        actor.state.notify_state_change()

    def test_on_start_backend_is_none(self) -> None:
        """AC10: Backend starts as None (lazy)."""
        actor = _make_actor()
        assert actor._backend is None

    def test_on_start_embedding_svc_is_none(self) -> None:
        """AC10: Embedding service starts as None (lazy)."""
        actor = _make_actor()
        assert actor._embedding_svc is None

    def test_singleton_constants(self) -> None:
        """AC2: Constants have expected values."""
        assert VS_ACTOR_NAME == "#VectorStore"
        assert VS_ACTOR_ROLE == "ToolActor"


# ---------------------------------------------------------------------------
# create_collection (AC4, AC9, AC12)
# ---------------------------------------------------------------------------


class TestCreateCollection:
    """AC4: create_collection delegates to backend and sets status."""

    def test_delegates_to_backend(self) -> None:
        """AC4: Delegation to InMemoryBackend.create_collection."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        config = CollectionConfig()
        actor.create_collection("test_col", config)

        backend.create_collection.assert_called_once_with("test_col", config)

    def test_sets_status_ready(self) -> None:
        """AC4: Collection status is READY after creation."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        actor.create_collection("test_col", CollectionConfig())
        assert actor.state.collection_statuses["test_col"] == CollectionStatus.READY

    def test_populates_collection_configs(self) -> None:
        """create_collection stores config dict in state.collection_configs."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        config = CollectionConfig(dimension=128, persistence="workspace", workspace_path="/ws")
        actor.create_collection("test_col", config)
        assert "test_col" in actor.state.collection_configs
        cfg = actor.state.collection_configs["test_col"]
        assert cfg["dimension"] == 128
        assert cfg["persistence"] == "workspace"
        assert cfg["workspace_path"] == "/ws"
        assert cfg["backend"] == "inmemory"

    def test_notifies_state_change(self) -> None:
        """AC12: state.notify_state_change() called after creation."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        with patch.object(VectorStoreState, "notify_state_change") as mock_notify:
            actor.create_collection("test_col", CollectionConfig())
            mock_notify.assert_called_once()

    def test_syncs_backend_state(self) -> None:
        """AC11: _sync_backend_state called after creation."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.get_state.return_value = {"collections": {"test_col": {}}}
        actor._backend = backend

        actor.create_collection("test_col", CollectionConfig())
        assert actor.state.backend_state == {"collections": {"test_col": {}}}

    def test_idempotent_second_call(self) -> None:
        """AC4: Second create_collection for same name is no-op (via backend)."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        actor.create_collection("test_col", CollectionConfig())
        actor.create_collection("test_col", CollectionConfig())
        assert backend.create_collection.call_count == 2
        # Backend itself handles idempotency (no-op on existing collection)

    def test_graceful_when_backend_unavailable(self) -> None:
        """AC9: Logs warning when backend cannot be created."""
        actor = _make_actor()
        with patch.object(actor, "_get_or_create_backend", return_value=None):
            # Should not raise
            actor.create_collection("test_col", CollectionConfig())
            assert "test_col" not in actor.state.collection_statuses


# ---------------------------------------------------------------------------
# add (AC5, AC9, AC12)
# ---------------------------------------------------------------------------


class TestAdd:
    """AC5: add delegates to backend with state notification."""

    def test_pre_embedded_delegates_to_backend(self) -> None:
        """AC9: Pre-embedded entries go directly to backend.add()."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(vector=[0.1, 0.2])

        actor.add("col1", [entry])
        backend.add.assert_called_once_with("col1", [entry])

    def test_pre_embedded_notifies_state_change(self) -> None:
        """AC12: state.notify_state_change() called after pre-embedded add."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(vector=[0.1])

        with patch.object(VectorStoreState, "notify_state_change") as mock_notify:
            actor.add("col1", [entry])
            mock_notify.assert_called_once()

    def test_pre_embedded_nonexistent_collection_raises_retriable(self) -> None:
        """AC9: ValueError from backend becomes RetriableError."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.add.side_effect = ValueError("Collection 'col1' does not exist")
        actor._backend = backend
        entry = _mock_entry(vector=[0.1])

        with pytest.raises(RetriableError, match="does not exist"):
            actor.add("col1", [entry])

    def test_pre_embedded_unexpected_error_swallowed(self) -> None:
        """AC9: Unexpected errors caught/logged/swallowed."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.add.side_effect = RuntimeError("unexpected")
        actor._backend = backend
        entry = _mock_entry(vector=[0.1])

        # Should not raise
        actor.add("col1", [entry])

    def test_empty_entries_no_op(self) -> None:
        """Empty entries list is a no-op."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        actor.add("col1", [])
        backend.add.assert_not_called()

    def test_backend_unavailable_skips(self) -> None:
        """When backend is None, add() logs and returns."""
        actor = _make_actor()
        with patch.object(actor, "_get_or_create_backend", return_value=None):
            actor.add("col1", [_mock_entry(vector=[0.1])])


# ---------------------------------------------------------------------------
# Non-blocking add — needs embedding (AC2, AC3, AC8)
# ---------------------------------------------------------------------------


class TestAddNeedsEmbedding:
    """AC2/AC3: Entries without vectors spawn EmbeddingActor."""

    def test_spawns_embedding_actor(self) -> None:
        """AC2: add() with vectorless entries spawns EmbeddingActor."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(vector=[])

        mock_addr = MagicMock()
        mock_proxy = MagicMock()
        with (
            patch.object(actor, "createActor", return_value=mock_addr) as mock_create,
            patch.object(actor, "proxy_tell", return_value=mock_proxy),
        ):
            actor.add("col1", [entry])
            mock_create.assert_called_once()
            mock_proxy.receiveMsg_EmbeddingRequest.assert_called_once()

    def test_sets_status_indexing(self) -> None:
        """AC3: Collection status transitions to INDEXING on add."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(vector=[])

        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", [entry])

        assert actor.state.collection_statuses["col1"] == CollectionStatus.INDEXING

    def test_tracks_indexing_pending_count(self) -> None:
        """AC3: indexing_pending incremented by number of entries."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entries = [_mock_entry(ref_id="e1"), _mock_entry(ref_id="e2")]

        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", entries)

        assert actor.state.indexing_pending["col1"] == 2

    def test_stores_pending_entries(self) -> None:
        """Pending entry metadata stored in state."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(ref_id="e1", text="hello")

        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", [entry])

        assert len(actor.state.pending_entries["col1"]) == 1
        assert actor.state.pending_entries["col1"][0]["ref_id"] == "e1"

    def test_does_not_call_backend_add(self) -> None:
        """AC2: Vectorless entries do NOT call backend.add() directly."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        entry = _mock_entry(vector=[])

        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", [entry])

        backend.add.assert_not_called()

    def test_retry_from_error_resets_to_indexing(self) -> None:
        """AC8: add() on ERROR collection transitions to INDEXING."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.ERROR

        entry = _mock_entry(vector=[])
        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", [entry])

        assert actor.state.collection_statuses["col1"] == CollectionStatus.INDEXING

    def test_mixed_entries_partitioned(self) -> None:
        """AC9: Mixed entries: pre-embedded go to backend, vectorless to actor."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        pre = _mock_entry(ref_id="pre1", vector=[0.1, 0.2])
        needs = _mock_entry(ref_id="needs1", vector=[])

        with (
            patch.object(actor, "createActor", return_value=MagicMock()),
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
        ):
            actor.add("col1", [pre, needs])

        # Pre-embedded goes to backend
        backend.add.assert_called_once_with("col1", [pre])
        # Needs-embedding tracked in pending
        assert actor.state.indexing_pending["col1"] == 1


# ---------------------------------------------------------------------------
# receiveMsg_EmbeddingResult (AC5)
# ---------------------------------------------------------------------------


class TestReceiveEmbeddingResult:
    """AC5: VectorStoreActor handles embedding results."""

    def _make_result(self, collection: str = "col1") -> EmbeddingResult:
        """Create a mock EmbeddingResult with VectorEntry objects."""
        from akgentic.tool.vector import VectorEntry

        entries = [
            VectorEntry(ref_type="entity", ref_id="e1", text="hi", vector=[0.1]),
            VectorEntry(ref_type="entity", ref_id="e2", text="bye", vector=[0.2]),
        ]
        return EmbeddingResult(
            collection=collection, entries=entries, request_id="req-1"
        )

    def test_inserts_into_backend(self) -> None:
        """AC5: Entries are inserted into backend on result delivery."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
            {"ref_type": "entity", "ref_id": "e2", "text": "bye"},
        ]

        result = self._make_result()
        actor.receiveMsg_EmbeddingResult(result)

        backend.add.assert_called_once_with("col1", result.entries)

    def test_transitions_to_ready(self) -> None:
        """AC5: Status returns to READY when all pending complete."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
            {"ref_type": "entity", "ref_id": "e2", "text": "bye"},
        ]

        actor.receiveMsg_EmbeddingResult(self._make_result())

        assert actor.state.collection_statuses["col1"] == CollectionStatus.READY
        assert actor.state.indexing_pending.get("col1") is None

    def test_decrements_pending_count(self) -> None:
        """AC5: indexing_pending decremented by result entry count."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        # Simulate 4 pending, result delivers 2
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 4
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": f"e{i}", "text": f"t{i}"}
            for i in range(4)
        ]

        actor.receiveMsg_EmbeddingResult(self._make_result())

        assert actor.state.indexing_pending["col1"] == 2
        assert actor.state.collection_statuses["col1"] == CollectionStatus.INDEXING

    def test_notifies_state_change(self) -> None:
        """state.notify_state_change() called after result delivery."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
            {"ref_type": "entity", "ref_id": "e2", "text": "bye"},
        ]

        with patch.object(VectorStoreState, "notify_state_change") as mock_notify:
            actor.receiveMsg_EmbeddingResult(self._make_result())
            assert mock_notify.call_count >= 1

    def test_backend_add_failure_transitions_to_error(self) -> None:
        """When backend.add() fails, collection moves to ERROR (not stuck in INDEXING)."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.add.side_effect = RuntimeError("disk full")
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
            {"ref_type": "entity", "ref_id": "e2", "text": "bye"},
        ]

        actor.receiveMsg_EmbeddingResult(self._make_result())

        assert actor.state.collection_statuses["col1"] == CollectionStatus.ERROR
        assert actor.state.indexing_pending["col1"] == 0
        assert "col1" not in actor.state.pending_entries


# ---------------------------------------------------------------------------
# receiveMsg_EmbeddingError (AC7)
# ---------------------------------------------------------------------------


class TestReceiveEmbeddingError:
    """AC7: VectorStoreActor handles embedding errors."""

    def test_sets_error_status(self) -> None:
        """AC7: Collection status set to ERROR."""
        actor = _make_actor()
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
        ]

        err = EmbeddingError(collection="col1", error="API failed", request_id="r1")
        actor.receiveMsg_EmbeddingError(err)

        assert actor.state.collection_statuses["col1"] == CollectionStatus.ERROR

    def test_discards_pending_entries(self) -> None:
        """AC7: Pending entries discarded on error."""
        actor = _make_actor()
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 2
        actor.state.pending_entries["col1"] = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hi"},
        ]

        err = EmbeddingError(collection="col1", error="API failed", request_id="r1")
        actor.receiveMsg_EmbeddingError(err)

        assert actor.state.indexing_pending["col1"] == 0
        assert "col1" not in actor.state.pending_entries

    def test_notifies_state_change(self) -> None:
        """state.notify_state_change() called after error."""
        actor = _make_actor()
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING

        with patch.object(VectorStoreState, "notify_state_change") as mock_notify:
            err = EmbeddingError(
                collection="col1", error="fail", request_id="r1"
            )
            actor.receiveMsg_EmbeddingError(err)
            assert mock_notify.call_count >= 1


# ---------------------------------------------------------------------------
# Search during INDEXING (AC6)
# ---------------------------------------------------------------------------


class TestSearchDuringIndexing:
    """AC6: search() returns partial results with INDEXING status."""

    def test_returns_indexing_status(self) -> None:
        """AC6: Search returns status=INDEXING when collection is indexing."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.search.return_value = SearchResult(
            hits=[], status=CollectionStatus.READY, indexing_pending=0
        )
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.INDEXING
        actor.state.indexing_pending["col1"] = 3

        result = actor.search("col1", [0.1], 5)
        assert result.status == CollectionStatus.INDEXING
        assert result.indexing_pending == 3

    def test_returns_ready_when_not_indexing(self) -> None:
        """Search returns READY status when no indexing."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.search.return_value = SearchResult(
            hits=[], status=CollectionStatus.READY, indexing_pending=0
        )
        actor._backend = backend
        actor.state.collection_statuses["col1"] = CollectionStatus.READY

        result = actor.search("col1", [0.1], 5)
        assert result.status == CollectionStatus.READY
        assert result.indexing_pending == 0


# ---------------------------------------------------------------------------
# State serialisation with new fields
# ---------------------------------------------------------------------------


class TestVectorStoreStateNewFields:
    """Pending entries and indexing_pending serialise correctly."""

    def test_pending_entries_round_trip(self) -> None:
        """pending_entries survives serialisation."""
        state = VectorStoreState(
            pending_entries={"c1": [{"ref_type": "t", "ref_id": "1", "text": "hi"}]},
            indexing_pending={"c1": 1},
        )
        data = state.model_dump()
        restored = VectorStoreState.model_validate(data)
        assert restored.pending_entries == state.pending_entries
        assert restored.indexing_pending == state.indexing_pending

    def test_defaults_empty(self) -> None:
        """New fields default to empty dicts."""
        state = VectorStoreState()
        assert state.pending_entries == {}
        assert state.indexing_pending == {}


# ---------------------------------------------------------------------------
# remove (AC6, AC9, AC12)
# ---------------------------------------------------------------------------


class TestRemove:
    """AC6: remove delegates to backend with state notification."""

    def test_delegates_to_backend(self) -> None:
        """AC6: Delegation to InMemoryBackend.remove."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        actor.remove("col1", ["id1", "id2"])
        backend.remove.assert_called_once_with("col1", ["id1", "id2"])

    def test_notifies_state_change(self) -> None:
        """AC12: state.notify_state_change() called after remove."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        with patch.object(VectorStoreState, "notify_state_change") as mock_notify:
            actor.remove("col1", ["id1"])
            mock_notify.assert_called_once()

    def test_nonexistent_collection_raises_retriable(self) -> None:
        """AC9: ValueError from backend becomes RetriableError."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.remove.side_effect = ValueError("Collection 'col1' does not exist")
        actor._backend = backend

        with pytest.raises(RetriableError, match="does not exist"):
            actor.remove("col1", ["id1"])

    def test_unexpected_error_swallowed(self) -> None:
        """AC9: Unexpected errors caught/logged/swallowed."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.remove.side_effect = RuntimeError("unexpected")
        actor._backend = backend

        actor.remove("col1", ["id1"])


# ---------------------------------------------------------------------------
# search (AC7, AC9)
# ---------------------------------------------------------------------------


class TestSearch:
    """AC7: search delegates to backend and returns SearchResult."""

    def test_delegates_to_backend(self) -> None:
        """AC7: Delegation to InMemoryBackend.search."""
        actor = _make_actor()
        backend = _mock_backend()
        expected = SearchResult(
            hits=[],
            status=CollectionStatus.READY,
            indexing_pending=0,
        )
        backend.search.return_value = expected
        actor._backend = backend

        result = actor.search("col1", [0.1, 0.2], 5)
        backend.search.assert_called_once_with("col1", [0.1, 0.2], 5)
        assert result == expected

    def test_nonexistent_collection_raises_retriable(self) -> None:
        """AC9: ValueError from backend becomes RetriableError."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.search.side_effect = ValueError("Collection 'col1' does not exist")
        actor._backend = backend

        with pytest.raises(RetriableError, match="does not exist"):
            actor.search("col1", [0.1], 5)

    def test_unexpected_error_returns_empty(self) -> None:
        """AC9: Unexpected errors return empty SearchResult."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.search.side_effect = RuntimeError("unexpected")
        actor._backend = backend

        result = actor.search("col1", [0.1], 5)
        assert result.hits == []

    def test_backend_unavailable_returns_empty(self) -> None:
        """AC9: No backend returns empty SearchResult."""
        actor = _make_actor()
        with patch.object(actor, "_get_or_create_backend", return_value=None):
            result = actor.search("col1", [0.1], 5)
            assert result.hits == []


# ---------------------------------------------------------------------------
# embed (AC8, AC9)
# ---------------------------------------------------------------------------


class TestEmbed:
    """AC8: embed delegates to EmbeddingService and returns vectors."""

    def test_delegates_to_embedding_service(self) -> None:
        """AC8: Delegation to EmbeddingService.embed."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1, 0.2, 0.3]]
        actor._embedding_svc = mock_svc

        result = actor.embed(["hello"])
        mock_svc.embed.assert_called_once_with(["hello"])
        assert result == [[0.1, 0.2, 0.3]]

    def test_returns_empty_when_service_unavailable(self) -> None:
        """AC8: Returns [] when embedding service is None."""
        actor = _make_actor()
        with patch.object(actor, "_get_or_create_embedding_svc", return_value=None):
            result = actor.embed(["hello"])
            assert result == []

    def test_returns_empty_on_failure(self) -> None:
        """AC9: Catch/log/swallow on embed failure."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.side_effect = RuntimeError("API error")
        actor._embedding_svc = mock_svc

        result = actor.embed(["hello"])
        assert result == []


# ---------------------------------------------------------------------------
# State persistence round-trip (AC11)
# ---------------------------------------------------------------------------


class TestStatePersistence:
    """AC11: Backend state persistence round-trip via actor state."""

    def test_round_trip_through_actor_state(self) -> None:
        """Create collection, add entries, verify state round-trip."""
        actor = _make_actor()

        # Use a real-ish backend mock that tracks state
        backend = _mock_backend()
        state_snapshot: dict[str, Any] = {
            "collections": {
                "test_col": {
                    "config": CollectionConfig().model_dump(),
                    "entries": [
                        {
                            "ref_type": "test",
                            "ref_id": "e1",
                            "text": "hello",
                            "vector": [0.1, 0.2],
                        }
                    ],
                }
            }
        }
        backend.get_state.return_value = state_snapshot
        actor._backend = backend

        # Trigger a mutation to sync state
        actor.create_collection("test_col", CollectionConfig())

        # Verify actor state has the snapshot
        assert actor.state.backend_state == state_snapshot

        # Now create a new actor and verify restore
        actor2 = _make_actor()
        actor2.state.backend_state = state_snapshot

        # The lazy init should restore from state
        import akgentic.tool.vector_store.inmemory as inmemory_mod

        mock_backend2 = _mock_backend()
        original_cls = inmemory_mod.InMemoryBackend
        inmemory_mod.InMemoryBackend = MagicMock(return_value=mock_backend2)  # type: ignore[misc]
        try:
            result = actor2._get_or_create_backend()
            assert result is not None
            mock_backend2.restore_state.assert_called_once_with(state_snapshot)
        finally:
            inmemory_mod.InMemoryBackend = original_cls  # type: ignore[misc]


# ---------------------------------------------------------------------------
# collection_statuses (AC11, AC12)
# ---------------------------------------------------------------------------


class TestCollectionStatuses:
    """AC11: collection_statuses tracks per-collection status."""

    def test_tracks_multiple_collections(self) -> None:
        """Multiple collections tracked independently."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        actor.create_collection("col_a", CollectionConfig())
        actor.create_collection("col_b", CollectionConfig())

        assert actor.state.collection_statuses["col_a"] == CollectionStatus.READY
        assert actor.state.collection_statuses["col_b"] == CollectionStatus.READY

    def test_status_in_serialised_state(self) -> None:
        """Collection statuses survive serialisation."""
        state = VectorStoreState(
            collection_statuses={"c1": CollectionStatus.READY, "c2": CollectionStatus.INDEXING}
        )
        data = state.model_dump()
        restored = VectorStoreState.model_validate(data)
        assert restored.collection_statuses["c1"] == CollectionStatus.READY
        assert restored.collection_statuses["c2"] == CollectionStatus.INDEXING


# ---------------------------------------------------------------------------
# Lazy backend initialisation (AC3, AC10)
# ---------------------------------------------------------------------------


class TestLazyBackend:
    """AC3/AC10: Lazy backend and embedding service initialisation."""

    def test_get_or_create_backend_caches(self) -> None:
        """Backend is cached after first creation."""
        actor = _make_actor()
        mock_backend = _mock_backend()
        actor._backend = mock_backend

        result = actor._get_or_create_backend()
        assert result is mock_backend

    def test_get_or_create_backend_returns_none_on_import_error(self) -> None:
        """Returns None when vector_search deps missing."""
        actor = _make_actor()
        # Patch the inmemory module so importing InMemoryBackend raises
        import akgentic.tool.vector_store.inmemory as inmemory_mod

        original_cls = inmemory_mod.InMemoryBackend
        inmemory_mod.InMemoryBackend = MagicMock(  # type: ignore[misc]
            side_effect=ImportError("no numpy"),
        )
        try:
            result = actor._get_or_create_backend()
            assert result is None
        finally:
            inmemory_mod.InMemoryBackend = original_cls  # type: ignore[misc]

    def test_get_or_create_embedding_svc_caches(self) -> None:
        """Embedding service is cached after first creation."""
        actor = _make_actor()
        mock_svc = MagicMock()
        actor._embedding_svc = mock_svc

        result = actor._get_or_create_embedding_svc()
        assert result is mock_svc

    def test_get_or_create_embedding_svc_returns_none_on_failure(self) -> None:
        """Returns None when EmbeddingService creation fails."""
        actor = _make_actor()
        import akgentic.tool.vector as vector_mod

        original_cls = vector_mod.EmbeddingService
        vector_mod.EmbeddingService = MagicMock(  # type: ignore[misc]
            side_effect=Exception("no API key"),
        )
        try:
            result = actor._get_or_create_embedding_svc()
            assert result is None
        finally:
            vector_mod.EmbeddingService = original_cls  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _save_workspace_collection error handling
# ---------------------------------------------------------------------------


class TestSaveWorkspaceCollection:
    """Workspace save error handling: logs warning and does not raise."""

    def test_save_logs_warning_on_backend_error(self) -> None:
        """_save_workspace_collection catches and logs backend save failure."""
        actor = _make_actor()
        backend = _mock_backend()
        backend.save_collection.side_effect = OSError("disk full")
        actor._backend = backend
        actor.state.collection_configs["col1"] = {
            "dimension": 3,
            "backend": "inmemory",
            "persistence": "workspace",
            "workspace_path": "/tmp/ws",
        }
        # Should not raise
        actor._save_workspace_collection("col1")
        backend.save_collection.assert_called_once_with("col1", "/tmp/ws")

    def test_save_skipped_for_actor_state_persistence(self) -> None:
        """_save_workspace_collection is a no-op for actor_state persistence."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        actor.state.collection_configs["col1"] = {
            "dimension": 3,
            "backend": "inmemory",
            "persistence": "actor_state",
            "workspace_path": None,
        }
        actor._save_workspace_collection("col1")
        backend.save_collection.assert_not_called()

    def test_save_skipped_for_unknown_collection(self) -> None:
        """_save_workspace_collection is a no-op for unknown collection."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        # No collection_configs entry
        actor._save_workspace_collection("unknown_col")
        backend.save_collection.assert_not_called()


# ---------------------------------------------------------------------------
# Public API exports (AC13)
# ---------------------------------------------------------------------------


class TestPublicApiExports:
    """AC13: vector_store/__init__.py re-exports actor symbols."""

    def test_actor_exported(self) -> None:
        """VectorStoreActor in __all__."""
        import akgentic.tool.vector_store as vs

        assert "VectorStoreActor" in vs.__all__
        assert hasattr(vs, "VectorStoreActor")

    def test_state_exported(self) -> None:
        """VectorStoreState in __all__."""
        import akgentic.tool.vector_store as vs

        assert "VectorStoreState" in vs.__all__
        assert hasattr(vs, "VectorStoreState")

    def test_constants_exported(self) -> None:
        """VS_ACTOR_NAME and VS_ACTOR_ROLE in __all__."""
        import akgentic.tool.vector_store as vs

        assert "VS_ACTOR_NAME" in vs.__all__
        assert "VS_ACTOR_ROLE" in vs.__all__
        assert vs.VS_ACTOR_NAME == "#VectorStore"
        assert vs.VS_ACTOR_ROLE == "ToolActor"

    def test_embedding_actor_exported(self) -> None:
        """EmbeddingActor and message models in __all__."""
        import akgentic.tool.vector_store as vs

        assert "EmbeddingActor" in vs.__all__
        assert "EmbeddingRequest" in vs.__all__
        assert "EmbeddingResult" in vs.__all__
        assert "EmbeddingError" in vs.__all__
        assert hasattr(vs, "EmbeddingActor")
        assert hasattr(vs, "EmbeddingRequest")
        assert hasattr(vs, "EmbeddingResult")
        assert hasattr(vs, "EmbeddingError")


# ---------------------------------------------------------------------------
# Weaviate backend routing (AC11 — Story 12.1)
# ---------------------------------------------------------------------------


class TestWeaviateRouting:
    """AC11: VectorStoreActor routes weaviate collections to WeaviateBackend."""

    def test_create_collection_routes_to_weaviate(self) -> None:
        """create_collection with backend='weaviate' uses WeaviateBackend."""
        actor = _make_actor()
        actor.config = VectorStoreConfig(
            name=VS_ACTOR_NAME,
            role=VS_ACTOR_ROLE,
            weaviate_url="http://localhost:8080",
        )
        mock_wb = MagicMock()
        actor._weaviate_backend = mock_wb

        config = CollectionConfig(backend="weaviate", dimension=384)
        actor.create_collection("wv_col", config)

        mock_wb.create_collection.assert_called_once_with("wv_col", config)
        assert actor.state.collection_configs["wv_col"]["backend"] == "weaviate"
        assert actor.state.collection_statuses["wv_col"] == CollectionStatus.READY

    def test_create_collection_inmemory_still_works(self) -> None:
        """create_collection with backend='inmemory' still routes to InMemoryBackend."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        config = CollectionConfig(backend="inmemory")
        actor.create_collection("im_col", config)

        backend.create_collection.assert_called_once_with("im_col", config)
        assert actor.state.collection_configs["im_col"]["backend"] == "inmemory"

    def test_add_routes_to_weaviate_backend(self) -> None:
        """add() for a weaviate collection routes to WeaviateBackend."""
        actor = _make_actor()
        mock_wb = MagicMock()
        actor._weaviate_backend = mock_wb
        actor.state.collection_configs["wv_col"] = {"backend": "weaviate"}

        entry = _mock_entry(vector=[0.1, 0.2])
        actor.add("wv_col", [entry])

        mock_wb.add.assert_called_once_with("wv_col", [entry])

    def test_remove_routes_to_weaviate_backend(self) -> None:
        """remove() for a weaviate collection routes to WeaviateBackend."""
        actor = _make_actor()
        mock_wb = MagicMock()
        actor._weaviate_backend = mock_wb
        actor.state.collection_configs["wv_col"] = {"backend": "weaviate"}

        actor.remove("wv_col", ["id1"])

        mock_wb.remove.assert_called_once_with("wv_col", ["id1"])

    def test_search_routes_to_weaviate_backend(self) -> None:
        """search() for a weaviate collection routes to WeaviateBackend."""
        actor = _make_actor()
        mock_wb = MagicMock()
        expected = SearchResult(hits=[], status=CollectionStatus.READY, indexing_pending=0)
        mock_wb.search.return_value = expected
        actor._weaviate_backend = mock_wb
        actor.state.collection_configs["wv_col"] = {"backend": "weaviate"}

        result = actor.search("wv_col", [0.1], 5)

        mock_wb.search.assert_called_once_with("wv_col", [0.1], 5)
        assert result == expected

    def test_inmemory_collection_not_routed_to_weaviate(self) -> None:
        """inmemory collections still go to InMemoryBackend even when weaviate is available."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend
        mock_wb = MagicMock()
        actor._weaviate_backend = mock_wb
        actor.state.collection_configs["im_col"] = {"backend": "inmemory"}

        entry = _mock_entry(vector=[0.1])
        actor.add("im_col", [entry])

        backend.add.assert_called_once()
        mock_wb.add.assert_not_called()

    def test_weaviate_backend_unavailable_logs_warning(self) -> None:
        """create_collection logs warning when weaviate backend is unavailable."""
        actor = _make_actor()
        actor.config = VectorStoreConfig(name=VS_ACTOR_NAME, role=VS_ACTOR_ROLE)
        # No weaviate_url => _get_or_create_weaviate_backend returns None

        config = CollectionConfig(backend="weaviate")
        actor.create_collection("wv_col", config)

        # Should not crash, just skip
        assert "wv_col" not in actor.state.collection_statuses

    def test_weaviate_no_sync_backend_state(self) -> None:
        """Weaviate collections should NOT call _sync_backend_state."""
        actor = _make_actor()
        mock_wb = MagicMock()
        actor._weaviate_backend = mock_wb
        actor.config = VectorStoreConfig(
            name=VS_ACTOR_NAME,
            role=VS_ACTOR_ROLE,
            weaviate_url="http://localhost:8080",
        )

        config = CollectionConfig(backend="weaviate")
        actor.create_collection("wv_col", config)

        # backend_state should still be empty (not synced for weaviate)
        assert actor.state.backend_state == {}

    def test_get_backend_for_collection_defaults_to_inmemory(self) -> None:
        """Unknown collections default to inmemory backend."""
        actor = _make_actor()
        backend = _mock_backend()
        actor._backend = backend

        result = actor._get_backend_for_collection("unknown")
        assert result is backend
