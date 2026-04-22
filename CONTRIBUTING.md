# Contributing to Akgentic v2

## Development Setup

1. Clone the repository
2. Install uv: https://docs.astral.sh/uv/getting-started/installation/
3. Install dependencies:
   ```bash
   cd akgentic-core
   uv sync --dev
   ```

## Code Quality Requirements

All contributions must pass these checks before merging:

### Type Checking (mypy)

```bash
uv run mypy src/
```

- Strict mode enabled (no untyped definitions allowed)
- All public APIs must have complete type annotations
- Use Python 3.12+ type syntax (`|` for unions, not `Optional`)

### Linting (ruff)

```bash
# Check for issues
uv run ruff check src/

# Auto-fix issues
uv run ruff check --fix src/
```

- Rules enforced: E, F, I, N, UP, ANN, ASYNC
- Line length: 100 characters

### Testing (pytest)

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/akgentic --cov-report=term-missing
```

- Minimum 80% code coverage required
- All public APIs must have unit tests

## Type Hint Guidelines

Use modern Python 3.12+ type syntax:

```python
# Correct
def get_agent(self, agent_id: str) -> ActorRef | None: ...
self.state_dict: dict[str, BaseState | dict] = {}

# Incorrect (old style)
from typing import Optional, Dict, Union
def get_agent(self, agent_id: str) -> Optional[ActorRef]: ...
```

## Docstring Style

Use Google-style docstrings for all public APIs:

```python
def create_agent(self, config: AgentConfig) -> ActorRef:
    """Create and start a new agent actor.

    Args:
        config: Configuration object for the agent

    Returns:
        Reference to the created actor

    Raises:
        ValueError: If config is invalid
    """
```

## Pull Request Checklist

Before submitting a PR:

- [ ] `uv run mypy src/` passes with zero errors
- [ ] `uv run ruff check src/` passes with zero violations
- [ ] `uv run pytest` passes with 80%+ coverage
- [ ] All public APIs have type hints
- [ ] All public APIs have Google-style docstrings
