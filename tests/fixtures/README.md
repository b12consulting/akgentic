# Test Fixture Factories

**Rule: No hand-crafted event dicts -- use the fixture factory.**

## Why

Factory functions create real Pydantic model (or dataclass) instances and call
`model_dump()` (or `dataclasses.asdict()`). This guarantees that every test dict
matches the actual serialization contract. If a model field is added, removed, or
renamed, the factory breaks at construction time -- not silently at test time.

## Usage

```python
from tests.fixtures.events import make_sent_message, make_error_message
from tests.fixtures.models import make_team_info

# Zero-arg call gives sensible defaults
event = make_sent_message()

# Override only the fields you care about
event = make_sent_message(content="custom greeting")
error = make_error_message(exception_type="RuntimeError")
team = make_team_info(name="My Team", status="stopped")
```

## Available Factories

### Event factories (`tests/fixtures/events.py`)

| Factory | Source Model | Package |
|---|---|---|
| `make_sent_message()` | `SentMessage` | akgentic-core |
| `make_event_message()` | `EventMessage` | akgentic-core |
| `make_error_message()` | `ErrorMessage` | akgentic-core |
| `make_start_message()` | `StartMessage` | akgentic-core |
| `make_received_message()` | `ReceivedMessage` | akgentic-core |
| `make_processed_message()` | `ProcessedMessage` | akgentic-core |
| `make_tool_call_event()` | `ToolCallEvent` | akgentic-llm |
| `make_tool_return_event()` | `ToolReturnEvent` | akgentic-llm |
| `make_llm_usage_event()` | `LlmUsageEvent` | akgentic-llm |

### Model factories (`tests/fixtures/models.py`)

| Factory | Source Model | Package |
|---|---|---|
| `make_team_info()` | `TeamInfo` | akgentic-infra |
| `make_event_info()` | `EventInfo` | akgentic-infra |

## The `**overrides` Pattern

Every factory accepts `**overrides` to customize fields:

```python
def make_error_message(**overrides):
    defaults = {
        "exception_type": "ValueError",
        "exception_value": "something went wrong",
    }
    defaults.update(overrides)
    return ErrorMessage(**defaults).model_dump()
```

For `make_sent_message`, the `content` override is a convenience shortcut that
sets the inner `UserMessage.content` field.

## Adding New Factories

When a new model type is introduced:

1. Add a factory function in `events.py` (for event/message types) or `models.py`
   (for CLI/server response models).
2. Follow the pattern: defaults dict, merge overrides, construct real model,
   return `model_dump()`.
3. Add round-trip tests in `test_fixture_factories.py`:
   - `test_round_trip_defaults` -- zero-arg call validates back to model
   - `test_round_trip_with_overrides` -- custom args validate back to model
   - `test_override_appears_in_output` -- override value present in dict
