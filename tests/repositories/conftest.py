"""Repository-layer test configuration with YAML writing helpers."""

from __future__ import annotations

from pathlib import Path

import yaml


def write_yaml(path: Path, data: object) -> None:
    """Write data as YAML to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
