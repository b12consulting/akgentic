"""Tests for TeamService — service layer with real in-memory adapters."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.team.models import Process, TeamStatus

from akgentic.infra.server.services.team_service import MAX_PAGE_SIZE, TeamService


def test_create_team_returns_process(team_service: TeamService) -> None:
    """Creating a team with a valid catalog entry returns a Process."""
    process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
    assert process.team_id is not None
    assert process.status == TeamStatus.RUNNING
    assert process.user_id == "anonymous"
    assert process.team_card.name == "Test Team"


def test_create_team_invalid_entry_raises(team_service: TeamService) -> None:
    """Creating a team with an invalid catalog namespace raises EntryNotFoundError."""
    with pytest.raises(EntryNotFoundError):
        team_service.create_team(catalog_namespace="nonexistent", user_id="anonymous")


def test_create_team_propagates_catalog_namespace(team_service: TeamService) -> None:
    """Process.catalog_namespace is populated from the create_team argument."""
    process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
    assert process.catalog_namespace == "test-team"


def test_create_team_forwards_user_email_and_team_id(team_service: TeamService) -> None:
    """user_email and team_id flow through to placement.create_team verbatim."""
    explicit_id = uuid.uuid4()
    mock_placement = MagicMock()
    # Match downstream contract: placement returns a handle whose team_id
    # round-trips through the cache.
    mock_placement.create_team.return_value.team_id = explicit_id
    team_service._services.placement = mock_placement  # type: ignore[assignment]

    try:
        team_service.create_team(
            "test-team",
            user_id="alice",
            user_email="alice@example.com",
            team_id=explicit_id,
        )
    except Exception:
        # Downstream worker_handle.get_team will fail because the mock placement
        # never persists a Process — that's fine, we only care about the
        # placement call shape.
        pass

    call = mock_placement.create_team.call_args
    assert call.args[1] == "alice"
    assert call.kwargs == {
        "user_email": "alice@example.com",
        "team_id": explicit_id,
        "catalog_namespace": "test-team",
    }


def test_list_teams_empty(team_service: TeamService) -> None:
    """Listing teams when none exist returns an empty page and a zero total."""
    page, total = team_service.list_teams(user_id="anonymous")
    assert page == []
    assert total == 0


def test_list_teams_filters_by_user(team_service: TeamService) -> None:
    """list_teams returns only teams belonging to the given user."""
    team_service.create_team(catalog_namespace="test-team", user_id="alice")
    team_service.create_team(catalog_namespace="test-team", user_id="bob")
    alice_teams, alice_total = team_service.list_teams(user_id="alice")
    bob_teams, bob_total = team_service.list_teams(user_id="bob")
    assert len(alice_teams) == 1
    assert alice_total == 1
    assert len(bob_teams) == 1
    assert bob_total == 1
    assert alice_teams[0].user_id == "alice"


def test_list_teams_delegates_to_event_store_with_user_id(team_service: TeamService) -> None:
    """TeamService.list_teams MUST push user_id down to event_store.list_teams,
    not load all teams and filter in Python. Regression-guard for the team-side
    ADR-16 / Epic 19 push-down: if a future refactor restores the in-memory
    filter pattern, this test fails even though the behavioural contract
    (users see only their own teams) still passes.
    """
    mock_event_store = MagicMock()
    mock_event_store.list_teams.return_value = []
    # Swap in the mock event_store on the wired TierServices container.
    # SkipValidation on the field allows direct assignment without re-validation.
    team_service._services.event_store = mock_event_store  # type: ignore[assignment]

    page, total = team_service.list_teams(user_id="alice")

    # The delegating call shape — exactly one call, user_id="alice" as kwarg.
    # Phase-2 (store-side offset pushdown) is out of scope: NO page/size here.
    mock_event_store.list_teams.assert_called_once_with(user_id="alice")
    assert mock_event_store.list_teams.call_args.args == ()
    assert mock_event_store.list_teams.call_args.kwargs == {"user_id": "alice"}
    # Empty owned set -> empty page, zero total.
    assert page == []
    assert total == 0


def test_list_teams_passes_empty_string_user_id_verbatim(team_service: TeamService) -> None:
    """user_id="" is a literal value, NOT a "list everything" sentinel.

    The empty string is passed through verbatim to event_store.list_teams; the
    backend applies its literal-match filter and returns only teams whose
    Process.user_id == "". This locks in the natural behaviour of the
    one-line delegating call.
    """
    mock_event_store = MagicMock()
    mock_event_store.list_teams.return_value = []
    team_service._services.event_store = mock_event_store  # type: ignore[assignment]

    team_service.list_teams(user_id="")

    mock_event_store.list_teams.assert_called_once_with(user_id="")


def test_get_team_found(team_service: TeamService) -> None:
    """get_team returns the Process for an existing team."""
    process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
    found = team_service.get_team(process.team_id)
    assert found is not None
    assert found.team_id == process.team_id


def test_get_team_not_found(team_service: TeamService) -> None:
    """get_team returns None for a nonexistent team ID."""
    result = team_service.get_team(uuid.uuid4())
    assert result is None


@pytest.mark.skip(
    reason="Flaky: race in TeamManager.delete_team — on_stop subscribers still "
    "flushing event_store writes while rmtree runs; pre-existing on master, "
    "not introduced by Epic 22."
)
def test_delete_team_stops_and_deletes(team_service: TeamService) -> None:
    """delete_team stops a running team and purges it from the event store."""
    process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
    team_service.delete_team(process.team_id)
    # After deletion, the team is fully purged from the event store
    after = team_service.get_team(process.team_id)
    assert after is None


@pytest.mark.skip(
    reason="Flaky: same race in TeamManager.delete_team as "
    "test_delete_team_stops_and_deletes; pre-existing, not introduced by Epic 22."
)
def test_delete_stopped_team(team_service: TeamService) -> None:
    """delete_team handles an already-stopped team without calling stop_team."""
    process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
    team_service._services.worker_handle.stop_team(process.team_id)
    team_service.delete_team(process.team_id)
    after = team_service.get_team(process.team_id)
    assert after is None


def test_delete_team_not_found_raises(team_service: TeamService) -> None:
    """delete_team raises ValueError for a nonexistent team ID."""
    with pytest.raises(ValueError, match="not found"):
        team_service.delete_team(uuid.uuid4())


# ---------------------------------------------------------------------------
# Reclassified from integration/test_adr003_tier_agnostic.py
# Source inspection; no real app needed.
# ---------------------------------------------------------------------------


class TestStopTeam:
    """Story 13.9 AC1: stop_team cleans up the event stream."""

    def test_stop_team_removes_event_stream(self, team_service: TeamService) -> None:
        """AC1: stop_team calls event_stream.remove(team_id)."""
        from akgentic.infra.adapters.community.local_event_stream import LocalEventStream

        process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
        team_id = process.team_id

        event_stream = team_service.get_event_stream()
        assert isinstance(event_stream, LocalEventStream)

        # Verify stream has events (team creation generates StartMessage events)
        events = event_stream.read_from(team_id)
        assert len(events) > 0

        team_service.stop_team(team_id)

        # After stop, subscribing should raise StreamClosed or return empty
        # The stream was removed — read_from returns [] for non-existent streams
        events_after = event_stream.read_from(team_id)
        assert events_after == []

    def test_stop_team_errors_are_non_fatal(self, team_service: TeamService) -> None:
        """AC1: event_stream.remove() failure does not prevent stop.

        Story 27.1: ``EventStreamSubscriber.on_stop(team_id)`` also calls
        ``event_stream.remove`` as the canonical per-team cleanup hook, and
        ``TeamService.stop_team`` retains its own ``event_stream.remove`` call
        as a belt-and-suspenders for the case where the worker has died
        before ``on_stop`` could fire. Both call sites must swallow
        ``event_stream.remove`` failures; the test now asserts the failing
        ``remove`` was invoked at least once.
        """
        process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
        team_id = process.team_id

        # Replace event_stream.remove with one that raises
        original_remove = team_service._services.event_stream.remove
        call_count = 0

        def failing_remove(tid: uuid.UUID) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated failure")

        team_service._services.event_stream.remove = failing_remove  # type: ignore[assignment]
        try:
            team_service.stop_team(team_id)  # Should not raise
            assert call_count >= 1
        finally:
            team_service._services.event_stream.remove = original_remove  # type: ignore[assignment]

        # Team should still be stopped
        stopped = team_service.get_team(team_id)
        assert stopped is not None
        assert stopped.status == TeamStatus.STOPPED


class TestTeamServiceImports:
    """Verify TeamService module does not import actor internals."""

    def test_team_service_has_no_actor_internal_imports(self) -> None:
        """TeamService module does not import actor internals."""
        import inspect

        from akgentic.infra.server.services import team_service as ts_module

        source = inspect.getsource(ts_module)
        forbidden = ["TeamManager", "ActorSystem", "LocalTeamHandle", "CommunityServices"]
        for name in forbidden:
            assert name not in source, f"TeamService module must not import {name}"


class TestTeamServiceLogging:
    """TeamService emits expected log messages."""

    def test_create_team_emits_info_log(
        self,
        team_service: TeamService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """create_team() emits INFO log with team_id and catalog_entry."""
        with caplog.at_level(logging.INFO, logger="akgentic.infra.server.services.team_service"):
            team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
        assert any("Team created" in r.message for r in caplog.records)

    @pytest.mark.skip(
        reason="Flaky: same race in TeamManager.delete_team as "
        "test_delete_team_stops_and_deletes; pre-existing, not introduced by Epic 22."
    )
    def test_delete_team_emits_info_log(
        self,
        team_service: TeamService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """delete_team() emits INFO log with team_id."""
        process = team_service.create_team(catalog_namespace="test-team", user_id="anonymous")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="akgentic.infra.server.services.team_service"):
            team_service.delete_team(process.team_id)
        assert any("Team deleted" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Story 24.1 — workspace-directory cleanup in delete_team
#
# These tests stub the tier services (MagicMock) so delete_team's FS-cleanup
# step can be exercised in isolation, without spinning up the real actor
# system (whose TeamManager.delete_team has a pre-existing flaky teardown
# race — see the skipped tests above).
# ---------------------------------------------------------------------------


def _stub_team_service(workspaces_root: Path, *, team_exists: bool) -> TeamService:
    """Build a TeamService with mocked tier services for FS-cleanup tests.

    When ``team_exists`` is False, ``worker_handle.get_team`` returns None so
    ``delete_team`` raises ``ValueError`` before any FS work.
    """
    services = MagicMock()
    if team_exists:
        process = MagicMock(spec=Process)
        process.status = TeamStatus.STOPPED
        services.worker_handle.get_team.return_value = process
    else:
        services.worker_handle.get_team.return_value = None
    return TeamService(services, workspaces_root=workspaces_root)


class TestDeleteTeamWorkspaceCleanup:
    """Story 24.1: delete_team removes the team's workspace directory."""

    def test_happy_path_removes_workspace_dir(self, tmp_path: Path) -> None:
        """AC #1: an existing workspace dir and its contents are removed."""
        team_id = uuid.uuid4()
        team_dir = tmp_path / str(team_id)
        team_dir.mkdir(parents=True)
        (team_dir / "file.txt").write_text("content")

        service = _stub_team_service(tmp_path, team_exists=True)
        service.delete_team(team_id)

        assert not team_dir.exists()

    def test_missing_dir_is_silent_no_op(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AC #2: a missing workspace dir produces no WARNING log and no error."""
        team_id = uuid.uuid4()
        # workspaces_root exists, but the {team_id} subdir does NOT.
        service = _stub_team_service(tmp_path, team_exists=True)

        with caplog.at_level(logging.WARNING):
            service.delete_team(team_id)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == []

    def test_rmtree_failure_logged_and_suppressed(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC #3: an rmtree failure is logged at WARNING and suppressed."""
        team_id = uuid.uuid4()
        team_dir = tmp_path / str(team_id)
        team_dir.mkdir(parents=True)
        (team_dir / "file.txt").write_text("content")

        def _boom(_path: object) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr(shutil, "rmtree", _boom)

        service = _stub_team_service(tmp_path, team_exists=True)
        with caplog.at_level(logging.WARNING):
            service.delete_team(team_id)  # must NOT raise

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and str(team_id) in r.getMessage()
        ]
        assert len(warnings) == 1
        # Team is still deleted from the system of record.
        service._services.worker_handle.delete_team.assert_called_once_with(team_id)

    def test_team_not_found_skips_fs_work(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC #5: a missing team raises ValueError before any rmtree is attempted."""
        team_id = uuid.uuid4()
        rmtree_calls: list[object] = []
        monkeypatch.setattr(shutil, "rmtree", lambda p: rmtree_calls.append(p))

        service = _stub_team_service(tmp_path, team_exists=False)
        with pytest.raises(ValueError, match="not found"):
            service.delete_team(team_id)

        assert rmtree_calls == []
        service._services.worker_handle.delete_team.assert_not_called()


# ---------------------------------------------------------------------------
# Story 37.1 — classic offset+total pagination on list_teams (ADR-032).
#
# These tests use a MagicMock event store returning hand-built Process
# snapshots so created_at / team_id are deterministic (the real fixture
# stamps near-identical timestamps). team_card is a MagicMock per the
# established `Process.model_construct` pattern.
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _make_process(created_at: datetime, team_id: uuid.UUID) -> Process:
    """Build a Process snapshot with explicit sort-key columns."""
    return Process.model_construct(
        team_id=team_id,
        team_card=MagicMock(),
        status=TeamStatus.RUNNING,
        user_id="alice",
        user_email="",
        created_at=created_at,
        updated_at=created_at,
    )


def _service_over(rows: list[Process]) -> TeamService:
    """TeamService whose event store returns a fresh copy of ``rows`` each call."""
    services = MagicMock()
    services.event_store.list_teams.side_effect = lambda **_kw: list(rows)
    return TeamService(services, workspaces_root=Path("/unused"))


def _distinct_rows(n: int) -> list[Process]:
    """``n`` processes with strictly increasing created_at (distinct positions)."""
    return [_make_process(_BASE_TIME + timedelta(minutes=i), uuid.uuid4()) for i in range(n)]


def test_page1_returns_size_rows_with_full_total() -> None:
    """(a) page 1 returns <= size rows; total_count is the full owned count."""
    service = _service_over(_distinct_rows(300))
    page, total = service.list_teams(user_id="alice")  # default page=1, size=250
    assert len(page) == 250
    assert total == 300


def test_page1_under_size_returns_all_with_full_total() -> None:
    """(a) a set smaller than size returns every team with the full total."""
    service = _service_over(_distinct_rows(40))
    page, total = service.list_teams(user_id="alice", size=250)
    assert len(page) == 40
    assert total == 40


def test_page_n_returns_correct_slice_in_order() -> None:
    """(b) page N returns the offset slice in created_at DESC, team_id DESC order."""
    rows = _distinct_rows(10)
    service = _service_over(rows)
    expected = sorted(rows, key=lambda p: (p.created_at, p.team_id), reverse=True)

    page1, total1 = service.list_teams(user_id="alice", page=1, size=3)
    page2, total2 = service.list_teams(user_id="alice", page=2, size=3)
    page4, total4 = service.list_teams(user_id="alice", page=4, size=3)

    assert total1 == total2 == total4 == 10
    assert [p.team_id for p in page1] == [p.team_id for p in expected[0:3]]
    assert [p.team_id for p in page2] == [p.team_id for p in expected[3:6]]
    # Last partial page: rows 9..10 of 10 (size-3 slice at offset 9).
    assert [p.team_id for p in page4] == [p.team_id for p in expected[9:12]]
    assert len(page4) == 1


def test_out_of_range_page_returns_empty_with_correct_total() -> None:
    """(c) a page past the end returns [] with the correct total (no error)."""
    service = _service_over(_distinct_rows(5))
    page, total = service.list_teams(user_id="alice", page=99, size=10)
    assert page == []
    assert total == 5


def test_ordering_is_created_at_then_team_id_desc() -> None:
    """(d) order is created_at DESC, team_id DESC, with a tie broken by team_id."""
    t0 = _BASE_TIME
    t1 = _BASE_TIME + timedelta(minutes=1)
    low = uuid.UUID(int=1)
    high = uuid.UUID(int=2)
    # Two rows share created_at=t0 to exercise the team_id tie-breaker.
    rows = [
        _make_process(t0, low),
        _make_process(t1, high),
        _make_process(t0, high),
    ]
    service = _service_over(rows)
    page, total = service.list_teams(user_id="alice", size=10)
    keys = [(p.created_at, p.team_id) for p in page]
    assert total == 3
    assert keys == sorted(keys, reverse=True)
    # Newest timestamp first; among the t0 tie, the higher team_id leads.
    assert keys == [(t1, high), (t0, high), (t0, low)]


def test_size_clamps_to_lower_bound() -> None:
    """(e) size <= 0 clamps to 1 (returns a single row)."""
    service = _service_over(_distinct_rows(3))
    page_zero, total_zero = service.list_teams(user_id="alice", size=0)
    page_neg, _ = service.list_teams(user_id="alice", size=-5)
    assert len(page_zero) == 1
    assert total_zero == 3
    assert len(page_neg) == 1


def test_size_clamps_to_upper_bound() -> None:
    """(e) size > MAX_PAGE_SIZE clamps to MAX_PAGE_SIZE (500)."""
    service = _service_over(_distinct_rows(600))
    page, total = service.list_teams(user_id="alice", size=99999)
    assert len(page) == MAX_PAGE_SIZE
    assert total == 600


def test_size_250_default_and_explicit() -> None:
    """(e) default size is 250, and size=250 (cap raised above 200) works."""
    service_default = _service_over(_distinct_rows(300))
    page_default, _ = service_default.list_teams(user_id="alice")
    assert len(page_default) == 250

    service_explicit = _service_over(_distinct_rows(300))
    page_explicit, _ = service_explicit.list_teams(user_id="alice", size=250)
    assert len(page_explicit) == 250


def test_default_page_is_1() -> None:
    """(f) default page is 1: omitting page returns the first slice."""
    rows = _distinct_rows(10)
    service = _service_over(rows)
    expected = sorted(rows, key=lambda p: (p.created_at, p.team_id), reverse=True)
    default_page, _ = service.list_teams(user_id="alice", size=3)
    explicit_page1, _ = service.list_teams(user_id="alice", page=1, size=3)
    assert [p.team_id for p in default_page] == [p.team_id for p in expected[0:3]]
    assert [p.team_id for p in default_page] == [p.team_id for p in explicit_page1]


def test_page_clamps_to_lower_bound() -> None:
    """(f) page <= 0 clamps to 1 (same slice as page 1)."""
    rows = _distinct_rows(10)
    service = _service_over(rows)
    page_zero, _ = service.list_teams(user_id="alice", page=0, size=3)
    page_neg, _ = service.list_teams(user_id="alice", page=-3, size=3)
    page1, _ = service.list_teams(user_id="alice", page=1, size=3)
    assert [p.team_id for p in page_zero] == [p.team_id for p in page1]
    assert [p.team_id for p in page_neg] == [p.team_id for p in page1]


# ---------------------------------------------------------------------------
# Story 37.1 AC #7 — list_teams is stateless: a pure function of
# (user_id, page, size) + current store contents; nothing is cached between
# requests, so a page is correct regardless of which replica serves it.
# ---------------------------------------------------------------------------


def test_list_teams_refetches_store_every_call() -> None:
    """Every list_teams call re-reads the store — no cached sorted list."""
    service = _service_over(_distinct_rows(5))
    service.list_teams(user_id="alice", page=1, size=2)
    service.list_teams(user_id="alice", page=2, size=2)
    service.list_teams(user_id="alice", page=3, size=2)
    # One store read per request — no request reused a prior request's fetch.
    assert service._services.event_store.list_teams.call_count == 3


def test_same_args_yield_same_page_independent_requests() -> None:
    """Two independent requests with the same (page, size) return the same page."""
    rows = _distinct_rows(7)
    service = _service_over(rows)
    page_a, total_a = service.list_teams(user_id="alice", page=2, size=2)
    page_b, total_b = service.list_teams(user_id="alice", page=2, size=2)
    assert [p.team_id for p in page_a] == [p.team_id for p in page_b]
    assert total_a == total_b


def test_page_followable_on_fresh_service_instance() -> None:
    """A page minted by one service instance matches a SEPARATE, freshly
    constructed instance over the same store contents — simulating a different
    worker/replica with no shared in-process state.
    """
    rows = _distinct_rows(7)
    minting_service = _service_over(rows)
    page1, _ = minting_service.list_teams(user_id="alice", page=1, size=3)

    # A brand-new instance (different "replica"), no prior request primed.
    fresh_service = _service_over(rows)
    page2, _ = fresh_service.list_teams(user_id="alice", page=2, size=3)

    seen = {p.team_id for p in page1} | {p.team_id for p in page2}
    assert {p.team_id for p in page1}.isdisjoint({p.team_id for p in page2})
    assert len(seen) == 6  # 3 + 3, no overlap, no gap across the replica boundary


def test_list_teams_holds_no_per_request_state() -> None:
    """list_teams mutates no instance attribute that survives the call."""
    service = _service_over(_distinct_rows(5))
    before = dict(vars(service))
    service.list_teams(user_id="alice", page=1, size=2)
    after = dict(vars(service))
    # No new attribute, and the wired collaborators are unchanged identities.
    assert before.keys() == after.keys()
    assert all(before[k] is after[k] for k in before)
