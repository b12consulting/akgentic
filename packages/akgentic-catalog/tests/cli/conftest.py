"""Shared fixtures and helpers for CLI tests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for CI-safe assertions."""
    return ANSI_RE.sub("", text)


AGENT_CLASS = "akgentic.agent.BaseAgent"


def make_dirs(tmp_path: Path) -> None:
    """Create standard catalog subdirectories."""
    for name in ("templates", "tools", "agents", "teams"):
        (tmp_path / name).mkdir(exist_ok=True)


def agent_data(
    agent_id: str,
    role: str = "engineer",
    description: str = "Test agent",
    skills: list[str] | None = None,
) -> dict[str, Any]:
    """Build an agent entry dict suitable for YAML serialization."""
    return {
        "id": agent_id,
        "tool_ids": [],
        "card": {
            "role": role,
            "description": description,
            "skills": skills or ["coding"],
            "agent_class": AGENT_CLASS,
            "config": {"name": f"@{role}", "role": role},
            "routes_to": [],
        },
    }


def team_data(
    team_id: str,
    name: str = "Test Team",
    entry_point: str = "eng-mgr",
    members: list[dict[str, Any]] | None = None,
    description: str = "A test team",
) -> dict[str, Any]:
    """Build a team entry dict suitable for YAML serialization."""
    return {
        "id": team_id,
        "name": name,
        "entry_point": entry_point,
        "message_types": ["akgentic.core.messages.UserMessage"],
        "members": members or [{"agent_id": entry_point, "headcount": 1}],
        "agent_profiles": [],
        "description": description,
    }


def seed_agent(catalog_dir: Path, agent_id: str, **kwargs: Any) -> None:
    """Seed an agent entry directly in the catalog directory."""
    data = agent_data(agent_id, **kwargs)
    (catalog_dir / "agents" / f"{agent_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def seed_team(catalog_dir: Path, team_id: str, **kwargs: Any) -> None:
    """Seed a team entry directly in the catalog directory."""
    data = team_data(team_id, **kwargs)
    (catalog_dir / "teams" / f"{team_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )
