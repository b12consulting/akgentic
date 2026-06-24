"""Behaviour tests for the ``StateKey[T]`` factory (ADR-030 §Decision 1).

Tests assert behaviour only — construction, get/set/require return values, the
unset-vs-set-to-None distinction, ``LookupError`` on required/None slots, soft
defaults, and ``_state`` resolution off a ``Request``/``WebSocket``-shaped
source. The typing headline (a keyed read returns the concrete type, not
``Any``) is encoded with ``typing.assert_type`` and verified by ``mypy
--strict``, never by asserting on a source/docstring string (Golden Rule #8).
"""

from __future__ import annotations

from typing import Any, assert_type, cast
from unittest.mock import MagicMock

import pytest
from akgentic.infra.server import state_keys as server_keys
from akgentic.infra.server.deps import TierServices
from akgentic.infra.server.state_keys import (
    CHANNEL_PARSERS,
    CHANNEL_REGISTRY,
    CONNECTION_MANAGER,
    DRAINING,
    FRONTEND_ADAPTER,
    INGESTION,
    SERVICES,
    SETTINGS,
    TEAM_SERVICE,
)
from akgentic.infra.utils import StateKey
from akgentic.infra.worker import state_keys as worker_keys
from akgentic.infra.worker.deps import WorkerServices
from fastapi import FastAPI, Request


def _services_stub() -> TierServices:
    """A non-None value typed as ``TierServices`` for the require() typing check.

    The typing assertion only needs ``require`` to return a non-``None`` value of
    the declared type; a real DI container (many protocol-typed fields) is not
    required, so a typed ``MagicMock`` stands in.
    """
    return cast(TierServices, MagicMock())


class _AppStub:
    """Minimal stand-in exposing ``.app.state`` like a ``Request``/``WebSocket``.

    ``StateKey._state`` only needs ``source.app.state`` for the non-``FastAPI``
    branch; this avoids spinning up a full request/connection scope just to
    exercise that resolution path.
    """

    def __init__(self, app: FastAPI) -> None:
        self.app = app


# --- AC 14: required key raises on unset, returns value after set ----------


def test_require_raises_lookup_error_on_bare_app() -> None:
    app = FastAPI()
    with pytest.raises(LookupError):
        SERVICES.require(app)


def test_require_returns_value_after_set() -> None:
    app = FastAPI()
    sentinel = object()
    key: StateKey[object] = StateKey("services", required=True)
    key.set(app, sentinel)
    assert key.require(app) is sentinel


def test_get_on_required_unset_raises() -> None:
    # required=True makes get itself raise on an unset slot (distinct from
    # set-to-None, which get returns as the value).
    app = FastAPI()
    with pytest.raises(LookupError):
        SERVICES.get(app)


# --- AC 15: soft get returns the key default when unset --------------------


def test_soft_get_returns_none_default_when_unset() -> None:
    app = FastAPI()
    # FRONTEND_ADAPTER is the surviving soft slot — its unset read is the None
    # default (CHANNEL_PARSERS is now required; see the LookupError test below).
    assert FRONTEND_ADAPTER.get(app) is None


def test_channel_parsers_get_raises_when_unset() -> None:
    # CHANNEL_PARSERS is now required: an unset slot raises LookupError rather
    # than reading back a silent None default.
    app = FastAPI()
    with pytest.raises(LookupError):
        CHANNEL_PARSERS.get(app)


def test_draining_returns_false_default_before_startup() -> None:
    app = FastAPI()
    assert DRAINING.get(app) is False


def test_soft_get_returns_stored_value_after_set() -> None:
    app = FastAPI()
    DRAINING.set(app, value=True)
    assert DRAINING.get(app) is True


# --- AC 16: set/get round-trip and _state resolution ----------------------


def test_set_get_round_trip_on_fastapi() -> None:
    app = FastAPI()
    key: StateKey[int] = StateKey("answer")
    key.set(app, 42)
    assert key.get(app) == 42


