from __future__ import annotations

import datetime
import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import Akgent, BaseConfig, BaseState
from akgentic.core.orchestrator import Orchestrator
from akgentic.core.utils.serializer import SerializableBaseModel
from akgentic.tool.errors import RetriableError
from akgentic.tool.vector import VectorEntry
from akgentic.tool.vector_store.actor import VS_ACTOR_NAME, VectorStoreActor
from akgentic.tool.vector_store.protocol import CollectionConfig

logger = logging.getLogger(__name__)

TaskStatus = Literal["pending", "started", "completed", "abort"]


class TaskCreate(SerializableBaseModel):
    id: int = Field(..., description="Unique identifier of the task.")
    status: TaskStatus = Field(..., description="Status of the task.")
    description: str = Field(
        ..., max_length=300, description="Short description of the task (max 300 chars)."
    )
    owner: str = Field(..., description="Assigned team member name; empty if not yet assigned.")
    dependencies: list[int] = Field(
        default_factory=list,
        description="List of task IDs that must be completed before this one.",
    )


class TaskUpdate(SerializableBaseModel):
    id: int = Field(..., description="Unique identifier of the task.")
    status: TaskStatus | None = Field(default=None, description="New status of the task.")
    description: str | None = Field(
        default=None, max_length=300, description="New description of the task (max 300 chars)."
    )
    output: str | None = Field(
        default=None,
        max_length=150,
        description="New output or result of the task (max 150 chars; truncated automatically).",
    )
    owner: str | None = Field(default=None, description="New assigned team member name;")
    dependencies: list[int] | None = Field(
        default=None,
        description="New list of task IDs that must be completed first.",
    )

    @field_validator("output", mode="before")
    @classmethod
    def truncate_output(cls, v: object) -> object:
        """Silently truncate output to 150 chars to avoid validation errors."""
        if isinstance(v, str) and len(v) > 150:
            return v[:147] + "..."
        return v


class Task(TaskCreate):
    output: str = Field(default="", description="Output or result of the task.")
    creator: str = Field(default="", description="Team member name who creates the task.")
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        description="ISO timestamp of the last update.",
    )


class UpdatePlan(BaseModel):
    create_tasks: list[TaskCreate] = Field(
        default_factory=list, description="Tasks to add to the plan."
    )
    update_tasks: list[TaskUpdate] = Field(
        default_factory=list, description="Tasks to update in the plan."
    )
    delete_tasks: list[int] = Field(
        default_factory=list, description="Tasks to remove from the plan."
    )


class PlanManagerState(BaseState):
    task_list: list[Task] = Field(default_factory=list)


PLAN_COLLECTION: str = "planning"
"""Collection name used in VectorStoreActor."""


