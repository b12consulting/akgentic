# Using the Akgentic Template

This guide shows you how to create a new Akgentic module using the template.

## Quick Start: Create a New Module

Let's say you want to create a new module called `akgentic-storage`.

### Step 1: Copy the Template

```bash
# From workspace root
cd packages
cp -r akgentic-template akgentic-storage
cd akgentic-storage
```

### Step 2: Rename the Module Directory

```bash
# Rename the 'template' directory to your module name
mv src/akgentic/template src/akgentic/storage
```

### Step 3: Update pyproject.toml

Edit `pyproject.toml` and update:

```toml
[project]
name = "akgentic-storage"  # Change from akgentic-<module>
version = "0.1.0"
description = "Storage abstractions for Akgentic agents"  # Your description
requires-python = ">=3.12"
authors = [
    { name = "gpiroux", email = "geoffroy.piroux@weareyuma.com" }
]
keywords = ["agents", "ai", "akgentic", "storage"]

dependencies = [
    "pydantic>=2.0.0",
    "akgentic",           # If you need core actor framework
    # "akgentic-llm",     # If you need LLM integration
]

# If you have workspace dependencies, uncomment:
# [tool.uv.sources]
# akgentic = { workspace = true }
# akgentic-llm = { workspace = true }
```

### Step 4: Update README.md

Replace all instances of `<Module>` and `<module>` with your module name:

```bash
# Use sed or your editor (macOS version shown)
sed -i '' 's/<Module>/Storage/g' README.md
sed -i '' 's/<module>/storage/g' README.md
```

Then fill in all bracketed placeholders `[...]`:
- Status badge
- "What is" description
- Design Principles
- Quick Start example
- Core Concepts
- API Reference with your classes
- Examples table and code

### Step 5: Update Module __init__.py

Edit `src/akgentic/storage/__init__.py`:

```python
"""Akgentic Storage Module - Storage abstractions for Akgentic agents.

Provides unified storage interfaces for agents, supporting multiple backends.
"""

__version__ = "0.1.0"

from akgentic.storage.interface import StorageInterface
from akgentic.storage.memory import MemoryStorage
from akgentic.storage.file import FileStorage

__all__ = ["StorageInterface", "MemoryStorage", "FileStorage"]
```

### Step 6: Add to Workspace

Edit the root `pyproject.toml` and add your module to workspace members:

```toml
[tool.uv.workspace]
members = [
    "packages/akgentic-core",
    "packages/akgentic-llm",
    "packages/akgentic-team",
    "packages/akgentic-tool",
    "packages/akgentic-storage",  # Add your module here
]
```

### Step 7: Install in Workspace

```bash
# From workspace root
uv sync
```

### Step 8: Verify Installation

```bash
# From workspace root
source .venv/bin/activate
python -c "from akgentic.storage import StorageInterface; print('✅ Module installed')"
```

### Step 9: Implement Your Module

Now implement your actual module code:

1. **Create your main files** in `src/akgentic/storage/`:
   - `interface.py` - Abstract base classes
   - `memory.py` - In-memory implementation
   - `file.py` - File-based implementation
   - etc.

2. **Write tests** in `tests/`:
   - `test_interface.py`
   - `test_memory.py`
   - `test_file.py`
   - etc.

3. **Add examples** in `examples/`:
   - `example_basic.py`
   - `example_advanced.py`
   - Update `examples/README.md`

4. **Run quality checks**:
   ```bash
   pytest                                    # Run tests
   pytest --cov=src/akgentic/storage        # Check coverage
   mypy src/                                # Type checking
   ruff format src/ tests/                  # Format code
   ruff check src/ tests/                   # Lint code
   ```

## Template Files Explained

### Structure

```
akgentic-template/
├── .gitignore                 # Standard Python gitignore
├── pyproject.toml            # Package configuration template
├── README.md                 # README template with all sections
├── README-TEMPLATE-GUIDE.md  # This guide about the template itself
├── USAGE-GUIDE.md            # Usage instructions (this file)
│
├── src/akgentic/
│   ├── __init__.py           # Namespace package marker
│   └── template/
│       ├── __init__.py       # Module __init__ with placeholder
│       └── py.typed          # Type hints marker
│
├── tests/
│   ├── __init__.py
│   └── test_example.py       # Example test file
│
└── examples/
    └── README.md             # Examples directory README
```

