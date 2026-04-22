# 03 — Dynamic Agents

> A manager agent spawns worker agents at runtime, distributes tasks across them, and collects
> results — introducing parent-child hierarchy and runtime actor creation.

---

## Concepts introduced

### `self.createActor()` — spawning from inside an agent thread

In [example 01](01-hello-world.md), agents were created from `main()` using
`actor_system.createActor()`. An agent can also create other agents from within its own message
handler, using `self.createActor()`:

```python
worker_addr = self.createActor(
    WorkerAgent,
    config=BaseConfig(name=f"worker-{i}", role="Worker"),
)
```

This is how you build dynamic, on-demand agent pools: agents are spawned only when needed and
sized to the actual workload.

---

### Parent-child relationship

When an agent calls `self.createActor()`, the framework automatically:

- sets `self.myAddress` as the parent of the new child
- stores the child in `self._children`
- propagates context (user_id, team_id, orchestrator reference) from parent to child

Because `ProcessTaskRequest` extends `Message`, `sender` is always a valid `ActorAddress` —
the worker replies directly to it:

```python
class WorkerAgent(Akgent[BaseConfig, BaseState]):

    def receiveMsg_ProcessTaskRequest(self, message: ProcessTaskRequest, sender: ActorAddress) -> None:
        result = message.data.upper()
        self.send(sender, TaskResult(task_id=message.task_id, result=result, ...))
```

The child reports back to the manager via `sender` — no need to reference `self._parent` explicitly.

---

### `on_start()` hook

Agents have an `on_start()` method (from pykka) that is called once after the agent is fully
constructed. Use it to initialize instance variables (instead of overriding `__init__`, which
can interfere with the framework's setup):

```python
class ManagerAgent(Akgent[BaseConfig, BaseState]):

    def on_start(self) -> None:
        self.results: list[str] = []
        self.completed_tasks: int = 0
        self.expected_tasks: int = 0
```

---

## Message flow

```
main()            ActorSystem         ManagerAgent        WorkerAgent-1       WorkerAgent-2
  |                   |                   |                    |                   |
  |--createActor(M)-->|                   |                    |                   |
  |                   |--spawn----------->|                    |                   |
  |                   |                   |                    |                   |
  |--tell(ProcessTasksCommand)----------->|                    |                   |
  |                   |                   |                    |                   |
  |                   |   receiveMsg_ProcessTasksCommand       |                   |
  |                   |                   |--createActor(W1)-->|                   |
  |                   |                   |--createActor(W2)---|------------------>|
  |                   |                   |                    |                   |
  |                   |                   |--send(Task-1)----->|                   |
  |                   |                   |--send(Task-2)------|------------------>|
  |                   |                   |                    |                   |
  |                   |                   |  receiveMsg_ProcessTaskRequest         |
  |                   |                   |<--send(TaskResult)-|                   |
  |                   |                   |                    |                   |
  |                   |                   |                    | receiveMsg_ProcessTaskRequest
  |                   |                   |<--send(TaskResult)-|-------------------|
  |                   |                   |                    |                   |
  |                   |      receiveMsg_TaskResult (×2)        |                   |
  |                   |        → all tasks complete            |                   |
  |                   |                   |                    |                   |
  |--shutdown()------>|                   |                    |                   |
```

---

## Walkthrough

The manager receives a `ProcessTasksCommand` containing a list of tasks. For each task, it
creates a fresh `WorkerAgent` — the workers don't exist until they are needed.

```python
for i, task in enumerate(tasks):
    worker_addr = self.createActor(
        WorkerAgent,
        config=BaseConfig(name=f"WorkerAgent-{i + 1}", role="Worker"),
    )
    self.send(worker_addr, ProcessTaskRequest(task_id=task["task_id"], data=task["data"]))
```

Each worker processes its task independently and sends a `TaskResult` back to `sender`.
The manager collects results and tracks completion:

```python
def receiveMsg_TaskResult(self, message: TaskResult, sender: ActorAddress) -> None:
    self.results.append(message.result)
    self.completed_tasks += 1
    if self.completed_tasks == self.expected_tasks:
        print(f"[ManagerAgent] All results: {self.results}")
```

This pattern — fan-out to N workers, fan-in their results — is a fundamental building block for
parallel processing in the actor model.

---

## Running it

```bash
uv run python examples/03_dynamic_agents.py
```

Expected output:

```
[Dynamic Agents] Starting dynamic agent creation demo...
[ManagerAgent] Creating worker for task: task-1
[ManagerAgent] Creating worker for task: task-2
[WorkerAgent-WorkerAgent-1] Processing task: task-1 (data: 'hello')
[WorkerAgent-WorkerAgent-2] Processing task: task-2 (data: 'world')
[ManagerAgent] All results received: ['HELLO', 'WORLD']
[Dynamic Agents] Demo complete. Active workers: 2. Shutting down.
```

---

## What's next

→ [04 — Stateful Agents](04-stateful-agents.md): typed state, the observer pattern, and
Orchestrator telemetry.
