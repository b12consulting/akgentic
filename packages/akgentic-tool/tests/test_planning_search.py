"""Tests for PlanActor.search_planning and PlanningTool._search_planning_factory.

Covers AC#2-#12 from Story 4.3: search_planning Filtered Search Capability.
Updated for Story 10-14: search_planning now returns list[str] with score labels.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from akgentic.tool.planning.planning_actor import (
    PlanActor,
    PlanConfig,
    Task,
)
from tests.conftest import MockActorAddress


def _extract_ids(results: list[str]) -> set[int]:
    """Extract task IDs from formatted result strings (``Task {id}: ...``)."""
    ids: set[int] = set()
    for line in results:
        m = re.match(r"Task (\d+):", line)
        if m:
            ids.add(int(m.group(1)))
    return ids


def _extract_id(result: str) -> int:
    """Extract task ID from a single formatted result string."""
    m = re.match(r"Task (\d+):", result)
    assert m, f"Could not extract task ID from: {result}"
    return int(m.group(1))


def _make_actor(vector_store: bool = False) -> PlanActor:
    """Construct a bare PlanActor with no Pykka runtime -- calls on_start directly."""
    actor = PlanActor()
    actor.config = PlanConfig(
        name="test-plan",
        role="ToolActor",
        vector_store=vector_store,
    )
    actor.on_start()
    return actor


def _add_task(
    actor: PlanActor,
    task_id: int,
    description: str,
    status: str = "pending",
    owner: str = "@Alice",
    creator: str = "@Bob",
) -> Task:
    """Helper: add a task to actor state without triggering embedding.

    Directly appends to task_list so tests can control the lifecycle independently.
    """
    task = Task(
        id=task_id,
        status=status,  # type: ignore[arg-type]
        description=description,
        owner=owner,
        creator=creator,
    )
    actor.state.task_list.append(task)
    return task


# ---------------------------------------------------------------------------
# AC#8 -- all params None -> full list returned
# ---------------------------------------------------------------------------


class TestSearchPlanningAllNone:
    """AC8: When all parameters are None, the full task list is returned."""

    def test_returns_full_list_when_all_none(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Setup database", status="pending")
        _add_task(actor, 2, "Auth module", status="started")
        _add_task(actor, 3, "Deployment pipeline", status="completed")

        result = actor.search_planning()

        assert len(result) == 3
        assert _extract_ids(result) == {1, 2, 3}

    def test_returns_empty_list_when_no_tasks_and_all_none(self) -> None:
        actor = _make_actor(vector_store=False)

        result = actor.search_planning()

        assert result == []


# ---------------------------------------------------------------------------
# AC#8 (edge case) -- empty task list
# ---------------------------------------------------------------------------


class TestSearchPlanningEmptyList:
    """Empty task list -> empty result for any filter."""

    def test_empty_list_with_status_filter(self) -> None:
        actor = _make_actor(vector_store=False)

        result = actor.search_planning(status="pending")

        assert result == []

    def test_empty_list_with_owner_filter(self) -> None:
        actor = _make_actor(vector_store=False)

        result = actor.search_planning(owner="@Alice")

        assert result == []

    def test_empty_list_with_query_filter(self) -> None:
        actor = _make_actor(vector_store=False)

        result = actor.search_planning(query="auth")

        assert result == []


# ---------------------------------------------------------------------------
# AC#2 -- status filter
# ---------------------------------------------------------------------------


class TestSearchPlanningStatusFilter:
    """AC2: status filter returns only tasks with exact matching status."""

    def test_status_pending_returns_only_pending(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", status="pending")
        _add_task(actor, 2, "Task B", status="started")
        _add_task(actor, 3, "Task C", status="pending")
        _add_task(actor, 4, "Task D", status="completed")

        result = actor.search_planning(status="pending")

        assert len(result) == 2
        assert all("[pending]" in line for line in result)
        assert _extract_ids(result) == {1, 3}

    def test_status_started_returns_only_started(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", status="pending")
        _add_task(actor, 2, "Task B", status="started")
        _add_task(actor, 3, "Task C", status="started")

        result = actor.search_planning(status="started")

        assert len(result) == 2
        assert all("[started]" in line for line in result)
        assert _extract_ids(result) == {2, 3}

    def test_status_filter_returns_empty_when_no_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", status="pending")

        result = actor.search_planning(status="completed")

        assert result == []


# ---------------------------------------------------------------------------
# AC#3 -- owner filter
# ---------------------------------------------------------------------------


class TestSearchPlanningOwnerFilter:
    """AC3: owner filter returns only tasks with exact owner match."""

    def test_owner_exact_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", owner="@Alice")
        _add_task(actor, 2, "Task B", owner="@Bob")
        _add_task(actor, 3, "Task C", owner="@Alice")

        result = actor.search_planning(owner="@Alice")

        assert len(result) == 2
        assert all("Owner: @Alice" in line for line in result)
        assert _extract_ids(result) == {1, 3}

    def test_owner_empty_string_matches_unassigned(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", owner="")
        _add_task(actor, 2, "Task B", owner="@Alice")
        _add_task(actor, 3, "Task C", owner="")

        result = actor.search_planning(owner="")

        assert len(result) == 2
        assert _extract_ids(result) == {1, 3}

    def test_owner_filter_returns_empty_when_no_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", owner="@Alice")

        result = actor.search_planning(owner="@Charlie")

        assert result == []


# ---------------------------------------------------------------------------
# AC#4 -- creator filter
# ---------------------------------------------------------------------------


class TestSearchPlanningCreatorFilter:
    """AC4: creator filter returns only tasks with exact creator match."""

    def test_creator_exact_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", creator="@Bob")
        _add_task(actor, 2, "Task B", creator="@Charlie")
        _add_task(actor, 3, "Task C", creator="@Bob")

        result = actor.search_planning(creator="@Bob")

        assert len(result) == 2
        assert all("Creator: @Bob" in line for line in result)
        assert _extract_ids(result) == {1, 3}

    def test_creator_filter_returns_empty_when_no_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", creator="@Bob")

        result = actor.search_planning(creator="@Alice")

        assert result == []


# ---------------------------------------------------------------------------
# AC#5 -- query keyword filter (no vector deps)
# ---------------------------------------------------------------------------


class TestSearchPlanningQueryKeyword:
    """AC5: query keyword filter -- case-insensitive substring on task.description."""

    def test_keyword_case_insensitive_match(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Authentication module setup")
        _add_task(actor, 2, "Database schema migration")
        _add_task(actor, 3, "OAuth authentication integration")

        result = actor.search_planning(query="authentication")

        assert len(result) == 2
        assert _extract_ids(result) == {1, 3}

    def test_keyword_uppercase_query_matches_lowercase_description(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow setup")
        _add_task(actor, 2, "payment service")

        result = actor.search_planning(query="AUTH")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_keyword_no_match_returns_empty(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Database setup")

        result = actor.search_planning(query="authentication")

        assert result == []

    def test_keyword_with_vector_store_disabled(self) -> None:
        """Keyword-only mode when vector_store=False -- _vs_proxy is None."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth flow")
        _add_task(actor, 2, "payment gateway")

        # _vs_proxy is None because vector_store=False -- keyword-only path.
        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1


