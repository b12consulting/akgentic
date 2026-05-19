"""AC29: ``schema.toml`` is discoverable as a packaged resource.

The Nagra schema file must ship alongside the code at install time so the
``init_db`` / ``PostgresEntryRepository.__init__`` paths can read it from a
runtime-discoverable location. ``importlib.resources`` is the native API
for this — the test asserts the file resolves and is a non-empty readable
resource under the ``akgentic.catalog.repositories.postgres`` package.

Regressions the test guards against: forgetting to re-add the
``[tool.hatch.build.targets.wheel.force-include]`` stanza after a
``pyproject.toml`` edit, or relocating ``schema.toml`` out of the package
directory.
"""

from __future__ import annotations

from importlib.resources import files


def test_schema_toml_is_discoverable_under_package() -> None:
    """AC29: ``schema.toml`` resolves relative to the postgres package.

    The resource must be a real file (not a stubby traversable) and must
    contain a Nagra table declaration (``[catalog_entries]`` header)
    — verifies the file is non-empty and structurally recognisable.
    """
    resource = files("akgentic.catalog.repositories.postgres").joinpath("schema.toml")

    assert resource.is_file(), f"schema.toml not found at {resource}"
    contents = resource.read_text(encoding="utf-8")
    assert "[catalog_entries]" in contents, (
        "schema.toml does not contain the [catalog_entries] section header; the file "
        "has been replaced with a shape Nagra cannot load."
    )
