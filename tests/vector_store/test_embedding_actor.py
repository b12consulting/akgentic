"""Unit tests for EmbeddingActor.

Covers: actor lifecycle (on_start), receiveMsg_EmbeddingRequest success
and failure paths, result/error delivery to parent, and self-stop.

Pattern: Instantiate EmbeddingActor() directly, set config, call
on_start(). Mock parent address and proxy_tell to capture messages.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from akgentic.core.agent_config import BaseConfig

from akgentic.tool.vector_store.embedding_actor import (
    EmbeddingActor,
    EmbeddingError,
    EmbeddingRequest,
    EmbeddingResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor() -> EmbeddingActor:
    """Create and initialise an EmbeddingActor for testing."""
    actor = EmbeddingActor()
    actor.config = BaseConfig(name="test-embed-actor", role="EmbeddingActor")
    actor._parent = MagicMock()
    actor.on_start()
    return actor


def _make_request(
    collection: str = "test_col",
    entries: list[dict[str, str]] | None = None,
) -> EmbeddingRequest:
    """Create a test EmbeddingRequest."""
    if entries is None:
        entries = [
            {"ref_type": "entity", "ref_id": "e1", "text": "hello world"},
            {"ref_type": "entity", "ref_id": "e2", "text": "goodbye"},
        ]
    return EmbeddingRequest(
        collection=collection,
        entries=entries,
        request_id="test-req-1",
        embedding_model="text-embedding-3-small",
        embedding_provider="openai",
    )


# ---------------------------------------------------------------------------
# Construction and on_start (AC1)
# ---------------------------------------------------------------------------


class TestEmbeddingActorLifecycle:
    """AC1: EmbeddingActor construction and on_start."""

    def test_on_start_initialises_state(self) -> None:
        """on_start sets state with observer."""
        actor = _make_actor()
        assert actor.state is not None
        actor.state.notify_state_change()

    def test_on_start_embedding_svc_is_none(self) -> None:
        """Embedding service starts as None (lazy)."""
        actor = _make_actor()
        assert actor._embedding_svc is None


# ---------------------------------------------------------------------------
# receiveMsg_EmbeddingRequest — success path (AC4)
# ---------------------------------------------------------------------------


class TestEmbeddingRequestSuccess:
    """AC4: EmbeddingActor embeds and delivers results to parent."""

    def test_sends_embedding_result_to_parent(self) -> None:
        """On success, EmbeddingResult is sent to parent via proxy_tell."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1, 0.2], [0.3, 0.4]]
        actor._embedding_svc = mock_svc

        mock_proxy = MagicMock()
        captured_results: list[EmbeddingResult] = []

        def capture_result(msg: EmbeddingResult) -> None:
            captured_results.append(msg)

        mock_proxy.receiveMsg_EmbeddingResult = capture_result

        with (
            patch.object(actor, "proxy_tell", return_value=mock_proxy),
            patch.object(actor, "stop"),
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())

        assert len(captured_results) == 1
        result = captured_results[0]
        assert result.collection == "test_col"
        assert result.request_id == "test-req-1"
        assert len(result.entries) == 2
        assert result.entries[0].ref_id == "e1"
        assert result.entries[0].vector == [0.1, 0.2]
        assert result.entries[1].ref_id == "e2"
        assert result.entries[1].vector == [0.3, 0.4]

    def test_calls_embedding_service_with_texts(self) -> None:
        """EmbeddingService.embed() is called with the entry texts."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1], [0.2]]
        actor._embedding_svc = mock_svc

        with (
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
            patch.object(actor, "stop"),
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())

        mock_svc.embed.assert_called_once_with(["hello world", "goodbye"])


# ---------------------------------------------------------------------------
# receiveMsg_EmbeddingRequest — failure path (AC4, AC7)
# ---------------------------------------------------------------------------


class TestEmbeddingRequestFailure:
    """AC4/AC7: EmbeddingActor sends error on embedding failure."""

    def test_sends_embedding_error_on_exception(self) -> None:
        """On embed failure, EmbeddingError is sent to parent."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.side_effect = RuntimeError("API timeout")
        actor._embedding_svc = mock_svc

        mock_proxy = MagicMock()
        captured_errors: list[EmbeddingError] = []

        def capture_error(msg: EmbeddingError) -> None:
            captured_errors.append(msg)

        mock_proxy.receiveMsg_EmbeddingError = capture_error

        with (
            patch.object(actor, "proxy_tell", return_value=mock_proxy),
            patch.object(actor, "stop"),
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())

        assert len(captured_errors) == 1
        err = captured_errors[0]
        assert err.collection == "test_col"
        assert "API timeout" in err.error
        assert err.request_id == "test-req-1"

    def test_sends_error_when_svc_unavailable(self) -> None:
        """When EmbeddingService cannot be created, sends error."""
        actor = _make_actor()

        mock_proxy = MagicMock()
        captured_errors: list[EmbeddingError] = []

        def capture_error(msg: EmbeddingError) -> None:
            captured_errors.append(msg)

        mock_proxy.receiveMsg_EmbeddingError = capture_error

        with (
            patch.object(
                actor,
                "_get_or_create_embedding_svc",
                return_value=None,
            ),
            patch.object(actor, "proxy_tell", return_value=mock_proxy),
            patch.object(actor, "stop"),
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())

        assert len(captured_errors) == 1
        assert "unavailable" in captured_errors[0].error.lower()