### Key Files to Modify

1. **pyproject.toml** - Update name, description, dependencies
2. **README.md** - Fill in all sections with your module details
3. **src/akgentic/template/** - Rename to your module name
4. **src/akgentic/template/__init__.py** - Update exports and docstring

### Key Files to Keep As-Is

1. **src/akgentic/__init__.py** - Namespace marker (don't modify)
2. **py.typed** - Type hints marker (don't modify)
3. **.gitignore** - Standard ignores (can extend if needed)

## README Template Compliance

Your README should follow the standard template structure (based on akgentic-core):

✅ **Required Sections:**
1. Package Title & Status
2. "What is" Description
3. Quick Start
4. Design Principles
5. Installation (standalone + monorepo)
6. Development (standalone + monorepo workflows)
7. Examples (with table and learning path)
8. Core Concepts
9. API Reference
10. Integration with Other Modules
11. Architecture
12. Dependencies
13. Migration (if applicable)

See [README-TEMPLATE-GUIDE.md](README-TEMPLATE-GUIDE.md) for detailed guidance on each section.

## Best Practices

### Module Naming

- Use lowercase with hyphens: `akgentic-storage`
- Directory name matches package name
- Python package name uses underscores if needed: `akgentic.storage`

### Code Organization

```
src/akgentic/<module>/
├── __init__.py          # Public API exports
├── config.py            # Configuration models (if needed)
├── interface.py         # Abstract base classes (if needed)
├── implementation.py    # Main implementation
├── utils.py             # Utilities
└── py.typed             # Type hints marker
```

### Testing Organization

```
tests/
├── __init__.py
├── test_config.py       # Match src/ structure
├── test_interface.py
├── test_implementation.py
└── test_utils.py
```

### Coverage Requirements

- Minimum 80% coverage (inherited from root workspace)
- Run coverage check: `pytest --cov=src/akgentic/<module> --cov-report=html`
- Review coverage report in `htmlcov/index.html`

### Type Hints Requirements

- All public APIs must have type hints
- Run type checker: `mypy src/`
- Strict mode enabled in pyproject.toml

### Documentation Requirements

- All public APIs must have docstrings
- Follow Google-style docstrings
- Include examples in docstrings when helpful

## Checklist for New Modules

Use this checklist when creating a new module:

### Initial Setup
- [ ] Copy template to new directory
- [ ] Rename module directory
- [ ] Update pyproject.toml (name, description, dependencies)
- [ ] Update README.md (replace `<module>`, fill sections)
- [ ] Update module __init__.py (exports, docstring)
- [ ] Add to workspace members in root pyproject.toml
- [ ] Run `uv sync`

### Implementation
- [ ] Implement core functionality
- [ ] Add comprehensive type hints
- [ ] Write docstrings for all public APIs
- [ ] Create unit tests
- [ ] Achieve 80%+ test coverage
- [ ] Pass mypy type checking
- [ ] Format with ruff
- [ ] Pass ruff linting

### Documentation
- [ ] Complete all required README sections
- [ ] Add working code examples
- [ ] Document integration patterns
- [ ] Add troubleshooting section
- [ ] Create examples/README.md
- [ ] Add example scripts

### Quality Checks
- [ ] `pytest` - All tests pass
- [ ] `pytest --cov` - Coverage ≥ 80%
- [ ] `mypy src/` - No type errors
- [ ] `ruff format` - Code formatted
- [ ] `ruff check` - No lint errors
- [ ] Examples run successfully
- [ ] README links are valid
- [ ] Dependencies match pyproject.toml

### Integration
- [ ] Test imports in isolation
- [ ] Test imports in workspace
- [ ] Test integration with other modules
- [ ] Update root README if needed
- [ ] Update architecture docs if needed

## Getting Help

If you have questions about the template:

1. Review [README-TEMPLATE-GUIDE.md](README-TEMPLATE-GUIDE.md)
2. Look at existing packages as examples:
   - [akgentic-core](../akgentic-core/) - Core implementation
   - [akgentic-llm](../akgentic-llm/) - LLM integration
   - [akgentic-team](../akgentic-team/) - Team coordination
3. Check the architecture docs in `_bmad-output/planning-artifacts/`
4. Ask the architect (Winston) or dev team

---

**Winston (Architect)** 🏗️  
Template Usage Guide - Making it easy to create new Akgentic modules
