"""Frontend adapter plugin system for the akgentic-infra server.

Defines the ``FrontendAdapter`` protocol and a dynamic loader utility that
resolves adapter classes by fully-qualified dotted name (FQDN) at server
start-up.  This allows external packages to supply their own route
translations and WebSocket event wrappers without modifying server code.
"""

from __future__ import annotations

import importlib
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from fastapi import FastAPI
from pydantic import BaseModel, Discriminator, Field, Tag

from akgentic.core.messages import Message

__all__ = [
    "ErrorPayload",
    "FrontendAdapter",
    "LlmContextPayload",
    "MessagePayload",
    "StatePayload",
    "ToolUpdatePayload",
    "UnknownPayload",
    "WrappedWsEvent",
    "WsEventPayload",
    "load_frontend_adapter",
]

# Keep in sync with WsEventPayload Tag annotations and payload Literal types below.
_KNOWN_TYPES = {"message", "state", "tool_update", "llm_context", "error"}


class MessagePayload(BaseModel):
    """V1 ``type: "message"`` envelope payload."""

    type: Literal["message"] = "message"
    id: str
    sender: str
    content: str
    timestamp: str
    message_type: str


class StatePayload(BaseModel):
    """V1 ``type: "state"`` envelope payload."""

    type: Literal["state"] = "state"
    agent: str
    state: dict[str, Any]
    timestamp: str


class ToolUpdatePayload(BaseModel):
    """V1 ``type: "tool_update"`` envelope payload."""

    type: Literal["tool_update"] = "tool_update"
    event: Any
    timestamp: str


class LlmContextPayload(BaseModel):
    """V1 ``type: "llm_context"`` envelope payload."""

    type: Literal["llm_context"] = "llm_context"
    context: dict[str, Any]
    timestamp: str


class ErrorPayload(BaseModel):
    """V1 ``type: "error"`` envelope payload."""

    type: Literal["error"] = "error"
    message: str
    timestamp: str


class UnknownPayload(BaseModel):
    """Catch-all payload for unrecognised event types."""

    type: str
    data: dict[str, Any] = {}


def _ws_event_discriminator(v: Any) -> str:  # noqa: ANN401
    """Route to the correct payload model based on ``type``, with fallback."""
    if isinstance(v, dict):
        t = v.get("type", "")
    else:
        t = getattr(v, "type", "")
    return t if t in _KNOWN_TYPES else "unknown"


WsEventPayload = Annotated[
    Annotated[MessagePayload, Tag("message")]
    | Annotated[StatePayload, Tag("state")]
    | Annotated[ToolUpdatePayload, Tag("tool_update")]
    | Annotated[LlmContextPayload, Tag("llm_context")]
    | Annotated[ErrorPayload, Tag("error")]
    | Annotated[UnknownPayload, Tag("unknown")],
    Discriminator(_ws_event_discriminator),
]


class WrappedWsEvent(BaseModel):
    """WebSocket event wrapped by a frontend adapter for client delivery.

    Encapsulates the adapter-transformed payload so that the return type
    crossing the adapter → WebSocket boundary is a proper Pydantic model
    rather than a raw dict.
    """

    payload: WsEventPayload = Field(
        description="Adapter-transformed event data in the frontend's expected format",
    )


@runtime_checkable
class FrontendAdapter(Protocol):
    """Protocol for frontend compatibility adapters.

    Implementations translate V2 API routes and WebSocket events into
    formats expected by a specific frontend client (e.g. Angular V1).
    """

    def register_routes(self, app: FastAPI) -> None:
        """Mount adapter-specific HTTP routes onto the FastAPI application.

        Args:
            app: The FastAPI application instance to add routes to.
        """
        ...

    def wrap_ws_event(self, event: Message) -> WrappedWsEvent:
        """Translate a message into a frontend-specific payload.

        Args:
            event: The V2 message to translate.

        Returns:
            A WrappedWsEvent containing the event in the frontend's expected format.
        """
        ...


def load_frontend_adapter(fqdn: str) -> FrontendAdapter:
    """Dynamically load a frontend adapter class by fully-qualified dotted name.

    Args:
        fqdn: Fully-qualified class name, e.g. ``"acme_corp.compat.AcmeAdapter"``.

    Returns:
        An instance of the adapter class that satisfies the ``FrontendAdapter``
        protocol.

    Raises:
        ImportError: If the module or class cannot be found.
        TypeError: If the resolved class does not implement ``FrontendAdapter``.
    """
    module_path, _, class_name = fqdn.rpartition(".")
    if not module_path:
        raise ImportError(f"Cannot load frontend adapter '{fqdn}': invalid FQDN (no module path)")

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise ImportError(f"Cannot load frontend adapter '{fqdn}': {exc}") from exc

    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ImportError(f"Cannot load frontend adapter '{fqdn}': {exc}") from exc

    try:
        instance = cls()
    except TypeError as exc:
        raise TypeError(f"Cannot instantiate frontend adapter '{fqdn}': {exc}") from exc
    if not isinstance(instance, FrontendAdapter):
        raise TypeError(f"Class '{fqdn}' does not implement FrontendAdapter protocol")

    return instance
