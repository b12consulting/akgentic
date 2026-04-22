"""Tests for PlanningTool._planning_prompt_factory agent-scoped system prompt rendering.

Covers AC#1-AC#12 from story SR-3.1.
Tests call the closure directly using lightweight mock objects — no actor system required.
"""

from __future__ import annotations

from typing import Callable
from unittest.mock import MagicMock

from akgentic.tool.core import COMMAND, SYSTEM_PROMPT  # noqa: I001
from akgentic.tool.planning.planning import GetPlanning, GetPlanningTask, PlanningTool
from akgentic.tool.planning.planning_actor import Task

# --- Helpers ---


def make_task(
    *,
    id: int,
    status: str = "pending",
    description: str = "A task",
    owner: str = "",
    creator: str = "",
    output: str = "",
) -> Task:
    """Build a Task with explicit fields for concise test setup."""
    return Task(
        id=id,
        status=status,  # type: ignore[arg-type]
        description=description,
        owner=owner,
        creator=creator,
        output=output,
        dependencies=[],
    )


def make_prompt_fn(
    tasks: list[Task],
    agent_name: str = "@DevAgent",
    filter_by_agent: bool = True,
) -> Callable[[], str]:
    """Return the closure produced by _planning_prompt_factory for the given setup.

    Builds a minimal PlanningTool with mocked observer + planning proxy so that
    no actor system is needed.
    """
    tool = PlanningTool.model_construct()

    # Mock observer — only myAddress.name is accessed at factory time.
    mock_observer = MagicMock()
    mock_observer.myAddress.name = agent_name
    tool._observer = mock_observer

    # Mock planning proxy — only get_planning() is called inside the closure.
    mock_proxy = MagicMock()
    mock_proxy.get_planning.return_value = tasks
    tool._planning_proxy = mock_proxy

    params = GetPlanning(filter_by_agent=filter_by_agent)
    return tool._planning_prompt_factory(params)  # type: ignore[return-value]


# --- Tests: empty task list (AC#9) ---


class TestEmptyTaskList:
    """AC9: Empty task list returns sentinel string."""

    def test_empty_returns_no_planning_message(self) -> None:
        fn = make_prompt_fn(tasks=[])
        assert fn() == "No current team planning."

    def test_empty_with_filter_false_also_returns_sentinel(self) -> None:
        fn = make_prompt_fn(tasks=[], filter_by_agent=False)
        assert fn() == "No current team planning."


# --- Tests: team summary and owner breakdown (AC#3, #5, #6) ---


