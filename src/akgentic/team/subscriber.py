"""PersistenceSubscriber: EventSubscriber to EventStore bridge for live event sourcing."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import StateChangedMessage
from akgentic.core.orchestrator import EventSubscriber
from akgentic.team.models import AgentStateSnapshot, PersistedEvent
from akgentic.team.ports import EventStore

if TYPE_CHECKING:
    from akgentic.team.manager import TeamManager

logger = logging.getLogger(__name__)

class PersistenceSubscriber(EventSubscriber):
    """Bridges EventSubscriber (akgentic-core) with EventStore (akgentic-team).

    Receives all messages from the orchestrator and persists:
    1. Every message as an append-only event (for replay on resume)
    2. Agent state snapshots on StateChangedMessage (for fast restore)

    LLM conversation history is NOT persisted separately -- it is
    reconstructed from the event log during restore.

    All types used here come from akgentic-core (Message, StateChangedMessage,
    BaseState) -- no imports from akgentic-llm or akgentic-agent needed.
    """

    def __init__(
        self,
        team_id: uuid.UUID,
        event_store: EventStore,
        initial_sequence: int = 0,
    ) -> None:
        """Initialize the persistence subscriber.

        Args:
            team_id: Unique identifier of the team whose events are persisted.
            event_store: Storage backend for events and agent state snapshots.
            initial_sequence: Starting sequence number. Use 0 (default) for new
                teams. For resumed teams, pass the max existing sequence so that
                new events continue monotonically without duplicating numbers.
        """
        self._team_id = team_id
        self._event_store = event_store
        self._sequence = initial_sequence
        self._restoring = False

    def on_message(self, msg: Message) -> None:
        """Persist a message as a PersistedEvent and optionally save agent state.

        If the restoring flag is True, skips all persistence to avoid duplicate
        writes during event replay.

        Args:
            msg: The message flowing through the orchestrator.
        """
        if self._restoring:
            return

        now = datetime.now(UTC)

        if isinstance(msg, StateChangedMessage) and msg.sender is not None:
            snapshot = AgentStateSnapshot(
                team_id=self._team_id,
                agent_id=str(msg.sender.agent_id),
                name=msg.sender.name,
                state=msg.state.serializable_copy(),
                updated_at=now,
            )
            self._event_store.save_agent_state(snapshot)
        else:
            self._sequence += 1
            event = PersistedEvent(
                team_id=self._team_id,
                sequence=self._sequence,
                event=msg,
                timestamp=now,
            )
            self._event_store.save_event(event)

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        """Set the restoring flag to skip or resume persistence.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id`` — the per-team subscriber
                is bound at construction and never accepts traffic from a
                foreign orchestrator.
            restoring: If True, on_message will skip all persistence.
        """
        assert team_id == self._team_id
        self._restoring = restoring

    def on_stop(self, team_id: uuid.UUID) -> None:
        """No-op: required by EventSubscriber protocol.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id``.
        """
        assert team_id == self._team_id

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        """No-op: required by EventSubscriber protocol.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id``.
        """
        assert team_id == self._team_id


class TimerStopSubscriber(EventSubscriber):
    """``EventSubscriber`` that bridges inactivity-timer stops into ``stop_team``.

    When the orchestrator's inactivity timer fires, it calls
    ``on_stop_request()`` on each subscriber (before the actor stops).
    This subscriber offloads ``TeamManager.stop_team`` to a daemon
    thread so the full shutdown path runs: ``Process.status=STOPPED``
    is persisted and the runtime is cleaned up. Without this bridge
    only the actor tree is torn down; the team-state record remains
    ``RUNNING`` indefinitely.

    Note: ``on_stop()`` is a no-op. The trigger to ``stop_team`` lives
    earlier in ``on_stop_request()``; by the time ``on_stop()`` runs
    the daemon-thread dispatch has already happened and there is
    nothing left to clean up on this subscriber.

    Idempotent: if ``stop_team`` raises :class:`ValueError` because
    the team is already ``STOPPED`` or ``DELETED``, the error is
    swallowed and logged at DEBUG.
    """

    def __init__(self, team_manager: TeamManager, team_id: uuid.UUID) -> None:
        self._team_manager = team_manager
        self._team_id = team_id

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        """No-op: timer-stop bridging is orthogonal to replay guarding.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id``.
            restoring: Ignored — the timer-stop bridge does not care whether
                the team is replaying or live.
        """
        assert team_id == self._team_id
        del restoring

    def on_stop(self, team_id: uuid.UUID) -> None:
        """No-op: nothing to clean up after the stop completes.

        ``TimerStopSubscriber`` only acts on the inactivity-timer signal,
        which is delivered through ``on_stop_request`` — by the time
        ``on_stop`` runs, the daemon-thread ``stop_team`` dispatch has
        already happened (see ``on_stop_request``) and this subscriber
        holds no resources that need explicit teardown. See ADR-18 §6
        for the rationale.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id``.
        """
        assert team_id == self._team_id

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        """Drain the orchestrator stop into TeamManager.stop_team (async).

        The orchestrator calls this from its own
        handler on the actor thread. Calling ``TeamManager.stop_team``
        synchronously here would deadlock: ``stop_team`` → ``_teardown_team``
        issues ``proxy_ask`` on the same orchestrator, which is busy
        servicing the current message and cannot answer. The work is
        therefore offloaded to a daemon thread so the actor thread can
        return immediately and proceed to its own ``on_stop``; by the
        time the daemon's ``stop_team`` reaches ``_teardown_team``, the
        orchestrator has finished stopping and ``stop_team``'s subsequent
        state-store writes land cleanly.

        Args:
            team_id: ``team_id`` of the orchestrator dispatching this lifecycle
                event. MUST match ``self._team_id``.
        """
        assert team_id == self._team_id
        thread = threading.Thread(
            target=self._drain_to_stop_team,
            name=f"orchestrator-stop-subscriber-{self._team_id}",
            daemon=True,
        )
        thread.start()

    def _drain_to_stop_team(self) -> None:
        """Daemon-thread body: call ``stop_team`` once, swallow idempotent errors."""
        try:
            self._team_manager.stop_team(self._team_id)
        except ValueError as exc:
            logger.debug(
                "TimerStopSubscriber idempotent no-op team_id=%s err=%s",
                self._team_id,
                exc,
            )
        except Exception:
            logger.warning(
                "TimerStopSubscriber.stop_team failed team_id=%s",
                self._team_id,
                exc_info=True,
            )

    def on_message(self, msg: Message) -> None:
        """No-op: this subscriber only reacts to orchestrator stop."""
        del msg
