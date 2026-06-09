"""Behaviour tests for the non-blocking orchestrator stop (ADR-012).

Ports the deterministic reproduction from
``packages/akgentic-team/research/test_stop_during_processing_deadlock.py`` into
the CI suite (reusing its ``RecordingSubscriber`` / ``DeadlockWorker`` /
``TelemetryWorker`` / ``_build_team`` harness) and adds the behaviour matrix from
the story Testing section.

All assertions are behaviour-only — no ADR-reference-string checks (Golden
Rule #8). Uses ONLY ``akgentic-core`` primitives (no LLM / infra / TestModel).
"""

from __future__ import annotations

import gc
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Generator

import pykka
import pytest

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.messages.message import Message, UserMessage
from akgentic.core.orchestrator import Orchestrator

# Signals the moment a worker is INSIDE its message handler (actor busy).
_in_handler = threading.Event()

# How long a worker holds its handler open so the stop request arrives while busy.
_HANDLER_HOLD_S = 0.5


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Stop any leaked actors after each test so failures don't cascade."""
    yield
    pykka.ActorRegistry.stop_all()


class RecordingSubscriber:
    """Captures the orchestrator's ``on_message`` telemetry fan-out so a test can
    observe which messages it records and forwards to subscribers."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def on_message(self, msg: Message) -> None:
        self.messages.append(msg)

    # Lifecycle hooks — no-ops, present to satisfy the EventSubscriber protocol.
    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        ...

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        ...

    def on_stop(self, team_id: uuid.UUID) -> None:
        ...

    def type_counts(self) -> Counter[str]:
        return Counter(m.__class__.__name__ for m in self.messages)


class DeadlockWorker(Akgent):
    """Mid-message, re-enters the orchestrator via ``get_team()`` — the reentrant
    edge. Mirrors ``BaseAgent._build_structured_output_type()``."""

    def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress) -> None:
        _in_handler.set()
        time.sleep(_HANDLER_HOLD_S)
        # Blocking ask back to the orchestrator. With a non-blocking orchestrator
        # stop this always resolves; with the legacy blocking stop it deadlocks.
        self.get_team()


class TelemetryWorker(Akgent):
    """Mid-message, does NOT touch the orchestrator — no deadlock. Emits its final
    ``ProcessedMessage`` when the handler returns, as it is being stopped."""

    def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress) -> None:
        _in_handler.set()
        time.sleep(_HANDLER_HOLD_S)


class WedgedWorker(Akgent):
    """Never stops gracefully — overrides ``stop()`` to ignore the request so the
    orchestrator's roster never empties on its own (exercises the backstop)."""

    def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress) -> None:
        _in_handler.set()

    def stop(self) -> None:  # type: ignore[override]
        # Swallow the stop: the actor keeps running, so its StopMessage never
        # lands and get_team() never empties — only the backstop can finalize.
        return None


# ---------------------------------------------------------------------------
# Tool-actor gate harness (story 18.2)
# ---------------------------------------------------------------------------

# Records the ORDER in which tool actors are told to stop (their ``stop()`` is
# invoked). Behaviour-only: a test asserts on the sequence of tool names, never
# on any ADR-reference string (Golden Rule #8). Reset by ``_reset_tool_state``.
_tool_stop_order: list[str] = []

# Captures whether a consumer could successfully invoke its tool DURING teardown
# (after stop() was called, before the consumer's own StopMessage landed).
_tool_invoke_ok = threading.Event()


def _reset_tool_state() -> None:
    """Clear the per-test tool-gate observation state."""
    _tool_stop_order.clear()
    _tool_invoke_ok.clear()


