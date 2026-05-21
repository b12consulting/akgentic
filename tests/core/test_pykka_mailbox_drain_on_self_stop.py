"""Verify Pykka's mailbox-drain guarantee on the self-stop path.

This test locks down a load-bearing assumption that the cross-package master
ADR-024 chain relies on: when a Pykka actor calls ``self.stop()`` from inside
one of its own ``receiveMsg_*`` handlers (the canonical graceful-stop shape
every ``orchestrator_proxy.stop()`` triggers), Pykka guarantees that every
queued message in the actor's mailbox is processed BEFORE ``on_stop`` fires.

If a future Pykka upgrade regresses this invariant, this test fails loudly so
the regression is caught before it silently breaks downstream subscribers
(``RedisStreamSubscriber.on_stop`` and the rest of the master ADR-024 chain)
in production — where an ``on_message`` → ``XADD`` could land *after*
``on_stop`` → ``DEL`` and resurface the very race ADR-024 eliminates.

The verification uses a minimal ``pykka.ThreadingActor`` probe, NOT
``Orchestrator`` or ``Akgent``: the test is about the Pykka invariant itself,
not about ``akgentic-core`` semantics.

Cross-references:
    - ``Orchestrator`` class docstring ("Load-bearing assumption" section) in
      ``packages/akgentic-core/src/akgentic/core/orchestrator.py``
    - Master cross-package ADR-024 (akgentic-infra) and package-local ADR-011
      (akgentic-core).
    - ``Akgent.receiveMsg_StopRecursively`` — the canonical self-stop shape
      mirrored here (``self.stop()`` from inside a ``receiveMsg_*`` handler).
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from typing import Any

import pykka
import pytest

# Number of follow-up messages the trigger handler enqueues to itself before
# calling self.stop(). The expected total is N_FOLLOW_UPS + 1 message entries
# (the initial "trigger" plus N follow-ups), then exactly one ("on_stop",).
N_FOLLOW_UPS = 5


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Stop any leaked probe actors after each test so failures don't cascade."""
    yield
    pykka.ActorRegistry.stop_all()


class _MailboxDrainProbe(pykka.ThreadingActor):
    """Minimal Pykka actor that records each receive and the on_stop call.

    On receiving the ``"trigger"`` message, the handler enqueues
    ``N_FOLLOW_UPS`` additional messages to itself via ``self.actor_ref.tell``
    and then calls ``self.stop()`` from inside the same handler. This mirrors
    ``Akgent.receiveMsg_StopRecursively`` → ``self.stop()`` — the canonical
    graceful-stop shape every ``orchestrator_proxy.stop()`` triggers.
    """

    def __init__(self, recorder: list[tuple[str, Any]], lock: threading.Lock) -> None:
        super().__init__()
        self.recorder = recorder
        self.lock = lock

    def on_receive(self, message: Any) -> None:
        with self.lock:
            self.recorder.append(("msg", message))
        if message == "trigger":
            # Enqueue follow-ups FIRST, then request self-stop. Pykka must
            # process the follow-ups before on_stop fires.
            for i in range(N_FOLLOW_UPS):
                self.actor_ref.tell(f"follow-up-{i}")
            self.stop()

    def on_stop(self) -> None:
        with self.lock:
            self.recorder.append(("on_stop",))