# ---------------------------------------------------------------------------
# Actor stops itself after delivery (AC4)
# ---------------------------------------------------------------------------


class TestEmbeddingActorStopsSelf:
    """AC4: EmbeddingActor stops itself after result or error delivery."""

    def test_stops_after_success(self) -> None:
        """Actor calls self.stop() after sending result."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1], [0.2]]
        actor._embedding_svc = mock_svc

        with (
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
            patch.object(actor, "stop") as mock_stop,
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())
            mock_stop.assert_called_once()

    def test_stops_after_failure(self) -> None:
        """Actor calls self.stop() after sending error."""
        actor = _make_actor()
        mock_svc = MagicMock()
        mock_svc.embed.side_effect = RuntimeError("fail")
        actor._embedding_svc = mock_svc

        with (
            patch.object(actor, "proxy_tell", return_value=MagicMock()),
            patch.object(actor, "stop") as mock_stop,
        ):
            actor.receiveMsg_EmbeddingRequest(_make_request())
            mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# No-parent guard (null safety)
# ---------------------------------------------------------------------------


class TestNoParentGuard:
    """Verify EmbeddingActor handles missing parent gracefully."""

    def test_no_parent_on_success_path(self) -> None:
        """When _parent is None, result delivery is skipped without error."""
        actor = _make_actor()
        actor._parent = None
        mock_svc = MagicMock()
        mock_svc.embed.return_value = [[0.1], [0.2]]
        actor._embedding_svc = mock_svc

        with patch.object(actor, "stop"):
            # Should not raise
            actor.receiveMsg_EmbeddingRequest(_make_request())

    def test_no_parent_on_error_path(self) -> None:
        """When _parent is None, _send_error logs and returns without error."""
        actor = _make_actor()
        actor._parent = None

        with patch.object(actor, "stop"):
            # _send_error should not raise
            actor._send_error(_make_request(), "test error")


# ---------------------------------------------------------------------------
# Message model construction
# ---------------------------------------------------------------------------


class TestMessageModels:
    """Validate EmbeddingRequest/Result/Error construction and serialisation."""

    def test_embedding_request_defaults(self) -> None:
        """EmbeddingRequest has sensible defaults."""
        req = EmbeddingRequest(
            collection="c1",
            entries=[{"ref_type": "t", "ref_id": "1", "text": "hi"}],
        )
        assert req.collection == "c1"
        assert req.request_id  # auto-generated UUID
        assert req.embedding_model == "text-embedding-3-small"

    def test_embedding_request_round_trip(self) -> None:
        """EmbeddingRequest round-trips through serialisation."""
        req = _make_request()
        data = req.model_dump()
        restored = EmbeddingRequest.model_validate(data)
        assert restored.collection == req.collection
        assert restored.entries == req.entries

    def test_embedding_error_round_trip(self) -> None:
        """EmbeddingError round-trips through serialisation."""
        err = EmbeddingError(
            collection="c1", error="test error", request_id="r1"
        )
        data = err.model_dump()
        restored = EmbeddingError.model_validate(data)
        assert restored.error == "test error"