class PlanConfig(BaseConfig):
    """Configuration for PlanActor with optional vector-backed semantic search."""

    vector_store: bool | str = Field(
        default=True,
        description=(
            "Binding to a VectorStoreActor: True=default #VectorStore, "
            "str=named instance, False=degraded mode (no vector search)."
        ),
    )
    collection: CollectionConfig = Field(
        default_factory=CollectionConfig,
        description=(
            "Vector collection configuration forwarded to "
            "VectorStoreActor.create_collection."
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


class PlanActor(Akgent[PlanConfig, PlanManagerState]):
    """Actor responsible for managing the execution of a plan.

    The PlanManager oversees the execution of a plan, coordinating
    between different agents and tools as needed. It maintains the
    state of the plan execution and handles any necessary communication
    with other actors.

    Attributes:
        config: Configuration for the PlanManager.
        state: Current state of the plan execution.
    """

    def on_start(self) -> None:
        self.state = PlanManagerState()
        self.state.observer(self)
        # Coerce BaseConfig → PlanConfig if the actor was started with a plain BaseConfig
        # (e.g., from existing PlanningTool wiring prior to Story 2.2 update).
        # This preserves backward compatibility while giving PlanActor typed config access.
        if not isinstance(self.config, PlanConfig):
            self.config = PlanConfig(
                name=self.config.name,
                role=self.config.role,
            )
        self._vs_proxy: VectorStoreActor | None = None
        if self.config.vector_store is not False:
            self._acquire_vs_proxy()

    def _acquire_vs_proxy(self) -> None:
        """Look up the VectorStoreActor proxy and create the planning collection.

        The VectorStoreActor is owned by ``VectorStoreTool``; this actor
        only resolves it by name. Behaviour:

        - ``config.vector_store is False`` → stay in degraded mode (no
          lookup, ``_vs_proxy`` remains ``None``).
        - ``self.orchestrator is None`` (test harness) → log WARNING and
          return — existing behaviour preserved.
        - Otherwise look up the target actor name (``config.vector_store``
          when a ``str``, else ``VS_ACTOR_NAME``) via
          ``orch_proxy.get_team_member``. Raise ``RuntimeError`` when it
          is missing — a missing VectorStoreTool is a **configuration**
          error, not a runtime degradation.
        - A transient backend error during ``create_collection`` drops
          back to degraded mode with a WARNING (matches existing
          behaviour for embedding failures).
        """
        if self.config.vector_store is False:
            return  # degraded mode by design

        if self.orchestrator is None:
            logger.warning(
                "[%s] No orchestrator; operating in degraded mode",
                self.config.name,
            )
            return

        vs_name = (
            self.config.vector_store
            if isinstance(self.config.vector_store, str)
            else VS_ACTOR_NAME
        )
        orch_proxy = self.proxy_ask(self.orchestrator, Orchestrator)
        vs_addr = orch_proxy.get_team_member(vs_name)
        if vs_addr is None:
            raise RuntimeError(
                f"{self.config.name} requires VectorStoreActor '{vs_name}' "
                f"but it was not found. Ensure VectorStoreTool is in the team config."
            )
        self._vs_proxy = self.proxy_ask(vs_addr, VectorStoreActor)
        try:
            self._vs_proxy.create_collection(PLAN_COLLECTION, self.config.collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] create_collection on VectorStoreActor failed: %s — degraded mode",
                self.config.name,
                exc,
            )
            self._vs_proxy = None

    def _embed_task(self, task: Task) -> None:
        """Embed a task's description and store the resulting VectorEntry.

        Called after task create or update. Does nothing when VectorStoreActor
        proxy is unavailable (vector_store=False or proxy not acquired).
        Any embedding error is logged and swallowed so that task CRUD
        is never interrupted by a transient embedding failure.
        """
        if self._vs_proxy is None:
            return
        try:
            vectors = self._vs_proxy.embed([task.description])
            if not vectors:
                return
            entry = VectorEntry(
                ref_type="task",
                ref_id=str(task.id),
                text=task.description,
                vector=vectors[0],
            )
            self._vs_proxy.add(PLAN_COLLECTION, [entry])
        except Exception:  # noqa: BLE001
            logger.warning(
                "Embedding failed for task %s — semantic index not updated", task.id, exc_info=True
            )

    def _create_task(self, task: TaskCreate, actor_address: ActorAddress) -> None:
        new_task = Task(**task.__dict__, creator=actor_address.name)
        self.state.task_list.append(new_task)
        if self._vs_proxy is not None:
            self._embed_task(new_task)

    def _update_task(self, task_update: TaskUpdate) -> None | str:
        # Use __dict__ to get raw values, filter out None to only apply explicitly set fields
        updates = {k: v for k, v in task_update.__dict__.items() if v is not None}
        for idx, task in enumerate(self.state.task_list):
            if task.id == task_update.id:
                updated_task = task.model_copy(update=updates)
                self.state.task_list[idx] = updated_task
                # Re-index only when the description actually changed (AC#5).
                description_changed = (
                    task_update.description is not None
                    and task_update.description != task.description
                )
                if description_changed and self._vs_proxy is not None:
                    self._vs_proxy.remove(PLAN_COLLECTION, [str(task.id)])
                    self._embed_task(updated_task)
                return None
        return f"Update error - no task with ID {task_update.id} found."

    ##
    ## Tools to expose to agents:
    ##
    def get_planning(self) -> list[Task]:
        """Get the current plan tasks."""
        return self.state.task_list

    def get_planning_task(self, task_id: int) -> Task | str:
        """Look up a task by exact integer ID.

        Args:
            task_id: The integer ID of the task to retrieve.

        Returns:
            The matching ``Task`` if found, or ``"No task with that ID."`` if no
            task with that ID exists.
        """
        return next(
            (t for t in self.state.task_list if t.id == task_id), "No task with that ID."
        )

    def search_planning(
        self,
        status: TaskStatus | None = None,
        owner: str | None = None,
        creator: str | None = None,
        query: str | None = None,
        mode: Literal["hybrid", "vector", "keyword"] = "hybrid",
        top_k: int | None = None,
        score_threshold: float | None = None,
    ) -> list[str]:
        """Search tasks with optional multi-criteria filters (AND logic).

        Args:
            status: Exact match on task.status. None means no filter.
            owner: Exact match on task.owner. Empty string matches unassigned tasks.
                   None means no filter.
            creator: Exact match on task.creator. None means no filter.
            query: Case-insensitive substring match on task.description (keyword phase).
                   When vector deps are available, also runs a semantic phase.
                   Keyword and semantic hits are unioned before other filters apply.
                   Degrades to keyword-only without raising when vector deps absent.
                   None means no filter.
            mode: Search mode — ``"hybrid"`` (default) runs both keyword and
                   semantic phases; ``"keyword"`` skips embedding/vector search;
                   ``"vector"`` skips keyword substring matching. When
                   ``_vs_proxy is None`` and ``mode="vector"``, returns empty
                   results; ``mode="hybrid"`` falls back to keyword-only with
                   a warning.
            top_k: Maximum number of semantic search hits. When None, uses
                   ``config.search_top_k`` (default 10).
            score_threshold: Minimum cosine similarity score for semantic results.
                   When None, uses ``config.search_score_threshold`` (default 0.5).

        Returns:
            Formatted strings for each matching task, ordered by score (highest
            first). Each string includes the task ID, description, status, owner,
            and a score label: ``(semantic: 0.85)`` for vector hits,
            ``(keyword match)`` for keyword-only hits, or ``(hybrid: 0.90)``
            for hits found by both keyword and semantic.
            When all parameters are None, returns the full task list (unscored).
        """
        effective_top_k = top_k if top_k is not None else self.config.search_top_k
        effective_threshold = (
            score_threshold if score_threshold is not None else self.config.search_score_threshold
        )

        tasks: list[Task] = list(self.state.task_list)

        # Track scores: {task_id: (score, label)}
        scores: dict[int, tuple[float, str]] = {}

        # Query phase: build candidate set based on mode, then intersect with rest
        if query is not None:
            run_keyword = mode in ("hybrid", "keyword")
            run_semantic = mode in ("hybrid", "vector")

            # Keyword phase
            if run_keyword:
                q_lower = query.lower()
                for t in tasks:
                    if q_lower in t.description.lower():
                        scores[t.id] = (1.0, "keyword match")

            # Semantic phase
            if run_semantic and self._vs_proxy is not None:
                try:
                    vectors = self._vs_proxy.embed([query])
                    if vectors:
                        query_vector = vectors[0]
                        result = self._vs_proxy.search(
                            PLAN_COLLECTION, query_vector, effective_top_k
                        )
                        for hit in result.hits:
                            if hit.score >= effective_threshold:
                                tid = int(hit.ref_id)
                                if tid in scores:
                                    # Found by both keyword and semantic — hybrid label
                                    scores[tid] = (
                                        max(scores[tid][0], hit.score),
                                        f"hybrid: {hit.score:.2f}",
                                    )
                                else:
                                    scores[tid] = (hit.score, f"semantic: {hit.score:.2f}")
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Semantic search failed for query — falling back to keyword only",
                        exc_info=True,
                    )
            elif run_semantic and self._vs_proxy is None:
                if mode == "vector":
                    logger.warning(
                        "mode='vector' but _vs_proxy is None — returning empty results"
                    )
                elif mode == "hybrid":
                    logger.warning(
                        "mode='hybrid' but _vs_proxy is None — falling back to keyword-only"
                    )

            candidate_ids = set(scores.keys())
            tasks = [t for t in tasks if t.id in candidate_ids]

        # Apply remaining AND filters
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if owner is not None:
            tasks = [t for t in tasks if t.owner == owner]
        if creator is not None:
            tasks = [t for t in tasks if t.creator == creator]

        # Sort by score descending when query was provided
        if query is not None:
            tasks.sort(key=lambda t: scores.get(t.id, (0.0, ""))[0], reverse=True)

        # Format output
        lines: list[str] = []
        for t in tasks:
            owner_label = t.owner or "unassigned"
            base = (
                f"Task {t.id}: {t.description} [{t.status}] "
                f"(Owner: {owner_label}, Creator: {t.creator})"
            )
            if t.id in scores:
                _, label = scores[t.id]
                base += f" ({label})"
            lines.append(base)

        return lines

    def update_planning(self, update: UpdatePlan, actor_address: ActorAddress) -> str:
        """Update the plan with new, updated, or deleted task."""

        errors = []

        # Handle task creation
        for task_create in update.create_tasks:
            self._create_task(task_create, actor_address)

        # Handle task updates
        for task_update in update.update_tasks:
            error = self._update_task(task_update)
            if error is not None:
                errors.append(error)

        # Handle task deletions
        for task_id in update.delete_tasks:
            if not any(task.id == task_id for task in self.state.task_list):
                errors.append(f"Delete error - no task with ID {task_id} found.")
            else:
                if self._vs_proxy is not None:
                    self._vs_proxy.remove(PLAN_COLLECTION, [str(task_id)])
                self.state.task_list = [task for task in self.state.task_list if task.id != task_id]

        self.state.notify_state_change()

        if errors:
            raise RetriableError("Update errors: " + "; ".join(errors))
        return "Done"
