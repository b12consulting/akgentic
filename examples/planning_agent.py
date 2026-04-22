"""Planning Tool example — demonstrates end-to-end usage with semantic search.

Shows how to integrate ``PlanningTool`` with an actor-based setup, create
sprint tasks, perform exact-ID and semantic lookups, and observe the
agent-scoped planning system prompt.

Run without OpenAI API key for exact-ID lookup only (semantic falls back gracefully):
    uv run python packages/akgentic-tool/examples/planning_agent.py

Run with OpenAI API key for semantic search:
    OPENAI_API_KEY=sk-... uv run python packages/akgentic-tool/examples/planning_agent.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.tool.planning.planning import (
    PLANNING_ACTOR_NAME,
    PLANNING_ACTOR_ROLE,
    GetPlanning,
    GetPlanningTask,
    PlanningTool,
    UpdatePlanning,
)
from akgentic.tool.planning.planning_actor import (
    PlanActor,
    PlanConfig,
    Task,
    TaskCreate,
    UpdatePlan,
)

# ---------------------------------------------------------------------------
# Minimal actor wiring (same pattern as knowledge_agent.py)
# ---------------------------------------------------------------------------


class _MockAddress(ActorAddress):
    """Minimal ActorAddress stand-in for example wiring."""

    def __init__(self, name: str = "example-agent", role: str = "example-role") -> None:
        self._name = name
        self._role = role
        self._agent_id = uuid.uuid4()

    @property
    def agent_id(self) -> uuid.UUID:
        return self._agent_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    @property
    def team_id(self) -> uuid.UUID:
        return self._agent_id

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: Any, message: Any) -> None:  # noqa: ANN401 — ActorAddress protocol allows Any for generic message passing
        pass

    def is_alive(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> ActorAddressDict:
        return {
            "__actor_address__": True,
            "__actor_type__": type(self).__qualname__,
            "agent_id": str(self._agent_id),
            "name": self._name,
            "role": self._role,
            "team_id": "",
            "squad_id": "",
            "user_message": False,
        }

    def __repr__(self) -> str:
        return f"_MockAddress(name={self._name})"


class _OrchestratorStub:
    """Minimal orchestrator stub returning the singleton PlanActor address."""

    def __init__(self, plan_addr: _MockAddress) -> None:
        self._plan_addr = plan_addr
        self._vs_addr = _MockAddress("#VectorStore", "ToolActor")

    def getChildrenOrCreate(  # noqa: N802
        self, actor_class: type, config: object = None,
    ) -> ActorAddress:
        """Return plan addr for PlanActor, VS addr for VectorStore actors."""
        from akgentic.tool.vector_store.actor import VectorStoreActor

        if actor_class is VectorStoreActor:
            return self._vs_addr
        return self._plan_addr


class _ExampleObserver:
    """Observer that wires a real PlanActor for the example.

    Replicates the pattern from ``knowledge_agent.py`` so the example
    runs without a full Pykka actor system.
    """

    def __init__(self) -> None:
        self.events: list[object] = []
        self._address = _MockAddress("planning-agent")
        self._orchestrator_addr = _MockAddress("orchestrator")
        self._plan_actor = PlanActor(config=PlanConfig(
            name=PLANNING_ACTOR_NAME,
            role=PLANNING_ACTOR_ROLE,
        ))
        self._plan_actor.on_start()
        self._plan_addr = _MockAddress(PLANNING_ACTOR_NAME, PLANNING_ACTOR_ROLE)

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        """Return the example agent's actor address."""
        return self._address

    @property
    def orchestrator(self) -> ActorAddress:
        """Return the orchestrator's actor address."""
        return self._orchestrator_addr

    def notify_event(self, event: object) -> None:
        """Record events for introspection."""
        self.events.append(event)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> Any:  # noqa: ANN401
        """Return the appropriate stub based on which actor is being asked."""
        if actor == self._orchestrator_addr:
            return _OrchestratorStub(self._plan_addr)
        # Return mock for VectorStoreActor proxy
        if hasattr(actor, 'name') and actor.name == "#VectorStore":
            from unittest.mock import MagicMock

            return MagicMock()
        return self._plan_actor


# ---------------------------------------------------------------------------
# Domain: sprint backlog for a web service
# ---------------------------------------------------------------------------

