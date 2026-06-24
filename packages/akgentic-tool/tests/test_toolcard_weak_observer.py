"""Behavioral tests for the base ``ToolCard`` weak-observer storage (Epic 22 / ADR-030).

The observer (owning agent) is held through a ``weakref`` so a tool, its closures,
and its command registry can never pin a stopped agent in memory. Access goes
through the ``_observer`` property, which derefs the weakref and raises
``ToolObserverGone`` once the referent is collected.
"""

import gc
import weakref
from typing import Callable

import pytest
from akgentic.tool.core import ToolCard
from akgentic.tool.errors import ToolObserverGone


class _DummyToolCard(ToolCard):
    """Minimal concrete ``ToolCard`` for exercising base observer storage."""

    name: str = "dummy"
    description: str = "dummy tool card"

    def get_tools(self) -> list[Callable]:
        return []


class _Observer:
    """Trivial weak-referenceable observer stand-in (satisfies the shape used here)."""


def test_observer_stores_a_weakref_and_does_not_pin() -> None:
    # AC6(a): observer() stores a weakref — the card does not keep the observer alive.
    card = _DummyToolCard()
    obs = _Observer()
    assert card.observer(obs) is card  # method chaining preserved

    ref = weakref.ref(obs)
    del obs
    gc.collect()
    assert ref() is None  # card did not pin it


def test_observer_property_returns_live_observer() -> None:
    # AC6(b): _observer returns the live observer while it is referenced.
    card = _DummyToolCard()
    obs = _Observer()
    card.observer(obs)
    assert card._observer is obs


def test_observer_gone_after_collection() -> None:
    # AC6(c): after drop + gc, _observer_or_none() is None and _observer raises.
    card = _DummyToolCard()
    obs = _Observer()
    card.observer(obs)
    del obs
    gc.collect()
    assert card._observer_or_none() is None
    with pytest.raises(ToolObserverGone):
        _ = card._observer


def test_observer_or_none_when_unset() -> None:
    # Covers the `_observer_ref is None` branch of _observer_or_none().
    card = _DummyToolCard()
    assert card._observer_or_none() is None
    with pytest.raises(ToolObserverGone):
        _ = card._observer


def test_observer_setter_stores_weakly() -> None:
    # LEAD DECISION (backward compatibility): `self._observer = obs` keeps working
    # and stores the observer weakly, so direct assignment never pins the agent.
    card = _DummyToolCard()
    obs = _Observer()
    card._observer = obs
    assert card._observer is obs

    ref = weakref.ref(obs)
    del obs
    gc.collect()
    assert ref() is None
    assert card._observer_or_none() is None


def test_observer_ref_excluded_from_model_dump() -> None:
    # AC6(d): _observer_ref is a PrivateAttr — excluded from serialization.
    card = _DummyToolCard()
    card.observer(_Observer())
    assert "_observer_ref" not in card.model_dump()
    assert "_observer_ref" not in card.model_dump(mode="json")
