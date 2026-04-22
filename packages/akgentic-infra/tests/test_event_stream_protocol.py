"""Tests for EventStream and StreamReader protocols (AC1, AC2, AC3, AC5, AC6).

Updated for Story 13.7: protocols typed on Message instead of PersistedEvent.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Protocol, get_type_hints

# --- StreamClosed exception (AC3) ---


def test_stream_closed_is_exception() -> None:
    """StreamClosed inherits from Exception."""
    from akgentic.infra.protocols.event_stream import StreamClosed

    assert issubclass(StreamClosed, Exception)


def test_stream_closed_has_docstring() -> None:
    """StreamClosed has a docstring explaining it is raised by read_next."""
    from akgentic.infra.protocols.event_stream import StreamClosed

    assert StreamClosed.__doc__ is not None
    assert "read_next" in StreamClosed.__doc__


# --- StreamReader protocol (AC2) ---


def test_stream_reader_is_protocol() -> None:
    """StreamReader uses typing.Protocol base."""
    from akgentic.infra.protocols.event_stream import StreamReader

    assert Protocol in inspect.getmro(StreamReader)


def test_stream_reader_has_read_next() -> None:
    """StreamReader defines read_next with timeout parameter."""
    from akgentic.infra.protocols.event_stream import StreamReader

    assert hasattr(StreamReader, "read_next")
    sig = inspect.signature(StreamReader.read_next)
    assert "timeout" in sig.parameters
    assert sig.parameters["timeout"].default == 0.5


def test_stream_reader_has_close() -> None:
    """StreamReader defines close method."""
    from akgentic.infra.protocols.event_stream import StreamReader

    assert hasattr(StreamReader, "close")
    sig = inspect.signature(StreamReader.close)
    # Only self parameter
    assert len(sig.parameters) == 1


def test_stream_reader_close_returns_none() -> None:
    """StreamReader.close returns None."""
    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import StreamReader

    hints = get_type_hints(
        StreamReader.close,
        localns={"Message": Message},
    )
    assert hints["return"] is type(None)


def test_stream_reader_is_not_runtime_checkable() -> None:
    """StreamReader does NOT have @runtime_checkable decorator."""
    from akgentic.infra.protocols.event_stream import StreamReader

    assert not getattr(StreamReader, "_is_runtime_protocol", False)


# --- EventStream protocol (AC1) ---


def test_event_stream_is_protocol() -> None:
    """EventStream uses typing.Protocol base."""
    from akgentic.infra.protocols.event_stream import EventStream

    assert Protocol in inspect.getmro(EventStream)


def test_event_stream_is_runtime_checkable() -> None:
    """EventStream has @runtime_checkable decorator and isinstance works."""
    from akgentic.infra.protocols.event_stream import EventStream

    class FakeStream:
        def append(self, team_id: uuid.UUID, event: object) -> int:
            return 0

        def read_from(self, team_id: uuid.UUID, cursor: int = 0) -> list[object]:
            return []

        def subscribe(self, team_id: uuid.UUID, cursor: int = 0) -> object:
            return None

        def remove(self, team_id: uuid.UUID) -> None:
            pass

    assert isinstance(FakeStream(), EventStream)


def test_event_stream_has_append() -> None:
    """EventStream defines append with team_id and event parameters."""
    from akgentic.infra.protocols.event_stream import EventStream

    assert hasattr(EventStream, "append")
    sig = inspect.signature(EventStream.append)
    assert "team_id" in sig.parameters
    assert "event" in sig.parameters


def test_event_stream_append_returns_int() -> None:
    """EventStream.append returns int."""
    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import EventStream

    hints = get_type_hints(
        EventStream.append,
        localns={"Message": Message},
    )
    assert hints["return"] is int


def test_event_stream_has_read_from() -> None:
    """EventStream defines read_from with team_id and cursor parameters."""
    from akgentic.infra.protocols.event_stream import EventStream

    assert hasattr(EventStream, "read_from")
    sig = inspect.signature(EventStream.read_from)
    assert "team_id" in sig.parameters
    assert "cursor" in sig.parameters
    assert sig.parameters["cursor"].default == 0


def test_event_stream_has_subscribe() -> None:
    """EventStream defines subscribe with team_id and cursor parameters."""
    from akgentic.infra.protocols.event_stream import EventStream

    assert hasattr(EventStream, "subscribe")
    sig = inspect.signature(EventStream.subscribe)
    assert "team_id" in sig.parameters
    assert "cursor" in sig.parameters
    assert sig.parameters["cursor"].default == 0


def test_event_stream_has_remove() -> None:
    """EventStream defines remove with team_id parameter."""
    from akgentic.infra.protocols.event_stream import EventStream

    assert hasattr(EventStream, "remove")
    sig = inspect.signature(EventStream.remove)
    assert "team_id" in sig.parameters


def test_event_stream_remove_returns_none() -> None:
    """EventStream.remove returns None."""
    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import EventStream

    hints = get_type_hints(
        EventStream.remove,
        localns={"Message": Message},
    )
    assert hints["return"] is type(None)


def test_event_stream_method_count() -> None:
    """EventStream has exactly 4 public methods."""
    from akgentic.infra.protocols.event_stream import EventStream

    public_methods = [
        m for m in dir(EventStream) if not m.startswith("_") and callable(getattr(EventStream, m))
    ]
    assert len(public_methods) == 4


def test_stream_reader_method_count() -> None:
    """StreamReader has exactly 2 public methods."""
    from akgentic.infra.protocols.event_stream import StreamReader

    public_methods = [
        m for m in dir(StreamReader) if not m.startswith("_") and callable(getattr(StreamReader, m))
    ]
    assert len(public_methods) == 2


# --- Type annotations use Message (Story 13.7 AC1) ---


def test_stream_reader_read_next_return_type() -> None:
    """StreamReader.read_next return annotation is Message | None."""
    import types

    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import StreamReader

    hints = get_type_hints(
        StreamReader.read_next,
        localns={"Message": Message},
    )
    ret = hints["return"]
    assert isinstance(ret, types.UnionType)
    assert Message in ret.__args__
    assert type(None) in ret.__args__


def test_event_stream_read_from_return_type() -> None:
    """EventStream.read_from return annotation is list[Message]."""
    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import EventStream

    hints = get_type_hints(
        EventStream.read_from,
        localns={"Message": Message},
    )
    ret = hints["return"]
    assert ret == list[Message]


def test_event_stream_append_event_type() -> None:
    """EventStream.append event parameter is annotated as Message."""
    from akgentic.core.messages import Message

    from akgentic.infra.protocols.event_stream import EventStream

    hints = get_type_hints(
        EventStream.append,
        localns={"Message": Message},
    )
    assert hints["event"] is Message


def test_event_stream_subscribe_returns_stream_reader() -> None:
    """EventStream.subscribe return annotation is StreamReader."""
    from akgentic.infra.protocols.event_stream import EventStream, StreamReader

    hints = get_type_hints(
        EventStream.subscribe,
        localns={"StreamReader": StreamReader},
    )
    assert hints["return"] is StreamReader


# --- NullEventStream satisfies protocol ---


def test_null_event_stream_satisfies_protocol() -> None:
    """NullEventStream satisfies EventStream @runtime_checkable check."""
    from akgentic.infra.adapters.shared.null_event_stream import NullEventStream
    from akgentic.infra.protocols.event_stream import EventStream

    assert isinstance(NullEventStream(), EventStream)


def test_null_event_stream_append_returns_zero() -> None:
    """NullEventStream.append returns 0."""
    from akgentic.infra.adapters.shared.null_event_stream import NullEventStream

    result = NullEventStream().append(uuid.uuid4(), None)  # type: ignore[arg-type]
    assert result == 0


def test_null_event_stream_read_from_returns_empty() -> None:
    """NullEventStream.read_from returns empty list."""
    from akgentic.infra.adapters.shared.null_event_stream import NullEventStream

    result = NullEventStream().read_from(uuid.uuid4())
    assert result == []


def test_null_event_stream_subscribe_returns_reader() -> None:
    """NullEventStream.subscribe returns a NullStreamReader."""
    from akgentic.infra.adapters.shared.null_event_stream import (
        NullEventStream,
        NullStreamReader,
    )

    reader = NullEventStream().subscribe(uuid.uuid4())
    assert isinstance(reader, NullStreamReader)


def test_null_stream_reader_read_next_returns_none() -> None:
    """NullStreamReader.read_next returns None."""
    from akgentic.infra.adapters.shared.null_event_stream import NullStreamReader

    assert NullStreamReader().read_next() is None


def test_null_stream_reader_close_is_noop() -> None:
    """NullStreamReader.close does not raise."""
    from akgentic.infra.adapters.shared.null_event_stream import NullStreamReader

    NullStreamReader().close()  # should not raise


def test_null_event_stream_remove_is_noop() -> None:
    """NullEventStream.remove does not raise."""
    from akgentic.infra.adapters.shared.null_event_stream import NullEventStream

    NullEventStream().remove(uuid.uuid4())  # should not raise