_SPRINT_TASKS: list[TaskCreate] = [
    TaskCreate(
        id=1,
        description="Design REST API endpoint schema",
        status="completed",
        owner="@Alice",
    ),
    TaskCreate(
        id=2,
        description="Implement authentication middleware",
        status="started",
        owner="@Bob",
    ),
    TaskCreate(
        id=3,
        description="Write integration tests for auth",
        status="pending",
        owner="@Alice",
        dependencies=[2],
    ),
    TaskCreate(
        id=4,
        description="Set up CI pipeline",
        status="pending",
        owner="@Bob",
    ),
    TaskCreate(
        id=5,
        description="Database migration for user table",
        status="pending",
        owner="",
    ),
]


# ---------------------------------------------------------------------------
# Example functions
# ---------------------------------------------------------------------------


def create_sprint_tasks(plan_actor: PlanActor, address: ActorAddress) -> None:
    """Populate the plan with sprint backlog tasks.

    Args:
        plan_actor: The PlanActor instance to mutate.
        address: The actor address of the creator.
    """
    print("\n--- Step 1: Creating sprint tasks ---")
    plan_actor.update_planning(
        UpdatePlan(create_tasks=_SPRINT_TASKS),
        address,
    )
    print(f"Created {len(_SPRINT_TASKS)} tasks.")


def demonstrate_exact_lookup(plan_actor: PlanActor) -> None:
    """Show exact-ID lookup returning a typed Task object.

    Args:
        plan_actor: The PlanActor instance to query.
    """
    print("\n--- Step 2: Exact-ID lookup ---")
    result = plan_actor.get_planning_task(2)
    if not isinstance(result, Task):
        msg = f"Expected Task from exact-ID lookup, got {type(result).__name__}: {result}"
        raise TypeError(msg)
    print(
        f"Task {result.id} (int lookup): {result.description}"
        f" [{result.status}] owner={result.owner}"
    )


def demonstrate_semantic_search(plan_actor: PlanActor) -> None:
    """Show semantic search — guarded by OPENAI_API_KEY availability.

    Args:
        plan_actor: The PlanActor instance to query.
    """
    print("\n--- Step 3: Semantic search ---")
    if os.environ.get("OPENAI_API_KEY"):
        # Queries with no substring overlap — proves real semantic value
        result = plan_actor.get_planning_task("login security")
        print(f'  get_planning_task("login security") → {result}')
        result = plan_actor.get_planning_task("automated testing")
        print(f'  get_planning_task("automated testing") → {result}')
    else:
        print("OPENAI_API_KEY not set — semantic search unavailable (graceful fallback):")
        result = plan_actor.get_planning_task("login security")
        print(f'  get_planning_task("login security") → "{result}"')


def demonstrate_planning_prompt(tool: PlanningTool) -> None:
    """Show the agent-scoped planning system prompt.

    Args:
        tool: The configured PlanningTool for system-prompt demonstration.
    """
    print("\n--- Step 4: Agent-scoped planning system prompt ---")
    prompts = tool.get_system_prompts()
    if prompts:
        system_prompt = prompts[0]()
        print(system_prompt)


def main() -> None:
    """Run the planning agent example end-to-end.

    Sets up an actor-based planning tool, creates sprint tasks,
    demonstrates exact-ID and semantic lookups, prints the agent-scoped
    system prompt, then shuts down cleanly.
    """
    print("=" * 60)
    print("Planning Tool — Semantic Search Example")
    print("=" * 60)

    # --- Setup: wire PlanningTool with observer ---
    print("\n--- Setup: configuring PlanningTool ---")
    observer = _ExampleObserver()
    tool = PlanningTool(
        get_planning=GetPlanning(filter_by_agent=True),
        get_planning_task=GetPlanningTask(),
        update_planning=UpdatePlanning(),
    )
    tool.observer(observer)
    print(f"Tool: {tool.name} — {tool.description}")

    # Access the underlying actor via observer for direct calls
    plan_actor = observer._plan_actor
    address = observer.myAddress

    # --- Create sprint tasks ---
    create_sprint_tasks(plan_actor, address)

    # --- Exact-ID lookup ---
    demonstrate_exact_lookup(plan_actor)

    # --- Semantic search ---
    demonstrate_semantic_search(plan_actor)

    # --- Agent-scoped planning system prompt ---
    demonstrate_planning_prompt(tool)

    # --- Summary of tool call events ---
    print("\n--- Tool call event log ---")
    print(f"Total tool events recorded: {len(observer.events)}")
    for event in observer.events:
        print(f"  [{event.tool_name}]")

    # --- Shutdown: proper actor lifecycle (start was called in observer init) ---
    plan_actor.on_stop()
    print("\n--- Shutdown complete ---")
    print("Planning agent example finished successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
