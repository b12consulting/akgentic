"""Skip-gate for the Postgres-specific test directory.

The actual ``postgres_container`` / ``postgres_conn_string`` /
``postgres_initialized`` / ``postgres_clean_tables`` fixtures live in
the parent ``tests/repositories/conftest.py`` so that the shared
``TestEventStoreContract`` parametrized suite can compose them. Tests
under this directory inherit those fixtures through normal pytest
fixture resolution. This module only exists to skip the entire directory
cleanly when ``nagra`` or ``testcontainers[postgres]`` is missing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")
