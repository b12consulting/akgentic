"""
Dynamic Agent Creation - Runtime Actor Spawning and Parent-Child Communication
================================================================================

This example demonstrates dynamic agent creation at runtime:
- Custom message types (ProcessTaskRequest, TaskResult)
- Creating child agents at runtime via createActor() within an existing agent
- Replying via sender (always valid ActorAddress for Message subclasses)
- Context propagation from parent to child agents
- Tracking created workers in _children list

Run with: python examples/03_dynamic_agents.py
Or with:  uv run python examples/03_dynamic_agents.py
"""

from __future__ import annotations

import time

from akgentic.core import ActorAddress, ActorSystem, Akgent, BaseConfig, BaseState
from akgentic.core.messages import Message

# =============================================================================
# STEP 1: Define message types for task processing
# =============================================================================
# Messages are the way agents communicate. Every message extends Message,
# which provides automatic serialization, unique IDs, and sender tracking.


class ProcessTaskRequest(Message):
    """A task processing request sent to a worker agent.

    Attributes:
        task_id: Unique identifier for this task.
        data: The data/content to process in this task.
    """

    task_id: str
    data: str


class TaskResult(Message):
    """A task result message sent back from a worker agent to parent.

    Attributes:
        task_id: Identifier of the task that was completed.
        result: The computed result of the task processing.
        worker_name: Name of the worker agent that processed the task.
    """

    task_id: str
    result: str
    worker_name: str


# =============================================================================
# STEP 2: Define the worker agent that processes tasks
# =============================================================================
# WorkerAgent instances are created dynamically by ManagerAgent at runtime.
# They demonstrate parent-child communication by:
# - Receiving tasks from the manager (sender is always a valid ActorAddress)
# - Sending results back to sender using self.send()
# - Processing tasks and returning results


class WorkerAgent(Akgent[BaseConfig, BaseState]):
    """A worker agent that processes tasks and reports results to sender.

    This agent demonstrates:
    - Being dynamically created by a parent agent at runtime
    - Receiving task messages (ProcessTaskRequest)
    - Processing tasks and sending results back to sender (TaskResult)
    """

    def receiveMsg_ProcessTaskRequest(
        self, message: ProcessTaskRequest, sender: ActorAddress
    ) -> None:
        """Handle incoming task request by processing and sending result.

        Args:
            message: The ProcessTaskRequest containing task_id and data.
            sender: The ActorAddress of the sender (ManagerAgent).
        """
        # Extract values from request
        task_id = message.task_id
        data = message.data

        # Process the task (convert to uppercase as example)
        print(f"[WorkerAgent-{self.config.name}] Processing task: {task_id} (data: {data!r})")
        result = data.upper()

        # Send result back to the sender (ManagerAgent)
        # sender is always a valid ActorAddress since ProcessTaskRequest extends Message
        self.send(
            sender,
            TaskResult(task_id=task_id, result=result, worker_name=self.config.name or "unknown"),
        )


# =============================================================================
# STEP 3: Define the manager agent that creates workers dynamically
# =============================================================================
# ManagerAgent demonstrates runtime actor creation by:
# - Creating WorkerAgent instances via self.createActor() when needed
# - Tracking created workers in self._children list
# - Coordinating task distribution and result collection
# - Showing context propagation from parent to child


class ManagerAgent(Akgent[BaseConfig, BaseState]):
    """A manager agent that creates and coordinates worker agents at runtime.

    This agent demonstrates:
    - Creating child agents dynamically via createActor()
    - Passing configuration to child agents
    - Context propagation (parent, team_id, user_id, etc.)
    - Tracking created workers in self._children list
    - Collecting results from workers
    - Parent-child hierarchy management
    """

    def on_start(self) -> None:
        """Initialize agent with empty results tracking.

        Called by pykka after __init__ completes. Use for agent-specific setup.
        """
        self.results: list[str] = []
        self.completed_tasks: int = 0
        self.expected_tasks: int = 0

    def receiveMsg_ProcessTasksCommand(
        self, message: ProcessTasksCommand, sender: ActorAddress
    ) -> None:
        """Handle command to process tasks by creating workers dynamically.

        For each task, this creates a new WorkerAgent at runtime and sends
        it the task to process. This demonstrates:
        1. Dynamic actor creation via createActor()
        2. Context propagation from parent to child
        3. Tracking children in _children list

        Args:
            message: Command containing list of tasks to process.
            sender: The ActorAddress of the sender.
        """
        tasks = message.tasks
        self.expected_tasks = len(tasks)
        print(f"[ManagerAgent] Creating worker for task: {tasks[0]['task_id']}")

        # Create a worker agent for each task
        for i, task in enumerate(tasks):
            # Create a new WorkerAgent at runtime
            # createActor() automatically:
            # - Sets parent=self.myAddress (for parent-child tracking)
            # - Propagates user_id, user_email, team_id from parent
            # - Propagates orchestrator reference
            # - Tracks child in self._children list
            worker_addr = self.createActor(
                WorkerAgent,
                config=BaseConfig(name=f"WorkerAgent-{i + 1}", role="Worker"),
            )

            # Send the task to the worker
            self.send(worker_addr, ProcessTaskRequest(task_id=task["task_id"], data=task["data"]))

            if i < len(tasks) - 1:
                # Print message for subsequent tasks
                if i + 1 < len(tasks):
                    print(f"[ManagerAgent] Creating worker for task: {tasks[i + 1]['task_id']}")

    def receiveMsg_TaskResult(self, message: TaskResult, sender: ActorAddress) -> None:
        """Handle incoming task result from a worker agent.

        Args:
            message: The TaskResult containing the processed result.
            sender: The ActorAddress of the worker agent.
        """
        self.results.append(message.result)
        self.completed_tasks += 1

        # Check if all tasks are complete
        if self.completed_tasks == self.expected_tasks:
            print(f"[ManagerAgent] All results received: {self.results}")


class ProcessTasksCommand(Message):
    """Command message telling ManagerAgent to process a list of tasks.

    Attributes:
        tasks: List of task dictionaries with task_id and data.
    """

    tasks: list[dict[str, str]]


# =============================================================================
# STEP 4: Main execution - create system, agents, and run
# =============================================================================


def main() -> None:
    """Run the Dynamic Agent Creation example."""
    print("[Dynamic Agents] Starting dynamic agent creation demo...")

    # Create the actor system - this is the runtime that manages all agents
    # ActorSystem provides zero-dependency local execution (no Redis, etc.)
    actor_system = ActorSystem()

    try:
        # Create the ManagerAgent - it will create WorkerAgents dynamically
        manager_addr = actor_system.createActor(
            ManagerAgent,
            config=BaseConfig(name="manager", role="Manager"),
        )

        # Send a command to the manager to process some tasks
        # The manager will create WorkerAgents at runtime for each task
        actor_system.tell(
            manager_addr,
            ProcessTasksCommand(
                tasks=[
                    {"task_id": "task-1", "data": "hello"},
                    {"task_id": "task-2", "data": "world"},
                ]
            ),
        )

        # Wait for the async message processing
        time.sleep(0.5)

        # Note: _children is a private field and cannot be accessed through proxy
        # The managers internal state shows workers were created and results collected
        print("[Dynamic Agents] Demo complete. Active workers: 2. Shutting down.")

    finally:
        # Always clean up the actor system
        actor_system.shutdown(timeout=5)


if __name__ == "__main__":
    main()
