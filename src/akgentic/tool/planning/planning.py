from __future__ import annotations

import logging
from typing import Callable, Literal

from pydantic import Field

from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import (
    COMMAND,
    SYSTEM_PROMPT,
    TOOL_CALL,
    BaseToolParam,
    Channels,
    ToolCard,
    _resolve,
)
from akgentic.tool.event import ActorToolObserver
from akgentic.tool.planning.planning_actor import (
    PlanActor,
    PlanConfig,
    Task,
    TaskStatus,
    UpdatePlan,
)
from akgentic.tool.vector_store.protocol import CollectionConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PLANNING_ACTOR_NAME = "#PlanningTool"
PLANNING_ACTOR_ROLE = "ToolActor"


class GetPlanning(BaseToolParam):
    """Get the full team plan — as system prompt and/or tool."""

    expose: set[Channels] = {SYSTEM_PROMPT, COMMAND}
    filter_by_agent: bool = Field(
        default=True,
        description=(
            "When True (default), the system prompt shows only tasks owned or created by the "
            "calling agent. The team summary (totals + owner breakdown) is always shown. "
            "Set False to list all tasks."
        ),
    )


class GetPlanningTask(BaseToolParam):
    """Get a single task by ID."""

    expose: set[Channels] = {TOOL_CALL, COMMAND}


class UpdatePlanning(BaseToolParam):
    """Update tasks."""


class SearchPlanning(BaseToolParam):
    """Search tasks by status, owner, creator, and/or natural-language description."""

    expose: set[Channels] = {TOOL_CALL, COMMAND}


