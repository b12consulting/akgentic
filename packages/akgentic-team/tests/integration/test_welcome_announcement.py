"""Integration tests for the welcome-message announcement (Story 20.2).

Verifies that TeamFactory.build() announces a TeamCard.welcome_message on the
team's event stream as a WelcomeMessage wrapped in a SentMessage, that the
announcement is recorded and broadcast to subscribers, that it is skipped when
no greeting is declared, and that it is replayed exactly once on resume.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.messages.message import Message, UserMessage
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.core.orchestrator import Orchestrator

from akgentic.team.factory import TeamFactory
from akgentic.team.manager import TeamManager
from akgentic.team.messages import WelcomeMessage
from akgentic.team.models import TeamCard, TeamCardMember
from tests.integration.conftest import (
    RecordingAgent,
    make_integration_agent_card,
)
from tests.services.conftest import InMemoryEventStore

_GREETING = "Welcome to the team!"


class _RecordingSubscriber:
    """EventSubscriber that records every message broadcast via on_message.

    Satisfies the EventSubscriber protocol via structural subtyping.
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def on_message(self, msg: Message) -> None:
        """Append the broadcast message to the recorded list."""
        self.messages.append(msg)

    def on_start(self) -> None:
        """No-op: not exercised by these tests."""

    def on_stop(self) -> None:
        """No-op: not exercised by these tests."""

    def on_stop_request(self) -> None:
        """No-op: not exercised by these tests."""


def _make_team_card(welcome_message: str | None) -> TeamCard:
    """Create a single-agent team card with the given welcome_message."""
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name="greeter",
            role="Greeter",
            agent_class=RecordingAgent,
        ),
    )
    return TeamCard(
        name="welcome-team",
        description="A team for welcome-message announcement tests",
        entry_point=entry,
        members=[],
        message_types=[UserMessage],
        welcome_message=welcome_message,
    )


