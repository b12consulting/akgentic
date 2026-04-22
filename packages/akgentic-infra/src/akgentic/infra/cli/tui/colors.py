"""Agent color assignment for TUI conversation widgets."""

from __future__ import annotations


class AgentColorRegistry:
    """Round-robin agent color assignment."""

    _PALETTE: list[str] = ["cyan", "green", "magenta", "yellow", "blue", "red"]

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._idx: int = 0

    def get(self, agent_name: str) -> str:
        """Return a consistent color for an agent (round-robin assignment)."""
        if agent_name not in self._map:
            self._map[agent_name] = self._PALETTE[self._idx % len(self._PALETTE)]
            self._idx += 1
        return self._map[agent_name]

    def reset(self) -> None:
        """Clear all assignments (e.g., on team switch)."""
        self._map.clear()
        self._idx = 0