# ---------------------------------------------------------------------------
# AC#6 -- query semantic filter with vector deps
# ---------------------------------------------------------------------------


class TestSearchPlanningQuerySemantic:
    """AC6: query with mocked VectorStoreActor proxy -- union of keyword + semantic."""

    def _make_vs_proxy_mock(
        self, embed_return: list[list[float]], search_hits: list[tuple[str, float]]
    ) -> MagicMock:
        """Build a mock VectorStoreActor proxy with embed and search stubs."""
        from akgentic.tool.vector_store.protocol import (
            CollectionStatus,
            SearchHit,
            SearchResult,
        )

        mock = MagicMock()
        mock.embed.return_value = embed_return
        mock.search.return_value = SearchResult(
            hits=[
                SearchHit(ref_type="task", ref_id=ref_id, text="", score=score)
                for ref_id, score in search_hits
            ],
            status=CollectionStatus.READY,
        )
        return mock

    def test_semantic_union_with_keyword(self) -> None:
        """Semantic hits union with keyword hits; tasks with score >= 0.5 included."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")  # keyword match
        _add_task(actor, 2, "database schema")  # semantic match only
        _add_task(actor, 3, "deployment pipeline")  # no match

        actor._vs_proxy = self._make_vs_proxy_mock(
            embed_return=[[0.1, 0.2, 0.3]],
            search_hits=[("2", 0.75), ("3", 0.3)],
        )

        result = actor.search_planning(query="auth flow")

        assert _extract_ids(result) == {1, 2}

    def test_deduplication_keyword_and_semantic_overlap(self) -> None:
        """Tasks matched by both keyword and semantic are deduplicated."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth module")

        actor._vs_proxy = self._make_vs_proxy_mock(
            embed_return=[[0.1, 0.2, 0.3]],
            search_hits=[("1", 0.9)],
        )

        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_cosine_threshold_exactly_05_included(self) -> None:
        """Tasks with cosine score exactly 0.5 are included."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = self._make_vs_proxy_mock(
            embed_return=[[0.1, 0.2]],
            search_hits=[("1", 0.5)],
        )

        result = actor.search_planning(query="db")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_cosine_threshold_below_05_excluded(self) -> None:
        """Tasks with cosine score < 0.5 are excluded (if not keyword match)."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "database task")

        actor._vs_proxy = self._make_vs_proxy_mock(
            embed_return=[[0.1, 0.2]],
            search_hits=[("1", 0.49)],
        )

        result = actor.search_planning(query="completely different topic")

        assert result == []

    def test_empty_embed_result_falls_back_to_keyword(self) -> None:
        """When embed returns empty list, semantic phase is skipped."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth flow setup")

        actor._vs_proxy = self._make_vs_proxy_mock(
            embed_return=[],
            search_hits=[],
        )

        result = actor.search_planning(query="auth")

        actor._vs_proxy.search.assert_not_called()
        assert len(result) == 1
        assert _extract_id(result[0]) == 1


# ---------------------------------------------------------------------------
# AC#7 -- combined filters (AND logic)
# ---------------------------------------------------------------------------


class TestSearchPlanningCombinedFilters:
    """AC7: multiple filters combined as AND conditions."""

    def test_status_and_owner_filter(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", status="started", owner="@Alice")
        _add_task(actor, 2, "Task B", status="pending", owner="@Alice")
        _add_task(actor, 3, "Task C", status="started", owner="@Bob")
        _add_task(actor, 4, "Task D", status="started", owner="@Alice")

        result = actor.search_planning(status="started", owner="@Alice")

        assert len(result) == 2
        assert _extract_ids(result) == {1, 4}

    def test_status_owner_query_triple_filter(self) -> None:
        """AC7: status + owner + query together (AND logic)."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "database migration", status="started", owner="@Alice")
        _add_task(actor, 2, "database backup", status="pending", owner="@Alice")
        _add_task(actor, 3, "auth setup", status="started", owner="@Alice")
        _add_task(actor, 4, "database schema", status="started", owner="@Bob")

        result = actor.search_planning(status="started", owner="@Alice", query="database")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_creator_and_status_filter(self) -> None:
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "Task A", status="pending", creator="@Bob")
        _add_task(actor, 2, "Task B", status="completed", creator="@Bob")
        _add_task(actor, 3, "Task C", status="pending", creator="@Charlie")

        result = actor.search_planning(status="pending", creator="@Bob")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1