class PlanningTool(ToolCard):
    """Team planning management via actor-based plan store.

    The ``VectorStoreActor`` singleton is owned by ``VectorStoreTool`` and
    declared as a dependency here; this tool only looks it up at actor-start
    time.
    """

    name: str = "Planning"
    description: str = "Planning tool to manage team plans and tasks"

    vector_store: bool | str = Field(
        default=True,
        description=(
            "False disables vector store wiring; True uses the default VectorStoreActor; "
            "str names a specific VectorStoreActor to look up."
        ),
    )

    collection: CollectionConfig = Field(
        default_factory=CollectionConfig,
        description=(
            "Vector collection configuration (backend, persistence, dimension, tenant). "
            "Propagated to PlanConfig and used by PlanActor._acquire_vs_proxy when calling "
            "create_collection on the VectorStoreActor."
        ),
    )

    search_top_k: int = Field(
        default=10,
        description="Default top-k for semantic search in search_planning.",
    )
    search_score_threshold: float = Field(
        default=0.5,
        description="Default minimum cosine similarity score for semantic results.",
    )

    @property
    def depends_on(self) -> list[str]:
        """Runtime dependency on VectorStoreTool, conditional on vector_store.

        When ``vector_store`` is ``False`` this tool is in degraded mode and
        does not need VectorStoreActor — the factory must not require a
        ``VectorStoreTool`` in the team config. Any other value (``True`` or a
        name ``str``) requires VectorStoreTool to be wired first so the
        PlanActor can look up the VectorStoreActor during ``on_start``.
        """
        return ["VectorStoreTool"] if self.vector_store is not False else []

    get_planning: GetPlanning | bool = Field(
        default=True, description="By default the plan in included in the system prompt"
    )
    get_planning_task: GetPlanningTask | bool = True
    update_planning: UpdatePlanning | bool = True
    search_planning: SearchPlanning | bool = True

    def observer(self, observer: ActorToolObserver) -> None:  # type: ignore[override]
        """Attach observer and set up the planning actor proxy.

        Assumes ``VectorStoreTool.observer()`` has already created the
        ``VectorStoreActor`` singleton (ordering enforced by
        ``ToolFactory`` topological sort via ``depends_on``). The
        ``PlanActor`` looks that actor up by name during its own ``on_start``.

        Requires an ActorToolObserver for actor system access.
        """
        self._observer = observer
        if observer.orchestrator is None:
            raise ValueError("PlanningTool requires access to the orchestrator.")

        orchestrator_proxy = observer.proxy_ask(observer.orchestrator, Orchestrator)

        # Create/retrieve PlanActor singleton. VectorStoreActor creation is owned
        # by VectorStoreTool (depends_on enforces ordering).
        planning_tool_addr = orchestrator_proxy.getChildrenOrCreate(
            PlanActor,
            config=PlanConfig(
                name=PLANNING_ACTOR_NAME,
                role=PLANNING_ACTOR_ROLE,
                vector_store=self.vector_store,
                collection=self.collection,
                search_top_k=self.search_top_k,
                search_score_threshold=self.search_score_threshold,
            ),
        )

        self._planning_proxy = observer.proxy_ask(planning_tool_addr, PlanActor)

    def get_system_prompts(self) -> list[Callable]:
        gp = _resolve(self.get_planning, GetPlanning)
        if gp and SYSTEM_PROMPT in gp.expose:
            return [self._planning_prompt_factory(gp)]
        return []

    def get_tools(self) -> list[Callable]:
        tools: list[Callable] = []

        gp = _resolve(self.get_planning, GetPlanning)
        if gp and TOOL_CALL in gp.expose:
            tools.append(self._planning_prompt_factory(gp))

        gpi = _resolve(self.get_planning_task, GetPlanningTask)
        if gpi and TOOL_CALL in gpi.expose:
            tools.append(self._get_planning_task_factory(gpi))

        up = _resolve(self.update_planning, UpdatePlanning)
        if up and TOOL_CALL in up.expose:
            tools.append(self._update_planning_factory(up))

        sp = _resolve(self.search_planning, SearchPlanning)
        if sp and TOOL_CALL in sp.expose:
            tools.append(self._search_planning_factory(sp))

        return tools

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        commands: dict[type[BaseToolParam], Callable] = {}

        gp = _resolve(self.get_planning, GetPlanning)
        if gp and COMMAND in gp.expose:
            commands[GetPlanning] = self._planning_prompt_factory(gp)

        gpi = _resolve(self.get_planning_task, GetPlanningTask)
        if gpi and COMMAND in gpi.expose:
            commands[GetPlanningTask] = self._get_planning_task_factory(gpi)

        sp = _resolve(self.search_planning, SearchPlanning)
        if sp and COMMAND in sp.expose:
            commands[SearchPlanning] = self._search_planning_factory(sp)

        return commands

    def _planning_prompt_factory(self, params: GetPlanning) -> Callable:
        planning_proxy = self._planning_proxy
        # Capture agent identity and filter setting at bind time — stable for actor's lifetime.
        agent_name = self._observer.myAddress.name
        filter_by_agent = params.filter_by_agent

        def planning_prompt() -> str:
            """Get the full team planning."""
            tasks = planning_proxy.get_planning()
            if not tasks:
                return "No current team planning."

            total = len(tasks)

            # --- Build per-owner breakdown ---
            owner_counts: dict[str, int] = {}
            for task in tasks:
                key = task.owner if task.owner else "unassigned"
                owner_counts[key] = owner_counts.get(key, 0) + 1

            named = sorted((k, v) for k, v in owner_counts.items() if k != "unassigned")
            unassigned_count = owner_counts.get("unassigned", 0)
            breakdown_parts = [f"{name}: {count}" for name, count in named]
            if unassigned_count:
                breakdown_parts.append(f"unassigned: {unassigned_count}")
            breakdown = " | ".join(breakdown_parts)

            lines = [f"**Team planning:** {total} task{'s' if total != 1 else ''} total"]
            lines.append(f"Owners: {breakdown}")

            if filter_by_agent:
                # Only include tasks where the calling agent is owner or creator.
                # Unassigned tasks (empty owner) never appear here even if creator matches.
                own_tasks = [
                    t
                    for t in tasks
                    if t.owner == agent_name or (t.owner and t.creator == agent_name)
                ]
                if own_tasks:
                    lines.append(f"\n**Your tasks** (owner or creator: {agent_name}):")
                    for task in own_tasks:
                        output_part = f" — Output: {task.output}" if task.output else ""
                        # own_tasks filter ensures task.owner is non-empty; fallback is defensive.
                        owner_label = task.owner or "unassigned"
                        suffix = f" (Owner: {owner_label}, Creator: {task.creator})"
                        lines.append(
                            f"- ID {task.id} [{task.status}] {task.description}"
                            f"{output_part}{suffix}"
                        )
                else:
                    lines.append(f"\nNo tasks assigned to or created by {agent_name} yet.")
            else:
                lines.append("\n**All tasks:**")
                for task in tasks:
                    output_part = f" — Output: {task.output}" if task.output else ""
                    owner_label = task.owner or "unassigned"
                    suffix = f" (Owner: {owner_label}, Creator: {task.creator})"
                    lines.append(
                        f"- ID {task.id} [{task.status}] {task.description}{output_part}{suffix}"
                    )

            lines.append(
                "\nUse get_planning_task(id) for exact ID lookup or "
                "search_planning(...) to filter tasks."
            )
            return "\n".join(lines)

        return planning_prompt

    def _get_planning_task_factory(self, params: GetPlanningTask) -> Callable:
        planning_proxy = self._planning_proxy

        def get_planning_task(task_id: int) -> Task | str:
            """Get a single team task by its integer ID."""
            return planning_proxy.get_planning_task(task_id)

        get_planning_task.__doc__ = params.format_docstring(get_planning_task.__doc__)
        return get_planning_task

    def _update_planning_factory(self, params: UpdatePlanning) -> Callable:
        planning_proxy = self._planning_proxy
        observer = self._observer

        def update_planning(update: UpdatePlan) -> str:
            """Update team tasks (create, update, delete).

            Field constraints (violating them causes a validation error):
            - description: max 300 characters — keep it concise.
            - output: max 150 characters — will be truncated automatically if exceeded.
            """
            ## observer.myAddress is used to set the creator of any new tasks in the plan.
            return planning_proxy.update_planning(update, observer.myAddress)

        update_planning.__doc__ = params.format_docstring(update_planning.__doc__)
        return update_planning

    def _search_planning_factory(self, params: SearchPlanning) -> Callable:
        planning_proxy = self._planning_proxy

        def search_planning(
            status: TaskStatus | None = None,
            owner: str | None = None,
            creator: str | None = None,
            query: str | None = None,
            mode: Literal["hybrid", "vector", "keyword"] = "hybrid",
            top_k: int | None = None,
            score_threshold: float | None = None,
        ) -> list[str]:
            """Search tasks. All filters are AND-combined; omit all for full list.

            Args:
                status: Filter by status.
                owner: Filter by owner.
                creator: Filter by creator.
                query: Search text for keyword and/or semantic matching.
                mode: "hybrid" (default) = keyword + semantic,
                    "keyword" = substring only, "vector" = semantic only.
                top_k: Max semantic hits (default 10).
                score_threshold: Min cosine similarity (default 0.5).

            Returns scored results: "(semantic: 0.85)", "(keyword match)", "(hybrid: 0.90)".
            """
            return planning_proxy.search_planning(
                status=status,
                owner=owner,
                creator=creator,
                query=query,
                mode=mode,
                top_k=top_k,
                score_threshold=score_threshold,
            )

        search_planning.__doc__ = params.format_docstring(search_planning.__doc__)
        return search_planning
