"""Tests for the ``_BlockScalarDumper`` subclass defined in ``repositories/yaml.py``.

Story 15.7: YAML writer uses block-scalar style for multi-line strings.
"""

from __future__ import annotations

import yaml

from akgentic.catalog.repositories.yaml import _BlockScalarDumper


def test_multiline_string_uses_block_scalar() -> None:
    """AC1: a multi-line ``str`` value is emitted as a ``|`` block scalar."""
    data = {
        "instructions": (
            "CRITICAL: Always keep the plan updated.\n"
            "Create tasks when your task involves other team members\n"
            "or is complex enough to require multiple steps."
        )
    }
    out = yaml.dump(
        data,
        Dumper=_BlockScalarDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    # The emitter should use a block-scalar header (`|` or `|-`), not a
    # single-quoted flow scalar.
    assert "instructions: |" in out
    assert "instructions: '" not in out
    # No blank lines should appear between the non-empty input lines.
    assert "\n\n" not in out.rstrip("\n")


def test_singleline_string_is_not_forced_to_block_scalar() -> None:
    """AC2: a single-line ``str`` value keeps PyYAML's default (plain) style."""
    data = {"name": "Planning"}
    out = yaml.dump(
        data,
        Dumper=_BlockScalarDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    # No block-scalar header for one-liners.
    assert "|" not in out
    assert out.strip() == "name: Planning"


def test_roundtrip_preserves_string_content() -> None:
    """AC3: mixed single/multi-line payloads survive dump → safe_load byte-faithfully."""
    data = {
        "single": "Planning",
        "multi": "line one\nline two\nline three",
        "trailing_newline": "ends with newline\n",
        "nested": {
            "block": "a\nb\nc",
            "plain": "short",
        },
    }
    out = yaml.dump(
        data,
        Dumper=_BlockScalarDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    reloaded = yaml.safe_load(out)
    assert reloaded == data