class ToolActor(Akgent):
    """A ``#``-prefixed tool actor backing a tool that ``@`` agents call.

    Records its own name into ``_tool_stop_order`` when its ``stop`` runs, then
    stops normally so its ``StopMessage`` telemetry still flows. NOTE: the tells
    are fire-and-forget (``proxy_tell``), so this records *execution* order on each
    tool's own thread — order-independent membership is what tests assert, not the
    sequence. Exposes a trivial ``ping`` an in-flight consumer can ask to prove the
    tool actor is still live during teardown.
    """

    def ping(self) -> str:
        return self.config.name

    def stop(self) -> None:  # type: ignore[override]
        _tool_stop_order.append(self.config.name)
        super().stop()


class ToolConsumerWorker(Akgent):
    """An ``@`` consumer that, mid-handler, invokes its ``#`` tool actor.

    The tool address is published on the module-level ``_consumer_tool_ref`` holder
    before the message is sent. The consumer enters its handler (sets
    ``_in_handler``), holds it open so a stop() arrives while it is busy, then asks
    the tool — which must still be alive (it is deferred to phase 2) — and records
    success on ``_tool_invoke_ok``.
    """

    def receiveMsg_UserMessage(self, message: UserMessage, sender: ActorAddress) -> None:
        _in_handler.set()
        time.sleep(_HANDLER_HOLD_S)
        tool = _consumer_tool_ref[0]
        if tool is not None and tool.is_alive():
            result = self.proxy_ask(tool, ToolActor).ping()
            if result is not None:
                _tool_invoke_ok.set()


# Holder so a consumer can reach its tool address from inside its handler thread.
_consumer_tool_ref: list[ActorAddress | None] = [None]


def _create_tool(orch_proxy: Orchestrator, name: str) -> ActorAddress:
    """Create a ``#``-prefixed tool actor as a direct child of the orchestrator."""
    return orch_proxy.createActor(ToolActor, config=BaseConfig(name=name, role="Tool"))


def _create_consumer(orch_proxy: Orchestrator, worker_cls: type[Akgent]) -> ActorAddress:
    """Create an ``@``-prefixed non-tool consumer under the orchestrator."""
    return orch_proxy.createActor(worker_cls, config=BaseConfig(name="@Consumer", role="Worker"))


def _build_team(
    system: ActorSystem, worker_cls: type[Akgent]
) -> tuple[ActorAddress, ActorAddress, RecordingSubscriber]:
    """Start an orchestrator + one child worker under it; return (orch, child, recorder)."""
    _in_handler.clear()
    orch_addr = system.createActor(
        Orchestrator,
        config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    recorder = RecordingSubscriber()
    orch_proxy.subscribe(recorder)
    child_addr = orch_proxy.createActor(
        worker_cls, config=BaseConfig(name="@Worker", role="Worker")
    )
    assert child_addr is not None
    return orch_addr, child_addr, recorder


# ---------------------------------------------------------------------------
# Ported reproduction (the two research tests, asserting the FIXED behaviour)
# ---------------------------------------------------------------------------


def test_stop_during_processing_does_not_deadlock() -> None:
    """DeadlockWorker calls get_team() mid-message; stop() is asked at that moment.

    The returned event must be set within the test watchdog (no deadlock) and the
    team must tear down. WATCHDOG bounds the *test*; GRACE is the backstop passed
    to stop().
    """
    grace = 5.0
    watchdog = 10.0
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, DeadlockWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0), "worker never entered its handler"

    event: threading.Event = system.proxy_ask(orch_addr, Orchestrator).stop(grace)
    assert event.wait(timeout=watchdog), "orchestrator.stop() deadlocked (event never set)"
    assert not orch_addr.is_alive()