def test_state_resolves_via_app_for_request_like_source() -> None:
    app = FastAPI()
    key: StateKey[str] = StateKey("token", required=True)
    # _state only reads source.app.state for the non-FastAPI branch; a stub with
    # an .app attribute exercises that path without a full request scope. The cast
    # narrows the static type to the accepted Request shape.
    request_like = cast(Request, _AppStub(app))
    # set/get through a Request/WebSocket-shaped source go through source.app.state,
    # which is the same State the FastAPI app exposes directly.
    key.set(request_like, "abc")
    assert key.get(request_like) == "abc"
    assert key.require(request_like) == "abc"
    # And the value is visible on the app's own .state (single underlying State).
    assert key.get(app) == "abc"


# --- AC 5/6: unset vs set-to-None distinction; require rejects None --------


def test_get_returns_none_when_slot_explicitly_set_to_none() -> None:
    app = FastAPI()
    key: StateKey[object | None] = StateKey("maybe")
    key.set(app, None)
    # Slot is set (to None), so get returns the stored value, not LookupError.
    assert key.get(app) is None


def test_require_raises_when_value_is_none() -> None:
    app = FastAPI()
    key: StateKey[object | None] = StateKey("maybe")
    key.set(app, None)
    with pytest.raises(LookupError):
        key.require(app)


def test_soft_get_returns_explicit_default_when_unset() -> None:
    app = FastAPI()
    key: StateKey[str] = StateKey("greeting", default="hi")
    assert key.get(app) == "hi"


# --- AC 2: serialization-free / stateless construction --------------------


def test_state_key_is_slotted_and_stores_only_three_attrs() -> None:
    key: StateKey[int] = StateKey("n", default=7, required=False)
    assert key.name == "n"
    assert key.default == 7
    assert key.required is False
    assert StateKey.__slots__ == ("name", "default", "required")
    # __slots__ means no per-instance __dict__ — nothing else can be stored.
    assert not hasattr(key, "__dict__")


# --- AC 17: typing headline (checked by mypy --strict, not at runtime) ------


def test_keyed_read_is_typed_not_any() -> None:
    app = FastAPI()
    # These assert_type calls are static assertions verified by mypy --strict:
    # a soft get returns the concrete ``T | None`` and require returns ``T``.
    assert_type(DRAINING.get(app), bool | None)
    DRAINING.set(app, value=False)
    assert_type(DRAINING.require(app), bool)
    SERVICES.set(app, _services_stub())
    assert_type(SERVICES.require(app), TierServices)


# --- AC 7: server-tier key declarations (name + required/default flags) -----


def test_server_keys_declare_expected_names_and_flags() -> None:
    # StateKey is invariant in T, so the heterogeneous declarations are collected
    # as StateKey[Any]; only the name/required/default fields are asserted here.
    # (key, expected slot name, expected required, expected default)
    expected: list[tuple[StateKey[Any], str, bool, object]] = [
        (SERVICES, "services", True, None),
        (TEAM_SERVICE, "team_service", True, None),
        (SETTINGS, "settings", True, None),
        (CONNECTION_MANAGER, "connection_manager", True, None),
        (CHANNEL_REGISTRY, "channel_registry", True, None),
        (CHANNEL_PARSERS, "channel_parser_registry", True, None),
        (INGESTION, "ingestion", True, None),
        (FRONTEND_ADAPTER, "frontend_adapter", False, None),
        (DRAINING, "draining", False, False),
    ]
    for key, name, required, default in expected:
        assert key.name == name
        assert key.required is required
        assert key.default == default


def test_server_keys_module_exposes_nine_state_keys() -> None:
    keys = [v for v in vars(server_keys).values() if isinstance(v, StateKey)]
    assert len(keys) == 9


# --- AC 9: worker-tier key declaration --------------------------------------


def test_worker_services_key_declaration() -> None:
    assert worker_keys.SERVICES.name == "services"
    assert worker_keys.SERVICES.required is True
    assert worker_keys.SERVICES.default is None


def test_worker_services_key_round_trip() -> None:
    app = FastAPI()
    with pytest.raises(LookupError):
        worker_keys.SERVICES.require(app)
    services = cast(WorkerServices, MagicMock())
    worker_keys.SERVICES.set(app, services)
    assert worker_keys.SERVICES.require(app) is services
