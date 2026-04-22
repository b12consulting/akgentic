# 02 — Request-Response: Proxy Tell vs Proxy Ask

> Introduces the two proxy modes — `proxy_tell` (fire-and-forget) and `proxy_ask`
> (blocking) — along with request-response message pairing.

---

## Concepts introduced

### Replying to a message

In [example 01](01-hello-world.md), messages only flowed in one direction. Here, the
`CalculatorAgent` sends a response _back_ to the caller. The `sender` parameter of every
handler gives you the address to reply to:

```python
def receiveMsg_CalculationRequest(self, message: CalculationRequest, sender: ActorAddress) -> None:
    result = compute(message)
    self.send(sender, CalculationResult(result=result, request_id=message.id))
```

When a message extends `Message`, `sender` is always a valid `ActorAddress` — no `None` check
needed. The framework guarantees this: only messages that subclass `Message` carry a `sender`,
so handlers for those message types always receive a real address.

`message.id` is the built-in UUID every `Message` carries — useful for correlating a response
back to its original request.

---

### tell vs ask

When calling agent methods from outside the actor system, you have two proxy modes:

| Mode                | Method         | Behaviour                                                                |
| ------------------- | -------------- | ------------------------------------------------------------------------ |
| **fire-and-forget** | `proxy_tell()` | Calls the method and returns immediately; you don't get the return value |
| **blocking**        | `proxy_ask()`  | Calls the method and blocks until it returns; you get the return value   |

Both modes require a **proxy**: a thin wrapper that lets you call agent methods as if they were
regular Python functions, while the actual execution happens inside the agent's thread.

```python
# Fire-and-forget: call the method, don't wait for a return value
client_tell = actor_system.proxy_tell(client_addr, ClientAgent)
client_tell.send_request_tell(calculator_addr, 10, 5, "+")
# => [ClientAgent] Sending calculation request: 10 + 5


# Blocking: call the method and get its return value
client_ask = actor_system.proxy_ask(client_addr, ClientAgent)
result = client_ask.send_request_ask(calculator_addr, 20, 3, "*")
print(f"[Request-Response] Ask result: {result}")
# => [Request-Response] Ask result: {'result': 60.0, 'request_id': UUID('...')}
```

Use `tell` when you don't need the return value. Use `ask` when you need it before
continuing.

---

### Request / response message pairing

A common pattern is to define a matching pair of messages:

```python
class CalculationRequest(Message):
    a: int
    b: int
    operation: str

class CalculationResult(Message):
    result: float
    request_id: uuid.UUID   # ties back to the original request
```

The `request_id` field stores the `id` of the original `CalculationRequest`, so the caller can
match each response to the request that triggered it. This pairing is used in the **tell** path,
where the response arrives as a separate message via `receiveMsg_CalculationResult`.

---

## Message flow

```
main()               ActorSystem            ClientAgent          CalculatorAgent
  |                      |                      |                      |
  |--createActor(Calc)-->|                      |                      |
  |                      |--spawn---------------|--------------------->|
  |                      |                      |                      |
  |--createActor(Cli)--->|                      |                      |
  |                      |--spawn-------------->|                      |
  |                      |                      |                      |
  |  [ tell pattern — fire and forget ]         |                      |
  |                      |                      |                      |
  |--proxy_tell()------->|                      |                      |
  |                      |--send_request_tell-->|                      |
  |                      |                      |--send(CalcRequest)-->|
  |                      |                      |                      |
  |                      |                      |      receiveMsg_CalculationRequest
  |                      |                      |                      |
  |                      |                      |<--send(CalcResult)---|
  |                      |                      |                      |
  |                      |        receiveMsg_CalculationResult         |
  |                      |                      |                      |
  |  [ ask pattern — blocking, no message to CalculatorAgent ]         |
  |                      |                      |                      |
  |--proxy_ask()-------->|                      |                      |
  |                      |--send_request_ask--->|                      |
  |                      |                      | (computes locally)   |
  |                      |<--return result------|                      |
  |<--result-------------|                      |                      |
  |                      |                      |                      |
  |  print(result)       |                      |                      |
  |                      |                      |                      |
  |--shutdown()--------->|                      |                      |
```

---

## Walkthrough

The key insight of this example is the two **proxy modes** for calling agent methods from
outside the actor system:

**`proxy_tell`** wraps agent method calls in fire-and-forget mode. The main thread calls
`send_request_tell`, which sends a `CalculationRequest` message to the `CalculatorAgent`.
The calculator processes it and sends a `CalculationResult` back to the `ClientAgent`, which
handles it in `receiveMsg_CalculationResult`. The main thread doesn't wait for any of this.

**`proxy_ask`** wraps agent method calls in blocking mode. The main thread calls
`send_request_ask`, which computes the result **locally inside the ClientAgent** and returns
it. No message is sent to the `CalculatorAgent`. The main thread blocks until the method
returns, then receives the return value directly.

The two paths are intentionally different to highlight the contrast: `tell` triggers
asynchronous inter-agent messaging, while `ask` gives you a synchronous return value from
the called method itself. Note that in the `ask` path, the `request_id` in the returned
dictionary is an arbitrary UUID — there is no correlated request message, unlike the `tell`
path where `request_id` ties back to the original `CalculationRequest.id`.

---

## Running it

```bash
uv run python examples/02_request_response.py
```

Expected output:

```
[Request-Response] Demonstrating proxy_tell and proxy_ask patterns...
[ClientAgent] Sending calculation request: 10 + 5
[CalculatorAgent] Processing request: 10 + 5
[ClientAgent] Received result: 15.0
[ClientAgent] Computing calculation locally (ask): 20 * 3
[Request-Response] Ask result: {'result': 60.0, 'request_id': UUID('...')}
[Request-Response] All calculations complete. Shutting down.
```

---

## What's next

> [03 — Dynamic Agents](03-dynamic-agents.md): agents that spawn other agents at runtime.