def _processed_count_after_worker_stop(system: ActorSystem) -> int:
    """Stop a TelemetryWorker while its message is in flight, then GC it, and
    return how many ProcessedMessages the (still-alive) orchestrator recorded.

    Stopping only the worker (blocking child stop) keeps the orchestrator alive
    so we can query its history. The orchestrator's GC-safe identity guard
    (ADR-012 §5) must not throw when it drains the late ProcessedMessage from the
    now-collected worker — otherwise the message is dropped (research §3.1).
    """
    orch_addr, child_addr, _ = _build_team(system, TelemetryWorker)
    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0), "worker never entered its handler"

    # Blocking stop of the worker only (no deadlock: TelemetryWorker never
    # re-enters the orchestrator). The worker emits its ProcessedMessage right as
    # it is torn down.
    system.proxy_ask(child_addr, Akgent).stop()
    del child_addr
    gc.collect()  # promptly collect the stopped worker, surfacing the §3.1 race
    time.sleep(0.3)  # let the orchestrator drain the queued ProcessedMessage

    from akgentic.core.messages.orchestrator import ProcessedMessage

    recorded = system.proxy_ask(orch_addr, Orchestrator).get_messages(
        message_type=ProcessedMessage
    )
    return len(recorded)


def test_processed_telemetry_survives_agent_stop() -> None:
    """Every in-flight message's ProcessedMessage is recorded even when the
    emitting agent is stopped + GC'd right after (GC-safe identity — research §3.1).

    Loops N=25 because the loss was a GC race; the orchestrator must record the
    ProcessedMessage on EVERY run.
    """
    runs = 25
    losses: list[int] = []
    for i in range(runs):
        system = ActorSystem()
        if _processed_count_after_worker_stop(system) < 1:
            losses.append(i)

    assert not losses, f"ProcessedMessage telemetry LOST in {len(losses)}/{runs} runs ({losses})"


# ---------------------------------------------------------------------------
# Behaviour matrix
# ---------------------------------------------------------------------------


def test_stop_returns_event_immediately() -> None:
    """stop() returns a threading.Event, unset on return, with the orchestrator
    thread free (a concurrent get_team() ask resolves before the event is set)."""
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, DeadlockWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    event = system.proxy_ask(orch_addr, Orchestrator).stop(5.0)
    assert isinstance(event, threading.Event)
    assert not event.is_set()  # returned before teardown completed

    # The orchestrator thread is free: a reentrant ask resolves while draining.
    roster = system.proxy_ask(orch_addr, Orchestrator).get_team()
    assert isinstance(roster, list)

    assert event.wait(timeout=10.0)


def test_stop_is_idempotent() -> None:
    """Two stop() calls return the SAME event; children are told once."""
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, TelemetryWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    proxy = system.proxy_ask(orch_addr, Orchestrator)
    first = proxy.stop(5.0)
    second = proxy.stop(5.0)
    assert first is second

    assert first.wait(timeout=10.0)


def test_zero_children_team_finalizes() -> None:
    """An orchestrator with no agents sets the event promptly."""
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )

    event = system.proxy_ask(orch_addr, Orchestrator).stop(5.0)
    assert event.wait(timeout=5.0)
    assert not orch_addr.is_alive()


def test_backstop_timeout_forces_finalize(caplog: pytest.LogCaptureFixture) -> None:
    """A wedged child that never stops → with a small grace_timeout the backstop
    fires → the event is set within ~grace_timeout, with a WARNING logged."""
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, WedgedWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)
    time.sleep(_HANDLER_HOLD_S + 0.1)  # let the handler finish so child is idle but alive

    grace = 1.0
    with caplog.at_level(logging.WARNING):
        event = system.proxy_ask(orch_addr, Orchestrator).stop(grace)
        # Event must NOT set before the grace period (child is wedged).
        assert not event.wait(timeout=0.3)
        # But the backstop sets it within ~grace.
        assert event.wait(timeout=grace + 5.0)

    assert any("forcing teardown" in rec.message for rec in caplog.records)


def test_get_team_safe_after_member_gc() -> None:
    """With a stopped+GC'd member recorded in messages, get_team() (driven during
    _stopping) returns the correct roster and raises no RuntimeError.

    The orchestrator's teardown completion check calls get_team() over every
    Start/Stop sender, including senders whose actor has already been collected.
    A clean event-set proves the completion check did not throw mid-teardown.
    """
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, TelemetryWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)
    time.sleep(_HANDLER_HOLD_S + 0.1)

    # Drop our reference so the member can be collected as teardown drains.
    del child_addr
    gc.collect()

    event = system.proxy_ask(orch_addr, Orchestrator).stop(5.0)
    assert event.wait(timeout=10.0)
    assert not orch_addr.is_alive()


