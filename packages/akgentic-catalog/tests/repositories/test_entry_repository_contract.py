"""AC30, AC31, AC33: three-backend ``EntryRepository`` contract parity.

Parametrised on a ``backend`` fixture that yields one instance of each
concrete implementation — :class:`YamlEntryRepository`,
:class:`MongoEntryRepository`, and :class:`PostgresEntryRepository`. Every
parity scenario is backend-agnostic in its assertions (no
``if backend is ...`` branching). Scenarios that legitimately differ
between backends (e.g. Postgres-specific init_db semantics) live in
:mod:`test_postgres_repository` below.

Skip-clean discipline:

* The Mongo branch imports ``pymongo`` via ``pytest.importorskip`` and
  uses a mongomock-backed collection fixture inherited from the v2
  conftest — the test is skipped gracefully if the optional dep is
  missing.
* The Postgres branch uses a session-scoped
  :class:`testcontainers.postgres.PostgresContainer` and skips cleanly
  when ``nagra`` / ``psycopg`` / ``testcontainers.postgres`` are absent
  OR when Docker itself is unavailable (``try/except`` around
  ``.start()``).

Between-test isolation:

* YAML — ``tmp_path`` is function-scoped; nothing persists.
* Mongo — the ``entries_collection`` fixture is function-scoped; each
  test gets a fresh mongomock collection.
* Postgres — one container per session (start-up is expensive); a per-
  test ``TRUNCATE catalog_entries`` clears rows without dropping the
  schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.repositories.yaml import YamlEntryRepository

from ..v2.conftest import make_entry

if TYPE_CHECKING:
    import pymongo.collection


# Session-scoped ``postgres_dsn`` + function-scoped ``postgres_clean_dsn``
# fixtures are defined at the package-level ``tests/conftest.py`` so every
# sub-suite (api/cli/scripts/repositories) shares one container per session.


# --- Three-backend parametrised fixture ---


@pytest.fixture(params=["yaml", "mongo", "postgres"])
def backend(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
) -> EntryRepository:
    """Yield a fresh ``EntryRepository`` for each parametrised backend.

    The ordering ``["yaml", "mongo", "postgres"]`` is pinned so pytest
    reports are stable. The ``postgres`` branch activates the session-
    scoped ``postgres_clean_dsn`` fixture via ``request.getfixturevalue``
    so YAML + Mongo runs do not pay the testcontainer cost.
    """
    if request.param == "yaml":
        return YamlEntryRepository(tmp_path)
    if request.param == "mongo":
        pytest.importorskip("pymongo")
        from akgentic.catalog.repositories.mongo import MongoEntryRepository

        return MongoEntryRepository(entries_collection)
    if request.param == "postgres":
        dsn = request.getfixturevalue("postgres_clean_dsn")
        from akgentic.catalog.repositories.postgres import PostgresEntryRepository

        return PostgresEntryRepository(dsn)
    raise AssertionError(f"Unexpected backend parameter: {request.param}")


# --- Shared parity scenarios (AC30 — no backend-specific branching) ---


class TestEntryRepositoryContract:
    """Byte-identical behaviour across YAML + Mongo + Postgres backends."""

    def test_put_get_round_trip(self, backend: EntryRepository) -> None:
        """AC11, AC14: put → get returns an equal entry.

        Parity with the existing two-backend parity suite; extended to
        include Postgres.
        """
        entry = make_entry(
            id="a",
            kind="agent",
            namespace="ns-1",
            description="desc",
            payload={"k": "v"},
        )
        backend.put(entry)
        assert backend.get("ns-1", "a") == entry

    def test_upsert_overwrites_existing(self, backend: EntryRepository) -> None:
        """AC12: second put with same (namespace, id) replaces the first."""
        first = make_entry(id="a", kind="agent", namespace="ns-1", description="v1")
        second = make_entry(id="a", kind="agent", namespace="ns-1", description="v2")

        backend.put(first)
        backend.put(second)

        got = backend.get("ns-1", "a")
        assert got is not None
        assert got.description == "v2"

    def test_upsert_tolerates_kind_change_between_calls(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC12: kind changes between writes are tolerated — it's metadata, not PK."""
        first = make_entry(id="x", kind="agent", namespace="ns-1")
        second = make_entry(id="x", kind="tool", namespace="ns-1")

        backend.put(first)
        backend.put(second)

        got = backend.get("ns-1", "x")
        assert got is not None
        assert got.kind == "tool"
        # Exactly one row in the namespace with that id — the PK collapse held.
        assert [e.id for e in backend.list_by_namespace("ns-1")] == ["x"]

    def test_delete_is_idempotent_on_missing(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC13: delete on a missing (namespace, id) is a no-op."""
        backend.delete("ns-never", "nope")  # Must not raise
        # Still absent afterwards.
        assert backend.get("ns-never", "nope") is None

    def test_delete_removes_existing(self, backend: EntryRepository) -> None:
        """AC13: delete removes an existing row."""
        backend.put(make_entry(id="a", kind="agent", namespace="ns-1"))
        backend.delete("ns-1", "a")
        assert backend.get("ns-1", "a") is None

    def test_list_by_namespace_returns_every_entry(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC15: list_by_namespace returns every entry regardless of kind."""
        backend.put(make_entry(id="t1", kind="team", namespace="team-abc"))
        backend.put(make_entry(id="a1", kind="agent", namespace="team-abc"))
        backend.put(make_entry(id="a2", kind="agent", namespace="team-abc"))
        backend.put(make_entry(id="tool1", kind="tool", namespace="team-abc"))
        # Cross-namespace noise.
        backend.put(make_entry(id="other", kind="agent", namespace="team-xyz"))

        got = backend.list_by_namespace("team-abc")

        assert {e.id for e in got} == {"t1", "a1", "a2", "tool1"}

    def test_list_by_namespace_empty_returns_empty_list(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC15: empty namespace returns ``[]`` without raising."""
        assert backend.list_by_namespace("namespace-that-never-existed") == []

    def test_get_by_kind_returns_singleton(self, backend: EntryRepository) -> None:
        """AC16: get_by_kind returns the only entry of that kind in the namespace."""
        team = make_entry(id="team", kind="team", namespace="team-abc")
        backend.put(team)
        backend.put(make_entry(id="a1", kind="agent", namespace="team-abc"))

        got = backend.get_by_kind("team-abc", "team")

        assert got == team

    def test_get_by_kind_missing_returns_none(self, backend: EntryRepository) -> None:
        """AC16: get_by_kind returns ``None`` when no entry of that kind exists."""
        backend.put(make_entry(id="a1", kind="agent", namespace="team-abc"))
        assert backend.get_by_kind("team-abc", "team") is None

    def test_find_references_at_arbitrary_depth(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC17: find_references walks payloads at arbitrary depth."""
        backend.put(
            make_entry(
                id="a",
                kind="agent",
                namespace="ns-1",
                payload={"config": {"model_cfg": {"__ref__": "target"}}},
            )
        )
        backend.put(
            make_entry(
                id="b",
                kind="agent",
                namespace="ns-1",
                payload={"config": {"tools": [{"__ref__": "target"}]}},
            )
        )
        backend.put(
            make_entry(
                id="c",
                kind="agent",
                namespace="ns-1",
                payload={"config": {"model_cfg": {"__ref__": "other"}}},
            )
        )

        got = backend.find_references("ns-1", "target")

        assert {e.id for e in got} == {"a", "b"}

    def test_find_references_namespace_isolation(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC17 namespace isolation: refs in other namespaces are not inspected."""
        backend.put(
            make_entry(
                id="a",
                kind="agent",
                namespace="ns-1",
                payload={"__ref__": "target"},
            )
        )
        backend.put(
            make_entry(
                id="b",
                kind="agent",
                namespace="ns-2",
                payload={"__ref__": "target"},
            )
        )
        got = backend.find_references("ns-1", "target")
        assert [e.id for e in got] == ["a"]

    def test_namespace_isolation_same_id_different_namespace(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC14: same id in two namespaces coexists; no cross-namespace bleed."""
        a = make_entry(id="shared", kind="agent", namespace="ns-a", payload={"which": "a"})
        b = make_entry(id="shared", kind="agent", namespace="ns-b", payload={"which": "b"})

        backend.put(a)
        backend.put(b)

        assert backend.get("ns-a", "shared") == a
        assert backend.get("ns-b", "shared") == b
        assert [e.id for e in backend.list_by_namespace("ns-a")] == ["shared"]
        assert [e.id for e in backend.list_by_namespace("ns-b")] == ["shared"]

    def test_nested_ref_markers_round_trip(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC11: nested dicts and list elements with ``__ref__`` / ``__type__`` round-trip."""
        payload = {
            "config": {
                "tools": [
                    {"__ref__": "id_tool_a"},
                    {"__ref__": "id_tool_b", "__type__": "akgentic.tool.ToolCard"},
                ]
            }
        }
        entry = make_entry(id="agent", kind="agent", namespace="ns-1", payload=payload)

        backend.put(entry)
        got = backend.get("ns-1", "agent")

        assert got is not None
        assert got.payload == payload

    def test_user_id_and_user_id_set_combine_conjunctively(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC21: user_id + user_id_set evaluate as AND on all three backends."""
        backend.put(
            make_entry(id="alice", kind="agent", namespace="ns-1", user_id="alice", payload={})
        )
        backend.put(
            make_entry(id="bob", kind="agent", namespace="ns-1", user_id="anonymous", payload={})
        )

        # user_id="alice" AND user_id_set=True → alice satisfies both.
        got = backend.list(EntryQuery(namespace="ns-1", user_id="alice", user_id_set=True))
        assert {e.id for e in got} == {"alice"}
        # user_id="alice" AND user_id_set=False → contradiction; zero entries.
        got = backend.list(EntryQuery(namespace="ns-1", user_id="alice", user_id_set=False))
        assert got == []

    def test_meta_entry_public_field_round_trips_typed_bool(
        self,
        backend: EntryRepository,
    ) -> None:
        # Story 18.2 AC7 — ``public=True`` survives the byte-identical
        # write/read cycle as a typed Python bool across YAML / Mongo /
        # Postgres backends. The flag lives inside ``Entry.payload:
        # dict[str, Any]`` and round-trips verbatim via the existing
        # repository contract — no per-backend code change is required.
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="ns-public-rt",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            description="public namespace",
            payload={
                "name": "Tenant Pub",
                "description": "",
                "properties": {},
                "shareable": False,
                "public": True,
            },
        )
        backend.put(meta)
        loaded = backend.get("ns-public-rt", "_meta")
        assert loaded is not None
        assert isinstance(loaded.payload, dict)
        assert loaded.payload["public"] is True

    def test_list_filters_with_description_contains(
        self,
        backend: EntryRepository,
    ) -> None:
        """AC20: description_contains substring match works uniformly on all backends.

        Postgres' ILIKE is case-insensitive while YAML / Mongo use case-
        sensitive substring (per the YAML backend's ``_matches`` and the
        Mongo backend's ``re.escape`` without ``$options: 'i'``). This
        test uses an input whose case matches exactly in every entry so
        the case-sensitivity divergence is not exercised — cross-backend
        substring matching is the cross-cutting invariant.
        """
        backend.put(
            make_entry(
                id="a",
                kind="agent",
                namespace="ns-1",
                description="quick brown fox",
            )
        )
        backend.put(
            make_entry(
                id="b",
                kind="agent",
                namespace="ns-1",
                description="lazy dog",
            )
        )

        got = backend.list(EntryQuery(namespace="ns-1", description_contains="brown"))
        assert [e.id for e in got] == ["a"]


# --- Postgres-specific behaviours (AC8, AC25) ---


class TestPostgresSpecific:
    """Postgres-only tests that verify behaviours absent from YAML / Mongo."""

    def test_constructor_does_not_create_table(
        self,
        postgres_dsn: str,
    ) -> None:
        """AC8 + AC25: constructor on a database *without* the table is still schema-less.

        Uses the session DSN but drops the table first to simulate a fresh
        database, then verifies the repository constructor does not
        re-create it. ``init_db`` restores the table afterwards so
        subsequent parity tests keep running.
        """
        import psycopg

        from akgentic.catalog.repositories.postgres import (
            PostgresCatalogConfig,
            PostgresEntryRepository,
            init_db,
        )

        # 1) Drop the table so this test starts from an uninitialised state.
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS catalog_entries CASCADE")
            conn.commit()

        # 2) Construct the repository — per AC7/AC8 this must not touch the server.
        PostgresEntryRepository(postgres_dsn)

        # 3) Verify the table is still absent.
        with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.catalog_entries')")
            row = cur.fetchone()
            assert row is not None
            assert row[0] is None, (
                "PostgresEntryRepository constructor created catalog_entries; "
                "schema creation must live in init_db (AC8/AC25)."
            )

        # 4) Restore the schema so the rest of the session keeps working.
        init_db(PostgresCatalogConfig(connection_string=postgres_dsn))

    def test_init_db_is_idempotent(
        self,
        postgres_dsn: str,
    ) -> None:
        """AC24: init_db called twice against the same DB succeeds without destruction."""
        from akgentic.catalog.repositories.postgres import (
            PostgresCatalogConfig,
            PostgresEntryRepository,
            init_db,
        )

        config = PostgresCatalogConfig(connection_string=postgres_dsn)
        # Already applied once by the session fixture; call again.
        init_db(config)
        init_db(config)

        # The repository still works afterwards.
        repo = PostgresEntryRepository(postgres_dsn)
        entry = make_entry(id="idem-a", kind="agent", namespace="idem-ns")
        repo.put(entry)
        got = repo.get("idem-ns", "idem-a")
        assert got == entry
        # Cleanup for subsequent tests.
        repo.delete("idem-ns", "idem-a")

    def test_upsert_leaves_exactly_one_row(
        self,
        postgres_clean_dsn: str,
    ) -> None:
        """AC12 row-count verification: two writes with same PK collapse to one row."""
        import psycopg

        from akgentic.catalog.repositories.postgres import PostgresEntryRepository

        repo = PostgresEntryRepository(postgres_clean_dsn)
        repo.put(make_entry(id="r1", kind="agent", namespace="ns-count", description="v1"))
        repo.put(make_entry(id="r1", kind="agent", namespace="ns-count", description="v2"))

        with psycopg.connect(postgres_clean_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE namespace = %s AND id = %s",
                ("ns-count", "r1"),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == 1, f"expected exactly one row after upsert, got {row[0]}"

    def test_default_user_id_writes_anonymous_string_not_null(
        self,
        postgres_clean_dsn: str,
    ) -> None:
        """Story 18.1 / AC6 — entries with the default ``user_id`` persist as
        the literal string ``"anonymous"`` in the ``user_id`` column, not NULL.
        """
        import psycopg

        from akgentic.catalog.repositories.postgres import PostgresEntryRepository

        repo = PostgresEntryRepository(postgres_clean_dsn)
        repo.put(make_entry(id="anon-row", kind="tool", namespace="ns-anon-pg"))
        with psycopg.connect(postgres_clean_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM catalog_entries WHERE namespace = %s AND id = %s",
                ("ns-anon-pg", "anon-row"),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "anonymous"
            assert row[0] is not None

    def test_meta_payload_public_stored_as_jsonb_boolean(
        self,
        postgres_clean_dsn: str,
    ) -> None:
        # Story 18.2 AC7 — the JSONB ``payload`` column stores the literal
        # JSON ``true`` for the ``public`` key (not the string ``"true"``).
        # Verifies the typed-bool round-trip at the database level: a
        # JSONB ``->'public'`` extract surfaces as PostgreSQL's ``true``
        # literal (cast to text yields ``'true'``).
        import psycopg

        from akgentic.catalog.repositories.postgres import PostgresEntryRepository

        repo = PostgresEntryRepository(postgres_clean_dsn)
        repo.put(
            make_entry(
                id="_meta",
                kind="meta",
                namespace="ns-pg-public",
                user_id="alice",
                model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
                description="",
                payload={
                    "name": "Tenant",
                    "description": "",
                    "properties": {},
                    "shareable": False,
                    "public": True,
                },
            )
        )
        with psycopg.connect(postgres_clean_dsn) as conn, conn.cursor() as cur:
            # ``payload->'public'`` returns the JSONB literal; cast to text
            # yields the JSON serialisation. ``"true"`` here is the JSON
            # literal ``true`` rendered to text — NOT a stored string.
            cur.execute(
                "SELECT payload->'public' FROM catalog_entries WHERE namespace = %s AND id = %s",
                ("ns-pg-public", "_meta"),
            )
            row = cur.fetchone()
            assert row is not None
            # psycopg returns the JSONB extract as a Python bool when the
            # underlying JSON value is a boolean — the typed-bool contract
            # is preserved end-to-end.
            assert row[0] is True
            assert isinstance(row[0], bool)