# ---------------------------------------------------------------------------
# AC#5/AC#6 -- vector fallback when svc unavailable
# ---------------------------------------------------------------------------


class TestSearchPlanningVectorFallback:
    """AC5/AC6: when _vs_proxy is None or raises, degrades to keyword-only."""

    def test_no_exception_when_vs_proxy_is_none(self) -> None:
        """When VectorStoreActor proxy unavailable, search_planning works via keyword only."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth service")
        _add_task(actor, 2, "payment service")

        assert actor._vs_proxy is None
        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_no_exception_when_vector_store_disabled(self) -> None:
        """When vector_store=False, _vs_proxy is None and keyword fallback works."""
        actor = _make_actor(vector_store=False)
        _add_task(actor, 1, "auth service")

        assert actor._vs_proxy is None
        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1

    def test_semantic_exception_falls_back_to_keyword(self) -> None:
        """When VectorStoreActor proxy raises an exception, keyword results still returned."""
        actor = _make_actor(vector_store=True)
        _add_task(actor, 1, "auth service")

        mock_proxy = MagicMock()
        mock_proxy.embed.side_effect = RuntimeError("embedding API error")
        actor._vs_proxy = mock_proxy

        result = actor.search_planning(query="auth")

        assert len(result) == 1
        assert _extract_id(result[0]) == 1


# ---------------------------------------------------------------------------
# AC#1 -- SearchPlanning param class structure
# ---------------------------------------------------------------------------


class TestSearchPlanningParamClass:
    """AC1: SearchPlanning has correct expose and only inherited fields."""

    def test_expose_contains_tool_call_and_command(self) -> None:
        from akgentic.tool.core import COMMAND, TOOL_CALL
        from akgentic.tool.planning.planning import SearchPlanning

        sp = SearchPlanning()
        assert TOOL_CALL in sp.expose
        assert COMMAND in sp.expose

    def test_only_inherited_fields(self) -> None:
        from akgentic.tool.planning.planning import SearchPlanning

        field_names = set(SearchPlanning.model_fields.keys())
        assert field_names == {"expose", "instructions"}


# ---------------------------------------------------------------------------
# AC#9 / AC#10 -- PlanningTool.get_tools() / get_commands() include search_planning
# ---------------------------------------------------------------------------


class TestSearchPlanningWiring:
    """AC9 / AC10: PlanningTool wires search_planning into get_tools and get_commands."""

    def _make_wired_tool(self) -> object:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        mock_observer = MagicMock()
        mock_observer.myAddress = MockActorAddress()
        tool._observer = mock_observer
        tool._planning_proxy = MagicMock()
        return tool

    def test_get_tools_includes_search_planning(self) -> None:
        tool = self._make_wired_tool()
        tools = tool.get_tools()
        tool_names = [fn.__name__ for fn in tools]
        assert "search_planning" in tool_names

    def test_get_commands_includes_search_planning(self) -> None:
        from akgentic.tool.planning.planning import SearchPlanning

        tool = self._make_wired_tool()
        commands = tool.get_commands()
        assert SearchPlanning in commands

    def test_get_tools_excludes_search_planning_when_disabled(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(search_planning=False)
        mock_observer = MagicMock()
        mock_observer.myAddress = MockActorAddress()
        tool._observer = mock_observer
        tool._planning_proxy = MagicMock()

        tools = tool.get_tools()
        tool_names = [fn.__name__ for fn in tools]
        assert "search_planning" not in tool_names

    def test_get_commands_excludes_search_planning_when_disabled(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool, SearchPlanning

        tool = PlanningTool(search_planning=False)
        mock_observer = MagicMock()
        mock_observer.myAddress = MockActorAddress()
        tool._observer = mock_observer
        tool._planning_proxy = MagicMock()

        commands = tool.get_commands()
        assert SearchPlanning not in commands
