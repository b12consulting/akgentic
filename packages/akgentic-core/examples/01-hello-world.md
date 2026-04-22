# 01 — Hello World

> Two agents exchange a greeting, introducing the fundamental building blocks of akgentic-core.

---

## Concepts introduced

### Message

A `Message` is the only way agents communicate. You define your own message types by subclassing
`Message` and adding typed fields:

```python
from akgentic.core.messages import Message

class HelloMessage(Message):
    greeting: str
```

Every message automatically gets:

- a unique `id` (UUID)
- a `sender_id` tracking who sent it
- serialization/deserialization

Messages are **immutable data packets** — they carry information, nothing else.

---

### Akgent

An `Akgent` is an actor: an isolated unit of state and behavior. You define an agent by
subclassing `Akgent[ConfigType, StateType]` and adding **message handlers**:

```python
from akgentic.core import Akgent, BaseConfig, BaseState

class ReceiverAgent(Akgent[BaseConfig, BaseState]):

    def receiveMsg_HelloMessage(self, message: HelloMessage, sender: ActorAddress) -> None:
        print(f"Received: {message.greeting}")
```

The naming convention `receiveMsg_<ClassName>` is the dispatch key. When a `HelloMessage`
arrives in this agent's mailbox, the framework automatically calls `receiveMsg_HelloMessage`.
No manual routing code needed.

`BaseConfig` and `BaseState` are the minimal built-in config and state types. You use them as-is
until you need custom fields (covered in later examples).

---

### ActorSystem

`ActorSystem` is the runtime. It:

- creates agents and assigns them addresses
- delivers messages between agents
- manages the lifecycle (startup / shutdown)

```python
from akgentic.core import ActorSystem

actor_system = ActorSystem()
```

---

### ActorAddress

An `ActorAddress` is a reference to an agent — like a mailbox address. You never hold a direct
Python object reference to another agent; you always use its address.

Addresses are returned by `createActor()`:

```python
receiver_addr = actor_system.createActor(
    ReceiverAgent,
    config=BaseConfig(name="receiver", role="Receiver"),
)
```

---

### Sending messages

From **outside** the actor system (e.g. in `main()`), use `actor_system.tell()`:

```python
actor_system.tell(greeter_addr, SendGreetingCommand(target=receiver_addr, greeting="Hello!"))
```

From **inside** an agent, use `self.send()`:

```python
self.send(message.target, HelloMessage(greeting=message.greeting))
```

Both are **fire-and-forget**: the call returns immediately without waiting for the recipient to
process the message.

---

## Message flow

```
main()               ActorSystem          GreeterAgent           ReceiverAgent
  |                      |                     |                       |
  |--createActor(R)----->|                     |                       |
  |                      |--spawn--------------|---------------------->|
  |                      |                     |                       |
  |--createActor(G)----->|                     |                       |
  |                      |--spawn------------->|                       |
  |                      |                     |                       |
  |--tell(SendGreetingCommand)---------------->|                       |
  |                      |                     |                       |
  |                      |       receiveMsg_SendGreetingCommand        |
  |                      |                     |                       |
  |                      |                     |--send(HelloMessage)-->|
  |                      |                     |                       |
  |                      |                     |         receiveMsg_HelloMessage
  |                      |                     |                       |
  |--shutdown()--------->|                     |                       |
```

---

## Walkthrough

The example wires two agents together:

1. `GreeterAgent` — knows how to send greetings. When it receives a `SendGreetingCommand`
   (which carries a target address and a greeting text), it creates a `HelloMessage` and
   forwards it to the target.

2. `ReceiverAgent` — listens for `HelloMessage` and prints the greeting.

The indirection through `SendGreetingCommand` is intentional: it illustrates that agents do not
call each other directly. Everything flows through messages.

From `main()`, the actor system is the only entry point:

- `createActor()` spawns agents and returns their addresses
- `tell()` drops a message into an agent's mailbox
- `shutdown()` cleanly stops all agents

```python
actor_system = ActorSystem()

receiver_addr = actor_system.createActor(ReceiverAgent, config=BaseConfig(...))
greeter_addr  = actor_system.createActor(GreeterAgent,  config=BaseConfig(...))

actor_system.tell(greeter_addr, SendGreetingCommand(target=receiver_addr, greeting="Hello!"))

time.sleep(0.5)          # wait for async processing
actor_system.shutdown()
```

The `time.sleep()` is needed because message delivery is asynchronous — `tell()` returns before
the agent processes the message.

---

## Running it

```bash
uv run python examples/01_hello_world.py
```

Expected output:

```
[Hello World] Starting two-agent message exchange...
[ReceiverAgent] Received greeting: Hello from GreeterAgent!
[Hello World] Exchange complete. Shutting down.
```

---

## What's next

→ [02 — Request-Response](02-request-response.md): agents reply to messages and you wait for results.