def test_actor_address_metadata_survives_gc() -> None:
    """After the actor is collected, EVERY metadata read and serialize() return
    the construction-time snapshot with no RuntimeError (ADR-013).

    This generalises the earlier agent_id/role-only guarantee: name, team_id,
    squad_id, handle_user_message() and serialize() are now equally GC-safe, and
    is_alive() returns False instead of raising.
    """
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    addr = system.createActor(
        Akgent, config=BaseConfig(name="@Solo", role="Worker"), team_id=orch_addr.team_id
    )
    impl = ActorAddressImpl(addr._actor_ref)  # type: ignore[attr-defined]
    twin = ActorAddressImpl(addr._actor_ref)  # type: ignore[attr-defined]

    cached_id = impl.agent_id
    cached_role = impl.role
    cached_name = impl.name

    # Stop + collect the underlying actor.
    addr._actor_ref.stop(block=True)  # type: ignore[attr-defined]
    del addr
    gc.collect()

    # Cached identity survives GC.
    assert impl.agent_id == cached_id
    assert impl.role == cached_role
    assert impl == twin  # __eq__ works post-GC (reads cached agent_id)
    assert hash(impl) == hash(cached_id)

    # name / serialize() are now snapshot-backed → resilient on a collected actor.
    assert impl.name == cached_name == "@Solo"
    assert impl.is_alive() is False
    serialized = impl.serialize()
    assert serialized["name"] == "@Solo"
    assert serialized["role"] == "Worker"
    assert serialized["__actor_type__"].endswith(".Akgent")


def test_agent_stop_still_blocking() -> None:
    """A non-orchestrator Akgent.stop() returns None and blocks until children
    are down (asymmetry regression guard)."""
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    parent = orch_proxy.createActor(Akgent, config=BaseConfig(name="@Parent", role="Worker"))
    child = system.proxy_ask(parent, Akgent).createActor(
        Akgent, config=BaseConfig(name="@Child", role="Worker")
    )

    result = system.proxy_ask(parent, Akgent).stop()
    assert result is None
    # Blocking stop means the subtree is down on the next line.
    assert not parent.is_alive()
    assert not child.is_alive()


