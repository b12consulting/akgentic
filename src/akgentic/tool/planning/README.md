# Planning Module

This module provides team planning management capabilities via an actor-based plan store.

## Architecture Note

**FUTURE REFACTORING**: This planning submodule has an explicit dependency on `akgentic-core`
(via imports of `ActorAddress`, `Akgent`, `BaseConfig`, `Orchestrator`, etc.). This violates
the original design goal where `akgentic-tool` had zero infrastructure dependencies.

The recommended approach is to extract the planning module into a separate package
(e.g., `akgentic-tool-planning` or incorporate it into `akgentic-team`) that explicitly
depends on `akgentic-core`. This would restore the clean separation and allow
`akgentic-tool` to remain infrastructure-agnostic for tools that don't require actor
system integration.

## Current Structure

- **planning.py**: `PlanningTool` - the main tool card that exposes planning capabilities
- **planning_actor.py**: `PlanActor` - the actor that manages plan state
- **event.py**: Empty module

## Dependencies

The planning module requires:
- `akgentic-core` for actor system integration (`ActorAddress`, `Akgent`, `Orchestrator`)
- `ActorToolObserver` protocol from parent `event.py` module

## Usage

Tools that need actor system features should use `ActorToolObserver` instead of the
basic `ToolObserver` protocol.