def _wait_for(predicate: Any, timeout: float = 3.0) -> bool:
    """Poll predicate until True or timeout (announcement is fire-and-forget)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestWelcomeAnnouncement:
    """TeamFactory.build() welcome-message announcement (AC #1-#5)."""

    def test_build_announces_welcome_message(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #1, #3: build() records a SentMessage wrapping a WelcomeMessage.

        The WelcomeMessage carries content == welcome_message,
        sender == orchestrator_addr, and team_id == the team's id.
        """
        runtime = TeamFactory.build(_make_team_card(_GREETING), actor_system)
        orchestrator: Orchestrator = actor_system.proxy_ask(runtime.orchestrator_addr, Orchestrator)

        def announced() -> bool:
            return any(
                isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
                for m in orchestrator.get_messages()
            )

        assert _wait_for(announced), "WelcomeMessage SentMessage not recorded on event log"

        sent = next(
            m
            for m in orchestrator.get_messages()
            if isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
        )
        welcome = sent.message
        assert isinstance(welcome, WelcomeMessage)
        assert welcome.content == _GREETING
        assert welcome.sender == runtime.orchestrator_addr
        assert welcome.team_id == runtime.id
        assert sent.recipient == runtime.entry_addr

    def test_subscriber_receives_welcome_message(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #2: registered subscribers receive the WelcomeMessage via on_message."""
        subscriber = _RecordingSubscriber()
        runtime = TeamFactory.build(
            _make_team_card(_GREETING), actor_system, subscribers=[subscriber]
        )
        assert runtime is not None

        def broadcast() -> bool:
            return any(
                isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
                for m in subscriber.messages
            )

        assert _wait_for(broadcast), "WelcomeMessage not broadcast to subscriber"

        sent = next(
            m
            for m in subscriber.messages
            if isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
        )
        welcome = sent.message
        assert isinstance(welcome, WelcomeMessage)
        assert welcome.content == _GREETING
        assert welcome.display_type == "other"

    def test_no_announcement_when_welcome_message_none(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #4: build() announces nothing when welcome_message is None."""
        runtime = TeamFactory.build(_make_team_card(None), actor_system)
        orchestrator: Orchestrator = actor_system.proxy_ask(runtime.orchestrator_addr, Orchestrator)

        # Give any (erroneous) async announcement a chance to land.
        time.sleep(0.3)

        welcome_sents = [
            m
            for m in orchestrator.get_messages()
            if isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
        ]
        assert welcome_sents == [], "No WelcomeMessage expected when welcome_message is None"

    def test_no_announcement_when_welcome_message_empty(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #4: build() announces nothing for an empty (falsy) welcome_message."""
        runtime = TeamFactory.build(_make_team_card(""), actor_system)
        orchestrator: Orchestrator = actor_system.proxy_ask(runtime.orchestrator_addr, Orchestrator)

        time.sleep(0.3)

        welcome_sents = [
            m
            for m in orchestrator.get_messages()
            if isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
        ]
        assert welcome_sents == [], "No WelcomeMessage expected for empty welcome_message"

    def test_announcement_failure_triggers_rollback(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #5: a delivery failure in sub-step 5.b triggers the standard rollback.

        The announcement runs inside build()'s existing try block, so a failure
        from actor_system.tell tears down already-spawned actors and re-raises.
        """
        team_card = _make_team_card(_GREETING)
        original_tell = actor_system.tell
        original_create = actor_system.createActor
        spawned: list[Any] = []

        def tracking_create(*args: Any, **kwargs: Any) -> Any:
            addr = original_create(*args, **kwargs)
            if addr is not None:
                spawned.append(addr)
            return addr

        def failing_tell(addr: Any, message: Any) -> None:
            if isinstance(message, SentMessage) and isinstance(message.message, WelcomeMessage):
                raise RuntimeError("simulated announcement delivery failure")
            return original_tell(addr, message)

        actor_system.tell = failing_tell  # type: ignore[method-assign]
        actor_system.createActor = tracking_create  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError, match="simulated announcement delivery failure"):
                TeamFactory.build(team_card, actor_system)
        finally:
            actor_system.tell = original_tell  # type: ignore[method-assign]
            actor_system.createActor = original_create  # type: ignore[method-assign]

        # Rollback: every spawned actor was torn down.
        assert spawned, "No actors were spawned -- rollback assertion is vacuous"
        for addr in spawned:
            assert not addr.is_alive(), "Actor still alive after rollback"


class TestWelcomeResumeNonDuplication:
    """Resume replays the greeting exactly once and never re-announces (AC #6)."""

    def test_welcome_message_replayed_once_on_resume(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC #6: stop + resume replays the greeting once -- never twice.

        TeamRestorer replays the persisted SentMessage event; it does not call
        TeamFactory.build() and adds no welcome-message announcement of its own,
        so the greeting appears exactly once in the resumed event stream.
        """
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_team_card(_GREETING)

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        orchestrator: Orchestrator = actor_system.proxy_ask(runtime.orchestrator_addr, Orchestrator)

        def announced_once() -> bool:
            welcome = [
                m
                for m in orchestrator.get_messages()
                if isinstance(m, SentMessage) and isinstance(m.message, WelcomeMessage)
            ]
            return len(welcome) == 1

        assert _wait_for(announced_once), "WelcomeMessage not announced once on create"

        # Allow the announcement to be persisted before stopping.
        time.sleep(0.3)
        manager.stop_team(team_id)

        new_runtime = manager.resume_team(team_id)
        resumed_orchestrator: Orchestrator = actor_system.proxy_ask(
            new_runtime.orchestrator_addr, Orchestrator
        )

        welcome_after_resume = [
            m
            for m in resumed_orchestrator.get_messages()
            if isinstance(m.message if isinstance(m, SentMessage) else m, WelcomeMessage)
        ]
        assert len(welcome_after_resume) == 1, (
            f"Expected the welcome greeting exactly once after resume, "
            f"got {len(welcome_after_resume)} -- re-announcement detected"
        )
        welcome = welcome_after_resume[0].message
        assert isinstance(welcome, WelcomeMessage)
        assert welcome.content == _GREETING