def test_stop_events_suppressed_from_subscribers_during_teardown() -> None:
    """During _stopping, StopMessages are appended to messages but NOT delivered
    to subscribers."""
    system = ActorSystem()
    orch_addr, child_addr, recorder = _build_team(system, TelemetryWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    system.proxy_ask(orch_addr, Orchestrator).stop(5.0).wait()

    # No StopMessage should have reached the subscriber (suppressed during teardown).
    assert recorder.type_counts()["StopMessage"] == 0


def test_shutdown_waits_on_orchestrator_events() -> None:
    """ActorSystem.shutdown() returns only after every orchestrator's event is set;
    no force-kill needed on the happy path."""
    system = ActorSystem()
    orch_addr, child_addr, _ = _build_team(system, TelemetryWorker)

    system.tell(child_addr, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    system.shutdown(timeout=10)

    # shutdown returned → every orchestrator finalized.
    assert not orch_addr.is_alive()
    assert len(system.orchestrators) == 0


def test_subscriber_snapshots_gcd_telemetry_sender() -> None:
    """A late ProcessedMessage whose sender has been stopped + GC'd is snapshotted
    for subscribers without crashing (the §3.1 serialize() case).

    Drives the exact production entry point — ``Orchestrator.snapshot_for_subscribers``
    → ``snapshot_addresses`` → ``ActorAddressImpl.serialize()``. Before ADR-013 the
    serialize resolved the now-collected sender and threw, dropping the message; with
    the resilient snapshot it composes from the cache, yielding a populated proxy.
    """
    from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy
    from akgentic.core.messages.orchestrator import ProcessedMessage

    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

    # Build a sender address from a short-lived worker, then stop + GC it so the
    # sender's underlying actor is gone by the time the message is snapshotted.
    worker_addr = orch_proxy.createActor(
        TelemetryWorker, config=BaseConfig(name="@Telemetry", role="Worker")
    )
    sender = ActorAddressImpl(worker_addr._actor_ref)  # type: ignore[attr-defined]
    system.proxy_ask(worker_addr, Akgent).stop()
    del worker_addr
    gc.collect()
    assert sender.is_alive() is False  # underlying actor collected

    # A late telemetry message whose sender is the now-collected worker.
    late = ProcessedMessage(sender=sender, recipient=orch_addr, message_id=uuid.uuid4())

    # The subscriber-snapshot step must NOT raise on the GC'd sender.
    snapshot = Orchestrator.snapshot_for_subscribers(late)

    # The snapshot carries a serialized proxy of the dead sender, not the live impl.
    assert isinstance(snapshot.sender, ActorAddressProxy)
    assert snapshot.sender.name == "@Telemetry"
    assert snapshot.sender.role == "Worker"
    assert snapshot.sender.serialize()["__actor_type__"].endswith(".TelemetryWorker")

    system.shutdown()


# ---------------------------------------------------------------------------
# Two-phase tool-agent stop gate (story 18.2, ADR-012 §2a)
# ---------------------------------------------------------------------------


def test_tool_actor_stopped_only_after_non_tool_agents() -> None:
    """AC3/AC5: a ``#`` tool actor is left alive + untold at stop() and is told to
    stop only after the non-tool consumer (and its subtree) has fully stopped.

    Consumer is created BEFORE the tool, so the tool is the later child; the
    consumer holds its handler open, so its StopMessage lands well after stop().
    """
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    consumer = _create_consumer(orch_proxy, TelemetryWorker)
    tool = _create_tool(orch_proxy, "#VectorStore")

    system.tell(consumer, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0), "consumer never entered its handler"

    # PHASE 1: stop() tells the non-tool consumer, defers the tool actor.
    event = orch_proxy.stop(5.0)
    # The tool actor is still alive and has NOT been told to stop yet: the
    # consumer is mid-handler and its StopMessage cannot have landed.
    assert tool.is_alive()
    assert _tool_stop_order == []

    # PHASE 2: once the consumer's handler returns and its StopMessage lands, the
    # deferred tool actor is told to stop and the team finalizes.
    assert event.wait(timeout=10.0)
    assert _tool_stop_order == ["#VectorStore"]
    assert not tool.is_alive()
    assert not orch_addr.is_alive()


def test_consumer_can_invoke_tool_during_teardown() -> None:
    """AC3: a consumer finishing its in-flight handler can still invoke its ``#``
    tool actor after stop() is called and before its own StopMessage lands."""
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    consumer = _create_consumer(orch_proxy, ToolConsumerWorker)
    tool = _create_tool(orch_proxy, "#VectorStore")
    _consumer_tool_ref[0] = tool

    system.tell(consumer, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0), "consumer never entered its handler"

    # Stop while the consumer is mid-handler; it will ask its tool before finishing.
    event = orch_proxy.stop(5.0)
    assert event.wait(timeout=10.0)

    # The mid-teardown tool invocation succeeded (no dead-actor crash): the tool
    # was still alive when the consumer called it.
    assert _tool_invoke_ok.is_set()
    assert not orch_addr.is_alive()


def test_multiple_tool_actors_all_stopped_after_non_tool_agents() -> None:
    """AC2/AC5: a team with two ``#`` tool actors tears them BOTH down — after the
    non-tool consumer has stopped — when the gate fires.

    The orchestrator *dispatches* the stop tells in reverse creation order —
    ``_pending_tool_stops`` is built from ``_live_children_reversed()`` (a pure
    list reversal), so the dispatch order is correct by construction. The order
    the tool actors then *execute* their stop is NOT asserted here: the tells are
    fire-and-forget (``proxy_tell``), so each tool runs its ``stop`` on its own
    thread and the completion order is a scheduling detail, not a contract.
    """
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    consumer = _create_consumer(orch_proxy, TelemetryWorker)
    tool_a = _create_tool(orch_proxy, "#VectorStore")  # dependency, created first
    tool_b = _create_tool(orch_proxy, "#PlanningTool")  # consumer of A, created last

    system.tell(consumer, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    event = orch_proxy.stop(5.0)
    assert event.wait(timeout=10.0)

    # Both tool actors were told to stop and are down; order-independent.
    assert set(_tool_stop_order) == {"#PlanningTool", "#VectorStore"}
    assert not tool_a.is_alive()
    assert not tool_b.is_alive()


def test_tools_only_team_stops_immediately() -> None:
    """AC4: a team whose only children are ``#`` tool actors finalizes promptly —
    phase 2 is kicked from stop() itself, with no non-tool StopMessage needed."""
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    tool_a = _create_tool(orch_proxy, "#VectorStore")
    tool_b = _create_tool(orch_proxy, "#PlanningTool")

    # No non-tool agent → no StopMessage would ever drive the gate; stop() itself
    # must kick phase 2 and tear both tools down (dispatch order is reverse
    # creation order by construction; execution/completion order is a scheduling
    # detail and is not asserted).
    event = orch_proxy.stop(5.0)
    assert event.wait(timeout=10.0)
    assert set(_tool_stop_order) == {"#PlanningTool", "#VectorStore"}
    assert not tool_a.is_alive()
    assert not tool_b.is_alive()
    assert not orch_addr.is_alive()


def test_backstop_flushes_pending_tool_actors(caplog: pytest.LogCaptureFixture) -> None:
    """AC6: a wedged non-tool agent prevents phase 2 → the backstop _force_stop
    still tells the deferred tool actors to stop before forcing teardown, with a
    WARNING logged."""
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    consumer = _create_consumer(orch_proxy, WedgedWorker)
    tool = _create_tool(orch_proxy, "#VectorStore")

    system.tell(consumer, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    grace = 1.0
    with caplog.at_level(logging.WARNING):
        event = orch_proxy.stop(grace)
        # The wedged consumer never emits its StopMessage, so phase 2 cannot fire
        # on its own; the tool stays deferred until the backstop flushes it.
        assert not event.wait(timeout=0.3)
        assert event.wait(timeout=grace + 5.0)

    # The backstop flushed the deferred tool actor (graceful tell) before forcing.
    assert _tool_stop_order == ["#VectorStore"]
    assert not tool.is_alive()
    assert any("forcing teardown" in rec.message for rec in caplog.records)


def test_completion_waits_for_whole_roster_including_tools() -> None:
    """AC7: the stop event fires only after the tool actors have ALSO stopped —
    not when only the non-tool roster has emptied."""
    _reset_tool_state()
    system = ActorSystem()
    orch_addr = system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    orch_proxy = system.proxy_ask(orch_addr, Orchestrator)
    consumer = _create_consumer(orch_proxy, TelemetryWorker)
    tool = _create_tool(orch_proxy, "#VectorStore")

    system.tell(consumer, UserMessage(content="hello"))
    assert _in_handler.wait(timeout=5.0)

    event = orch_proxy.stop(5.0)
    assert event.wait(timeout=10.0)

    # The completion event sets (in the orchestrator's on_stop) only after the
    # whole roster — tool actors included — has emptied: the tool actor was told
    # to stop and is down, and the orchestrator has finalized.
    assert _tool_stop_order == ["#VectorStore"]
    assert not tool.is_alive()
    assert not orch_addr.is_alive()