class TestOwnerBreakdownSorting:
    """AC6: Named owners sorted alphabetically; unassigned last. AC5: empty-owner -> unassigned."""

    def test_named_owners_sorted_alphabetically(self) -> None:
        """AC6: @ZAgent before @AAgent would be wrong; sorted() must fix order."""
        tasks = [
            make_task(id=1, owner="@ZAgent"),
            make_task(id=2, owner="@AAgent"),
            make_task(id=3, owner="@MAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@AAgent")
        result = fn()
        # Owner line must list them alpha
        assert "Owners: @AAgent: 1 | @MAgent: 1 | @ZAgent: 1" in result

    def test_unassigned_tasks_counted_and_placed_last(self) -> None:
        """AC5+AC6: empty owner -> unassigned in summary, always last."""
        tasks = [
            make_task(id=1, owner="@ArchAgent"),
            make_task(id=2, owner=""),  # unassigned
            make_task(id=3, owner=""),  # unassigned
        ]
        fn = make_prompt_fn(tasks, agent_name="@ArchAgent")
        result = fn()
        assert "@ArchAgent: 1" in result
        assert "unassigned: 2" in result
        # unassigned must appear after @ArchAgent in the line
        owners_line = next(
            line for line in result.splitlines() if line.startswith("Owners:")
        )
        arch_pos = owners_line.find("@ArchAgent")
        unassigned_pos = owners_line.find("unassigned")
        assert arch_pos < unassigned_pos

    def test_total_task_count_shown(self) -> None:
        """AC3: Team summary shows total count."""
        tasks = [make_task(id=i, owner="@DevAgent") for i in range(1, 10)]  # 9 tasks
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "**Team planning:** 9 tasks total" in result

    def test_singular_task_label(self) -> None:
        """Grammatical: 1 task uses 'task' not 'tasks'."""
        tasks = [make_task(id=1, owner="@DevAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "1 task total" in result
        assert "1 tasks total" not in result


# --- Tests: filter_by_agent=True (AC#3, #4, #5, #7) ---


class TestFilterByAgentTrue:
    """AC3+AC4+AC5+AC7: Filtered mode shows only own tasks."""

    def test_own_tasks_shown_others_excluded(self) -> None:
        """AC3: Only tasks where owner==agent_name are shown."""
        tasks = [
            make_task(id=1, owner="@DevAgent", description="Dev task"),
            make_task(id=2, owner="@ArchAgent", description="Arch task"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "Dev task" in result
        assert "Arch task" not in result

    def test_section_header_contains_agent_name(self) -> None:
        """AC3: Section header reads **Your tasks** (owner or creator: {agent_name})."""
        tasks = [make_task(id=1, owner="@DevAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "**Your tasks** (owner or creator: @DevAgent):" in result

    def test_created_by_you_tag_on_delegated_task(self) -> None:
        """AC1 (4.4): Delegated task (creator==agent, owner!=agent) shows no [created by you] tag."""
        tasks = [
            make_task(id=1, owner="@ArchAgent", creator="@DevAgent", description="Delegated"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "Delegated" in result
        assert "[created by you]" not in result
        assert "(Owner: @ArchAgent, Creator: @DevAgent)" in result

    def test_owned_task_has_no_created_by_tag(self) -> None:
        """AC4 inverse: When owner==agent, no [created by you] tag."""
        tasks = [
            make_task(id=1, owner="@DevAgent", creator="@DevAgent", description="Own task"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "Own task" in result
        assert "[created by you]" not in result
        assert "(Owner: @DevAgent, Creator: @DevAgent)" in result

    def test_unassigned_tasks_excluded_from_own_list(self) -> None:
        """AC5: Tasks with empty owner not in own-task section even if creator matches."""
        tasks = [
            make_task(id=1, owner="", creator="@DevAgent", description="Orphan task"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        # Must NOT appear in own-task section
        assert "Orphan task" not in result
        # But unassigned count in summary must show 1
        assert "unassigned: 1" in result

    def test_agent_with_no_tasks_shows_no_tasks_message(self) -> None:
        """AC7: When agent has no tasks, shows team summary + no-tasks message."""
        tasks = [
            make_task(id=1, owner="@ArchAgent"),
            make_task(id=2, owner="@ArchAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        # Team summary always shown
        assert "**Team planning:**" in result
        assert "Owners:" in result
        # No-tasks message
        assert "No tasks assigned to or created by @DevAgent yet." in result

    def test_nine_tasks_across_three_owners_scenario(self) -> None:
        """AC3: Full scenario from story — 9 tasks, 3 owners, DevAgent sees 4."""
        tasks = [
            make_task(id=1, owner="@ArchAgent"),
            make_task(id=2, owner="@ArchAgent"),
            make_task(id=3, owner="@DevAgent", description="Implement auth"),
            make_task(id=4, owner="@DevAgent"),
            make_task(id=5, owner="@DevAgent"),
            make_task(id=6, owner="@DevAgent"),
            make_task(id=7, owner=""),  # unassigned
            make_task(id=8, owner=""),  # unassigned
            make_task(id=9, owner=""),  # unassigned
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "**Team planning:** 9 tasks total" in result
        assert "@ArchAgent: 2 | @DevAgent: 4 | unassigned: 3" in result
        assert "**Your tasks** (owner or creator: @DevAgent):" in result
        assert "Implement auth" in result
        # Arch and unassigned tasks not in own section
        task_lines = [line for line in result.splitlines() if line.startswith("- ID")]
        assert len(task_lines) == 4


# --- Tests: filter_by_agent=False (AC#8) ---


class TestFilterByAgentFalse:
    """AC8: Full task list with owner/creator info."""

    def test_all_tasks_listed(self) -> None:
        """AC8: All tasks appear when filter_by_agent=False."""
        tasks = [
            make_task(id=1, owner="@ArchAgent", creator="@ArchAgent"),
            make_task(id=2, owner="@DevAgent", creator="@DevAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "**All tasks:**" in result
        # Both tasks shown
        task_lines = [line for line in result.splitlines() if line.startswith("- ID")]
        assert len(task_lines) == 2

    def test_owner_and_creator_included_per_task(self) -> None:
        """AC8: Each task line includes (Owner: ..., Creator: ...)."""
        tasks = [
            make_task(id=1, owner="@ArchAgent", creator="@DevAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "(Owner: @ArchAgent, Creator: @DevAgent)" in result

    def test_unassigned_owner_shown_as_unassigned(self) -> None:
        """AC8: task with empty owner shows 'unassigned' in full-list mode."""
        tasks = [
            make_task(id=1, owner="", creator="@DevAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "(Owner: unassigned, Creator: @DevAgent)" in result

    def test_no_your_tasks_header_in_full_list_mode(self) -> None:
        """AC8: **Your tasks** header must not appear in full-list mode."""
        tasks = [make_task(id=1, owner="@DevAgent", creator="@DevAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "**Your tasks**" not in result


# --- Tests: output field rendering (AC#10) ---


class TestOutputFieldRendering:
    """AC10: Tasks with non-empty output include output marker in the line."""

    def test_output_rendered_in_own_task_section(self) -> None:
        """AC10: output appears in filter_by_agent=True section."""
        tasks = [
            make_task(id=1, owner="@DevAgent", output="skeleton committed"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "— Output: skeleton committed" in result

    def test_output_rendered_in_full_list_section(self) -> None:
        """AC10: output appears in filter_by_agent=False section."""
        tasks = [
            make_task(
                id=1,
                owner="@DevAgent",
                creator="@DevAgent",
                output=".github/workflows/ci.yml",
            ),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "— Output: .github/workflows/ci.yml" in result

    def test_no_output_field_omits_output_marker(self) -> None:
        """AC10 inverse: empty output produces no output marker."""
        tasks = [make_task(id=1, owner="@DevAgent", output="")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "— Output:" not in result

    def test_output_and_created_by_tag_combined(self) -> None:
        """AC1+AC10 (4.4): Delegated task with output — output rendered, no [created by you] tag."""
        tasks = [
            make_task(
                id=7,
                owner="@ArchAgent",
                creator="@DevAgent",
                description="Review PR #42",
                output="comments posted",
            ),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "Review PR #42" in result
        assert "— Output: comments posted" in result
        assert "[created by you]" not in result
        task_line = next(line for line in result.splitlines() if "Review PR #42" in line)
        assert "— Output:" in task_line
        assert "(Owner: @ArchAgent, Creator: @DevAgent)" in task_line


# --- Tests: navigation hint (AC#3, #8) ---

_NAV_HINT = "Use get_planning_task(id) for exact ID lookup or search_planning(...) to filter tasks."


class TestNavigationHint:
    """Navigation hint must be the final line in every non-empty response."""

    def test_hint_present_filter_true(self) -> None:
        tasks = [make_task(id=1, owner="@DevAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert _NAV_HINT in result

    def test_hint_present_filter_false(self) -> None:
        tasks = [make_task(id=1, owner="@DevAgent", creator="@DevAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert _NAV_HINT in result

    def test_hint_present_agent_with_no_tasks(self) -> None:
        """AC7: Even when agent has no tasks, hint appears."""
        tasks = [make_task(id=1, owner="@ArchAgent")]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert _NAV_HINT in result

    def test_hint_absent_when_no_tasks(self) -> None:
        """AC9: Empty list returns only sentinel — no navigation hint."""
        fn = make_prompt_fn(tasks=[])
        result = fn()
        assert _NAV_HINT not in result


# --- Tests: GetPlanning model (AC#1) ---


class TestGetPlanningModel:
    """AC1: GetPlanning has filter_by_agent field with correct default."""

    def test_filter_by_agent_defaults_to_true(self) -> None:
        """AC1: Default value is True."""
        params = GetPlanning()
        assert params.filter_by_agent is True

    def test_filter_by_agent_can_be_set_false(self) -> None:
        """AC1: Can be explicitly set to False."""
        params = GetPlanning(filter_by_agent=False)
        assert params.filter_by_agent is False

    def test_expose_defaults_include_system_prompt_and_command(self) -> None:
        """GetPlanning default expose channels unchanged."""
        params = GetPlanning()
        assert SYSTEM_PROMPT in params.expose
        assert COMMAND in params.expose

    def test_filter_by_agent_field_has_description(self) -> None:
        """AC1: Field includes description text (checked via model_fields metadata)."""
        field_info = GetPlanning.model_fields.get("filter_by_agent")
        assert field_info is not None
        assert field_info.description is not None
        assert len(field_info.description) > 0


# --- Tests: PlanningTool routing methods ---


def _make_tool_with_mocks(
    tasks: list[Task],
    agent_name: str = "@DevAgent",
) -> PlanningTool:
    """Build PlanningTool with mocked internals for routing method coverage."""
    tool = PlanningTool.model_construct()
    mock_observer = MagicMock()
    mock_observer.myAddress.name = agent_name
    tool._observer = mock_observer

    mock_proxy = MagicMock()
    mock_proxy.get_planning.return_value = tasks
    tool._planning_proxy = mock_proxy

    # Set defaults for get_planning, get_planning_task, update_planning fields
    tool.get_planning = GetPlanning()
    tool.get_planning_task = GetPlanningTask()
    tool.update_planning = True

    return tool


class TestPlanningToolRoutingMethods:
    """Coverage for get_system_prompts, get_tools, get_commands routing."""

    def test_get_system_prompts_returns_callable_when_system_prompt_exposed(self) -> None:
        """get_system_prompts returns list with one callable when SYSTEM_PROMPT in expose."""
        tasks = [make_task(id=1, owner="@DevAgent")]
        tool = _make_tool_with_mocks(tasks)
        prompts = tool.get_system_prompts()
        assert len(prompts) == 1
        assert callable(prompts[0])

    def test_get_system_prompts_empty_when_not_exposed(self) -> None:
        """get_system_prompts returns empty list when SYSTEM_PROMPT not in expose."""
        tool = _make_tool_with_mocks([])
        tool.get_planning = GetPlanning(expose={COMMAND})  # no SYSTEM_PROMPT
        prompts = tool.get_system_prompts()
        assert prompts == []

    def test_get_commands_returns_get_planning_when_command_exposed(self) -> None:
        """get_commands returns GetPlanning entry when COMMAND in expose."""
        tasks = [make_task(id=1, owner="@DevAgent")]
        tool = _make_tool_with_mocks(tasks)
        commands = tool.get_commands()
        assert GetPlanning in commands
        assert callable(commands[GetPlanning])

    def test_get_commands_includes_get_planning_task(self) -> None:
        """get_commands returns GetPlanningTask entry when COMMAND in expose."""
        tool = _make_tool_with_mocks([])
        commands = tool.get_commands()
        assert GetPlanningTask in commands

    def test_get_tools_has_planning_task_tool(self) -> None:
        """Default GetPlanningTask has TOOL_CALL in expose — appears in get_tools."""
        tool = _make_tool_with_mocks([])
        tools = tool.get_tools()
        # Default GetPlanningTask has TOOL_CALL in expose -> one entry
        assert any(callable(t) for t in tools)


# --- Tests: owner/creator suffix in scoped view (AC#1, #2, #3) ---


class TestScopedViewOwnerCreatorDisplay:
    """AC1-AC3: Owner/creator suffix on task lines in filter_by_agent=True mode."""

    def test_owned_task_shows_owner_creator_suffix(self) -> None:
        """AC1: Task where owner==agent shows (Owner: ..., Creator: ...)."""
        tasks = [
            make_task(id=1, owner="@DevAgent", creator="@DevAgent", description="My task"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "(Owner: @DevAgent, Creator: @DevAgent)" in result

    def test_delegated_task_shows_owner_creator_no_tag(self) -> None:
        """AC1 (4.4): Delegated task shows (Owner:..., Creator:...) with no [created by you] tag."""
        tasks = [
            make_task(id=1, owner="@ArchAgent", creator="@DevAgent", description="Delegated"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        task_line = next(line for line in result.splitlines() if "Delegated" in line)
        assert "[created by you]" not in task_line
        assert "(Owner: @ArchAgent, Creator: @DevAgent)" in task_line

    def test_full_view_unassigned_owner_shown_as_unassigned(self) -> None:
        """AC3: Task with empty owner in full-view (filter_by_agent=False) renders 'unassigned'.

        Note: unassigned tasks are excluded from the scoped view (filter_by_agent=True) because
        the filter requires task.owner == agent_name or (task.owner and task.creator == agent_name).
        The owner_label guard in the scoped-view loop is defensive; this test covers the label via
        the full-view branch where unassigned tasks are always rendered.
        """
        tasks = [
            make_task(id=1, owner="", creator="@DevAgent"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent", filter_by_agent=False)
        result = fn()
        assert "(Owner: unassigned, Creator: @DevAgent)" in result

    def test_scoped_view_owner_created_by_other_shows_correct_suffix(self) -> None:
        """AC1 variant: Task created by a different agent but owned by the calling agent shows correct suffix."""
        tasks = [
            make_task(id=2, owner="@DevAgent", creator="@OtherAgent", description="Assigned"),
        ]
        fn = make_prompt_fn(tasks, agent_name="@DevAgent")
        result = fn()
        assert "(Owner: @DevAgent, Creator: @OtherAgent)" in result
