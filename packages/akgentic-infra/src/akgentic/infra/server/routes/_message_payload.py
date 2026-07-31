"""Shared send-payload resolution for the ``/message*`` routes (server + worker).

The merged ``SendMessageRequest`` carries exactly one of a plain ``content``
string or a pre-formed typed ``Message`` wire envelope (``message``). Both the
server team routes and the worker team routes decode that envelope identically
via these helpers, so there is one decode implementation and both tiers accept
a typed ``Message`` for agent processing (ADR-22).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import deserialize_object
from akgentic.infra.server.models import SendMessageRequest


def decode_message(raw: dict[str, Any]) -> Message:
    """Reconstruct a concrete typed ``Message`` from a wire envelope.

    ``raw`` is a ``Message.model_dump(mode="json")`` dict carrying the
    ``__model__`` tag; ``deserialize_object`` reads the tag, imports the class,
    and rebuilds the exact concrete subclass.

    Raises:
        HTTPException: 400 when the payload cannot be deserialized or decodes to
            something that is not a ``Message`` — a client error.
    """
    try:
        # deserialize_object accepts the serialized dict directly and
        # reconstructs the concrete typed Message from its __model__ tag.
        message = deserialize_object(raw)
    except (ValueError, TypeError, ImportError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="invalid message payload") from exc
    if not isinstance(message, Message):
        raise HTTPException(status_code=400, detail="payload is not a Message")
    return message


def resolve_send_payload(body: SendMessageRequest) -> str | Message:
    """Select the send payload from a ``SendMessageRequest``.

    A present ``message`` selects the typed path — the wire envelope is decoded
    into the concrete ``Message`` (400 on a bad envelope) — otherwise the plain
    ``content`` string is used. ``SendMessageRequest``'s validator guarantees
    exactly one is set, so this never falls through.
    """
    if body.message is not None:
        return decode_message(body.message)
    assert body.content is not None  # guaranteed by SendMessageRequest validator
    return body.content