def test_pykka_drains_mailbox_before_on_stop_on_self_stop() -> None:
    """Pykka must drain the mailbox before ``on_stop`` fires on self-stop.

    The trigger handler enqueues ``N_FOLLOW_UPS`` follow-up messages and then
    calls ``self.stop()`` from inside the same handler. After the actor has
    fully stopped, the recorder must contain:

    - exactly ``N_FOLLOW_UPS + 1`` ``("msg", ...)`` entries (trigger + follow-ups),
    - exactly one ``("on_stop",)`` entry sitting AFTER every ``("msg", ...)``
      entry (index == ``N_FOLLOW_UPS + 1``),
    - no ``("msg", ...)`` entry after the ``("on_stop",)`` entry.

    If any assertion fails, Pykka's mailbox-drain invariant has regressed on
    the self-stop path, and the cross-package master ADR-024 chain
    (RedisStreamSubscriber, etc.) can no longer rely on it.
    """
    recorder: list[tuple[str, Any]] = []
    lock = threading.Lock()
    actor_ref = _MailboxDrainProbe.start(recorder, lock)
    try:
        # Send ONLY the trigger from the test thread. The handler itself
        # enqueues follow-ups and then calls ``self.stop()`` — that internal
        # ``self.stop()`` is what places ``_ActorStop`` in the mailbox AFTER
        # the follow-ups, exercising the invariant under test.
        #
        # We must NOT call ``actor_ref.stop()`` from the test here: that would
        # enqueue a second ``_ActorStop`` racing with ``tell("trigger")`` and
        # could land in the mailbox BEFORE the follow-ups, short-circuiting
        # the drain we are trying to verify.
        actor_ref.tell("trigger")

        # Wait for the actor to terminate of its own accord via the self-stop
        # path. ``actor_stopped`` is the same threading.Event the actor loop
        # sets in ``_stop`` — so this returns exactly when ``on_stop`` has
        # finished (and the on_stop entry is in the recorder).
        stopped = actor_ref.actor_stopped.wait(timeout=5)
        assert stopped, (
            "Self-stop path did not terminate within 5s — Pykka behaviour has "
            "changed unexpectedly. See ADR-024 chain."
        )
    finally:
        # Defensive: stop any remaining actors in case the assertions below
        # short-circuit before the autouse fixture runs.
        pykka.ActorRegistry.stop_all()

    # Snapshot the recorder under the lock so any in-flight handler thread
    # cannot mutate it mid-read (the actor is stopped at this point, but
    # honour the lock contract).
    with lock:
        snapshot = list(recorder)

    msg_entries = [e for e in snapshot if e[0] == "msg"]
    expected_msg_count = N_FOLLOW_UPS + 1
    assert len(msg_entries) == expected_msg_count, (
        f"Pykka mailbox-drain invariant violated — expected {expected_msg_count} "
        f"message handlers to run before on_stop, observed {len(msg_entries)}. "
        "See ADR-024 chain: RedisStreamSubscriber.on_stop and downstream "
        f"subscribers depend on this guarantee. Recorder: {snapshot!r}"
    )

    on_stop_entries = [e for e in snapshot if e[0] == "on_stop"]
    assert len(on_stop_entries) == 1, (
        f"on_stop fired {len(on_stop_entries)} times — expected exactly one. Recorder: {snapshot!r}"
    )

    on_stop_idx = snapshot.index(("on_stop",))
    assert on_stop_idx == expected_msg_count, (
        f"on_stop fired before mailbox drained — on_stop index is {on_stop_idx}, "
        f"expected {expected_msg_count}. ADR-024 chain depends on this ordering. "
        f"Recorder: {snapshot!r}"
    )

    # Belt-and-braces: nothing after on_stop.
    after_on_stop = snapshot[on_stop_idx + 1 :]
    assert after_on_stop == [], (
        f"Unexpected entries observed AFTER on_stop: {after_on_stop!r}. "
        "ADR-024 chain assumes on_stop is the terminal event. "
        f"Full recorder: {snapshot!r}"
    )
    # Confirm the prefix is the expected message sequence (trigger first, then
    # follow-ups in enqueue order — Pykka preserves FIFO per producer).
    assert snapshot[0] == ("msg", "trigger"), (
        f"First handled message should be 'trigger', got {snapshot[0]!r}"
    )
    for i in range(N_FOLLOW_UPS):
        assert snapshot[i + 1] == ("msg", f"follow-up-{i}"), (
            f"Follow-up ordering violated at index {i + 1}: {snapshot[i + 1]!r}"
        )
