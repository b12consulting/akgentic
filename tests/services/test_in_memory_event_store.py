"""Conformance tests for the ``InMemoryEventStore`` test fake.

The fake in ``conftest.py`` stands in for a real ``EventStore`` across the
service suite, but nothing else calls its ``list_teams`` — so its filter
semantics have no other coverage, and mypy does not run over ``tests/``.
That combination is exactly how this fake drifted out of Protocol shape
between epic 19 and epic 26. These tests pin the semantics so the next
drift fails a test instead of going unnoticed.
"""

from __future__ import annotations

from akgentic.team.models import TeamStatus
from tests.models.conftest import make_process
from tests.services.conftest import InMemoryEventStore


class TestInMemoryEventStoreListTeams:
    """``list_teams`` on the fake must match the Protocol it stands in for."""

    def test_no_argument_call_returns_every_team_including_deleted(self) -> None:
        """No filter means no filter — ``DELETED`` is in the default result set."""
        store = InMemoryEventStore()
        running = make_process(status=TeamStatus.RUNNING)
        deleted = make_process(status=TeamStatus.DELETED)
        store.save_team(running)
        store.save_team(deleted)

        assert {p.team_id for p in store.list_teams()} == {running.team_id, deleted.team_id}

    def test_filters_by_user_id(self) -> None:
        """``user_id`` selects only that owner's snapshots."""
        store = InMemoryEventStore()
        mine = make_process(user_id="u1")
        theirs = make_process(user_id="u2")
        store.save_team(mine)
        store.save_team(theirs)

        assert {p.team_id for p in store.list_teams(user_id="u1")} == {mine.team_id}

    def test_filters_by_status(self) -> None:
        """``status`` selects only snapshots in that lifecycle state."""
        store = InMemoryEventStore()
        running = make_process(status=TeamStatus.RUNNING)
        stopped = make_process(status=TeamStatus.STOPPED)
        store.save_team(running)
        store.save_team(stopped)

        assert {p.team_id for p in store.list_teams(status=TeamStatus.RUNNING)} == {
            running.team_id
        }

    def test_both_filters_combine_with_and(self) -> None:
        """The two filters are independent and intersect.

        Unlike the real backends — whose push-downs land in stories 26.2 /
        26.3 / 26.5 — the fake's AND semantics are final here, so they are
        asserted rather than deferred.
        """
        store = InMemoryEventStore()
        wanted = make_process(user_id="u1", status=TeamStatus.RUNNING)
        wrong_status = make_process(user_id="u1", status=TeamStatus.STOPPED)
        wrong_user = make_process(user_id="u2", status=TeamStatus.RUNNING)
        for process in (wanted, wrong_status, wrong_user):
            store.save_team(process)

        result = store.list_teams(user_id="u1", status=TeamStatus.RUNNING)
        assert {p.team_id for p in result} == {wanted.team_id}

    def test_user_id_is_accepted_positionally(self) -> None:
        """The fake keeps the same appended-parameter shape as the real backends."""
        store = InMemoryEventStore()
        mine = make_process(user_id="u1")
        store.save_team(mine)
        store.save_team(make_process(user_id="u2"))

        assert {p.team_id for p in store.list_teams("u1")} == {mine.team_id}
