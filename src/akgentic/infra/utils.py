"""Typed ``app.state`` access via a ``StateKey[T]`` handle (ADR-030).

``app.state`` is a ``starlette.datastructures.State`` whose ``__getattr__`` is
deliberately dynamic and typed ``Any``, so every attribute read decays to
``Any`` and consumers paper over it with ``cast(...)``. A :class:`StateKey` is a
serialization-free, stateless handle to one slot: it pins the slot's *name*, its
*type* (via the generic parameter ``T``), its *default*, and whether it is
*required*. Producers ``set`` through it; consumers ``get``/``require`` through
it and receive a typed object — no ``cast``, no string literal at the call site.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi import FastAPI, Request, WebSocket
from starlette.datastructures import State

T = TypeVar("T")

# Typed ``Any`` so ``getattr(state, name, _MISSING)`` stays ``Any`` (matching
# ``State.__getattr__``), keeping ``get``'s final ``return value`` a documented
# ``no-any-return`` rather than widening the read to ``object``.
_MISSING: Any = object()


class StateKey(Generic[T]):  # noqa: UP046  # ADR-030 pins the classic Generic[T] form
    """Typed handle to one slot in ``app.state`` (ADR-030)."""

    __slots__ = ("name", "default", "required")

    def __init__(self, name: str, *, default: T | None = None, required: bool = False) -> None:
        self.name = name
        self.default = default
        self.required = required

    def set(self, source: FastAPI | Request | WebSocket, value: T) -> None:
        """Write ``value`` into this key's ``app.state`` slot (producer side).

        The only writer of the slot — :meth:`get`/:meth:`require` rely on this
        invariant for their typed reads. ``source`` may be the ``FastAPI`` app
        or any ``Request``/``WebSocket`` that reaches it.
        """
        setattr(self._state(source), self.name, value)

    def get(self, source: FastAPI | Request | WebSocket) -> T | None:
        """Read this key's slot, typed as ``T``, without a ``cast`` (consumer side).

        Returns the stored value if present. If the slot was never ``set``,
        raises ``LookupError`` when the key is ``required``, otherwise returns
        ``self.default``. A ``_MISSING`` sentinel distinguishes "never set" from
        a slot deliberately set to ``None``. See :meth:`require` for the strict,
        non-optional variant.
        """
        value = getattr(self._state(source), self.name, _MISSING)
        if value is _MISSING:
            if self.required:
                raise LookupError(f"app.state.{self.name} is not set")
            return self.default
        return value  # type: ignore[no-any-return]  # invariant: only set() writes this slot

    def require(self, source: FastAPI | Request | WebSocket) -> T:
        """Return the slot's value, raising ``LookupError`` if it is missing.

        Strict counterpart to :meth:`get`: it returns a non-optional ``T`` so
        callers need no null-check. Any ``None`` from ``get`` (absent slot, a
        ``None`` default, or a slot explicitly set to ``None``) is turned into
        a named ``LookupError`` so a missing dependency fails fast at the
        access site instead of crashing later as ``NoneType``.
        """
        value = self.get(source)
        if value is None:
            raise LookupError(f"app.state.{self.name} is not set")
        return value

    @staticmethod
    def _state(source: FastAPI | Request | WebSocket) -> State:
        return source.state if isinstance(source, FastAPI) else source.app.state
