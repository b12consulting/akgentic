"""EventStore implementations: YAML (default) and MongoDB (optional)."""

from __future__ import annotations

from akgentic.team.repositories.yaml import YamlEventStore

__all__: list[str] = [
    "YamlEventStore",
]

try:
    from akgentic.team.repositories.mongo import MongoEventStore  # noqa: F401

    __all__.append("MongoEventStore")
except ImportError:
    pass
