# Contributing to Akgentic

Thank you for your interest in contributing! This guide covers everything you need to get started — from forking the repos to opening a pull request.

## Repository Structure

Akgentic is a **monorepo of submodules**. Each package lives in its own GitHub repository under `b12consulting/`:

| Package | Repository |
|---------|-----------|
| akgentic-core | github.com/b12consulting/akgentic-core |
| akgentic-llm | github.com/b12consulting/akgentic-llm |
| akgentic-tool | github.com/b12consulting/akgentic-tool |
| akgentic-agent | github.com/b12consulting/akgentic-agent |
| akgentic-catalog | github.com/b12consulting/akgentic-catalog |
| akgentic-team | github.com/b12consulting/akgentic-team |
| akgentic-infra | github.com/b12consulting/akgentic-infra |

Contributions targeting a specific package should be made against **that package's repository**, not this quick-start repo.

---

## Getting Started (External Contributors)

### 1. Fork the target repository

Go to the repository you want to contribute to (e.g. `github.com/b12consulting/akgentic-core`) and click **Fork**.

### 2. Clone the quick-start with submodules

```bash
git clone https://github.com/b12consulting/akgentic-quick-start.git
cd akgentic-quick-start
git submodule update --init --recursive
```

### 3. Point the submodule at your fork

```bash
cd packages/akgentic-core          # replace with your target package
git remote add fork https://github.com/<your-username>/akgentic-core.git
git fetch fork
```

### 4. Set up the workspace

```bash
uv venv
source .venv/bin/activate
uv sync --all-packages --all-extras
```

---

## Workflow

### Every contribution must be linked to an issue

**Never open a PR without an associated GitHub issue.** If one doesn't exist yet, create it first.

### Branch naming

```
<type>/<issue-number>-<short-description>
```

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code restructuring without behaviour change |
| `chore` | Tooling, CI, dependency updates |
| `test` | Tests only |

**Examples:**
```
feat/42-actor-lifecycle
fix/17-routing-deadlock
docs/31-update-readme
```

Create the branch inside the submodule directory:

```bash
cd packages/akgentic-core
git checkout -b feat/42-actor-lifecycle
```

---

## Code Quality Requirements

All contributions must pass these checks before merging:

### Type checking (mypy)

```bash
uv run mypy packages/akgentic-core/src/
```

- Strict mode — no untyped definitions allowed
- All public APIs must have complete type annotations
- Use Python 3.12+ syntax (`X | Y` not `Optional[X]`)

### Linting (ruff)

```bash
uv run ruff check packages/akgentic-core/src/
uv run ruff check --fix packages/akgentic-core/src/   # auto-fix
```

- Rules: E, F, I, N, UP, ANN, ASYNC, C901, PLR0912, PLR0915
- Line length: 100 characters

### Tests (pytest)

```bash
uv run pytest packages/akgentic-core/tests/
uv run pytest packages/akgentic-core/tests/ --cov     # with coverage
```

- Minimum 80% coverage (enforced)
- All public APIs must have unit tests

---

## Type Hint Guidelines

Use modern Python 3.12+ syntax:

```python
# Correct
def get_agent(self, agent_id: str) -> ActorRef | None: ...

# Incorrect
from typing import Optional
def get_agent(self, agent_id: str) -> Optional[ActorRef]: ...
```

## Docstring Style

Google-style docstrings on all public APIs:

```python
def create_agent(self, config: AgentConfig) -> ActorRef:
    """Create and start a new agent actor.

    Args:
        config: Configuration object for the agent.

    Returns:
        Reference to the created actor.

    Raises:
        ValueError: If config is invalid.
    """
```

---

## Commit Standards

- **Conventional Commits:** `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`
- **DCO sign-off required:** use `git commit -s`
- **One commit, one responsibility** — never mix feature work with formatting or unrelated fixes
- **Link the issue:** include `Relates to #<issue>` or `Closes #<issue>` in the commit body

```bash
git commit -s -m "feat: add actor lifecycle hook

Implement on_start() override support for subclass initialization.

Closes #42"
```

---

## Opening a Pull Request

Push your branch to **your fork**:

```bash
git push fork feat/42-actor-lifecycle
```

Then open a PR from your fork's branch to the upstream `master` branch of the target package repository.

### PR checklist

- [ ] Linked to a GitHub issue
- [ ] Branch follows `<type>/<issue-number>-<short-description>` convention
- [ ] `mypy` passes with zero errors
- [ ] `ruff check` passes with zero violations
- [ ] `pytest` passes with 80%+ coverage
- [ ] All public APIs have type hints and Google-style docstrings
- [ ] Commits are signed off (`git commit -s`)
- [ ] CI is green before requesting review

### PR rules

- **Never push directly to `master`**
- **Never mention AI assistants** in PR descriptions or commit messages
- **Do not include test plans or effort estimates** in PR descriptions
- PRs are merged only after CI passes and at least one review approval

---

## License

By contributing, you agree that your contributions will be licensed under the same terms as this project. See [LICENSE](LICENSE) for details.
