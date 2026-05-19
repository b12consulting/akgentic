"""Shared fixtures and factories for the v2 test suite.

Factory functions are plain helpers (not pytest fixtures) per the project
convention for stateless constructions. The one fixture-like helper
(``register_akgentic_test_module``) is a plain function that accepts a
``monkeypatch`` argument so test cleanup is handled by pytest's built-in
fixture teardown without additional bookkeeping in the test body.

The ``FakeEntryRepository`` class is a stateful, in-memory
``EntryRepository`` implementation used exclusively by Story 15.2 resolver
tests. It is a testing utility — concrete production repositories ship in
Stories 15.3 (YAML) and 15.4 (Mongo).
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.repositories.yaml import (
    YamlEntryRepository,
)
from akgentic.catalog.repositories.yaml import (
    _payload_has_cross_ns_ref as _payload_has_cross_ns_ref,
)
from akgentic.catalog.repositories.yaml import (
    _payload_has_ref as _payload_has_ref,
)

if TYPE_CHECKING:
    import pymongo.collection


_NAMESPACE_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def make_meta_entry(
    namespace: str,
    *,
    shareable: bool = True,
    public: bool = False,
    name: str | None = None,
    description: str = "",
    extra_properties: dict[str, str] | None = None,
    user_id: str = "anonymous",
) -> Entry:
    """Build a ``kind="meta"`` Entry with the canonical id ``"_meta"``.

    Used by cross-ns shareable-flag tests to opt the target namespace into
    being a cross-ns ref target. Story 17.7 / AC1 — ``shareable`` is a typed
    bool at the root of the meta payload. Story 18.2 — ``public`` is a typed
    bool at the root of the meta payload (default ``False``). The factory
    always emits the typed-bool shape; legacy-shape fixtures construct the
    payload by hand.
    """
    properties: dict[str, str] = {}
    if extra_properties:
        properties.update(extra_properties)
    payload: dict[str, Any] = {
        "name": name if name is not None else namespace,
        "description": description,
        "properties": properties,
        "shareable": shareable,
        "public": public,
    }
    return Entry(
        id="_meta",
        kind="meta",
        namespace=namespace,
        user_id=user_id,
        model_type=_NAMESPACE_META_TYPE,
        payload=payload,
    )


def make_entry(**overrides: Any) -> Entry:
    """Build a minimal valid ``Entry`` with sensible defaults, overridable by kwargs.

    Defaults model a fresh, community-tier (``user_id="anonymous"``) entry of
    kind ``tool`` pointing at a known-valid ``akgentic.*`` class. Tests pass
    keyword overrides for the attribute under test.
    """
    base: dict[str, Any] = {
        "id": "entry-1",
        "kind": "tool",
        "namespace": "ns-1",
        "model_type": "akgentic.core.agent_card.AgentCard",
        "description": "",
        "payload": {},
    }
    base.update(overrides)
    return Entry(**base)


def register_akgentic_test_module(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    **attributes: Any,
) -> str:
    """Register a throwaway module under ``sys.modules["akgentic.<suffix>"]``.

    Builds a ``types.ModuleType`` carrying every attribute passed as a kwarg,
    then installs it via ``monkeypatch.setitem`` so pytest's fixture teardown
    un-registers it after the test finishes.

    Args:
        monkeypatch: Pytest's ``monkeypatch`` fixture.
        suffix: The portion after ``"akgentic."`` used as the module name.
        **attributes: Names to attach to the module (classes, functions, …).

    Returns:
        The fully-qualified module name (``"akgentic.<suffix>"``) so tests can
        build class paths off it.
    """
    module_name = f"akgentic.{suffix}"
    module = types.ModuleType(module_name)
    for name, value in attributes.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module_name


@pytest.fixture
def entries_collection() -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide a fresh mongomock-backed ``catalog_entries`` collection per test.

    Builds an in-memory ``mongomock.MongoClient`` on demand so tests that do
    not touch Mongo pay no import cost. Each test gets an isolated collection
    — no cross-test state. ``pymongo`` is an optional dep per the package
    ``pyproject.toml``; ``mongomock`` ships under the ``dev`` extra.
    """
    import mongomock

    client = mongomock.MongoClient()
    return client["test_catalog"]["catalog_entries"]


class FakeEntryRepository:
    """In-memory ``EntryRepository`` for resolver + write-pipeline tests.

    Stateful by design — each test that mutates the store instantiates its
    own ``FakeEntryRepository()``. Satisfies the ``EntryRepository``
    structural protocol for every method the Story 15.2 resolver pipeline
    touches (``get``, ``put``, ``delete``, ``list_by_namespace``,
    ``get_by_kind``, ``find_references``). ``list`` raises
    ``NotImplementedError`` — no 15.2 code path consumes it.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Entry] = {}

    def get(self, namespace: str, id: str) -> Entry | None:
        return self._store.get((namespace, id))

    def put(self, entry: Entry) -> Entry:
        self._store[(entry.namespace, entry.id)] = entry
        return entry

    def delete(self, namespace: str, id: str) -> None:
        self._store.pop((namespace, id), None)

    def list(self, query: EntryQuery) -> list[Entry]:
        # Story 15.5 needs a minimal implementation so the Catalog service's
        # list() pass-through can be counted. Applies AND semantics over the
        # EntryQuery fields used by current tests; ignores filters not yet
        # exercised to keep the fake honest about its minimality.
        out: list[Entry] = list(self._store.values())
        if query.namespace is not None:
            out = [e for e in out if e.namespace == query.namespace]
        if query.kind is not None:
            out = [e for e in out if e.kind == query.kind]
        if query.id is not None:
            out = [e for e in out if e.id == query.id]
        if query.user_id:
            out = [e for e in out if e.user_id == query.user_id]
        if query.user_id_set is True:
            out = [e for e in out if e.user_id != "anonymous"]
        elif query.user_id_set is False:
            out = [e for e in out if e.user_id == "anonymous"]
        if query.description_contains is not None:
            out = [e for e in out if query.description_contains in e.description]
        return out

    def list_by_namespace(self, namespace: str) -> list[Entry]:
        return [e for (ns, _), e in self._store.items() if ns == namespace]

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        for (ns, _), e in self._store.items():
            if ns == namespace and e.kind == kind:
                return e
        return None

    def find_references(self, namespace: str, target_id: str) -> list[Entry]:
        out: list[Entry] = []
        for (ns, _), e in self._store.items():
            if ns == namespace and _payload_has_ref(e.payload, target_id):
                out.append(e)
        return out

    def find_references_global(self, namespace: str, target_id: str) -> list[Entry]:
        out: list[Entry] = []
        for _key, e in self._store.items():
            if _payload_has_cross_ns_ref(e.payload, namespace, target_id):
                out.append(e)
        return out


class CountingEntryRepository:
    """Decorator repository recording every method invocation.

    Wraps any ``EntryRepository`` (in practice a ``FakeEntryRepository`` or one
    of the production backends) and records each call into a public ``calls``
    list as ``(method_name, args, kwargs)`` tuples. Tests use this to assert
    "repository method X was called exactly once with arg Y" without touching
    the production repositories.

    The inner repository is accessible via ``inner`` for tests that need to
    seed state directly without polluting the call log. Call ``reset()`` to
    clear the recorded history (e.g. after seeding).
    """

    def __init__(self, inner: EntryRepository) -> None:
        self.inner: EntryRepository = inner
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def reset(self) -> None:
        """Clear the recorded call log."""
        self.calls = []

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append((name, args, kwargs))

    def get(self, namespace: str, id: str) -> Entry | None:
        self._record("get", (namespace, id), {})
        return self.inner.get(namespace, id)

    def put(self, entry: Entry) -> Entry:
        self._record("put", (entry,), {})
        return self.inner.put(entry)

    def delete(self, namespace: str, id: str) -> None:
        self._record("delete", (namespace, id), {})
        self.inner.delete(namespace, id)

    def list(self, query: EntryQuery) -> list[Entry]:
        self._record("list", (query,), {})
        return self.inner.list(query)

    def list_by_namespace(self, namespace: str) -> list[Entry]:
        self._record("list_by_namespace", (namespace,), {})
        return self.inner.list_by_namespace(namespace)

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        self._record("get_by_kind", (namespace, kind), {})
        return self.inner.get_by_kind(namespace, kind)

    def find_references(self, namespace: str, target_id: str) -> list[Entry]:
        self._record("find_references", (namespace, target_id), {})
        return self.inner.find_references(namespace, target_id)

    def find_references_global(self, namespace: str, target_id: str) -> list[Entry]:
        self._record("find_references_global", (namespace, target_id), {})
        return self.inner.find_references_global(namespace, target_id)

    def count(self, method_name: str) -> int:
        """Return the number of recorded calls to ``method_name``."""
        return sum(1 for name, _, _ in self.calls if name == method_name)


CatalogFactory = Callable[[], tuple[Catalog, EntryRepository]]


@pytest.fixture(params=["yaml", "mongo"], ids=["yaml", "mongo"])
def catalog_factory(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
) -> CatalogFactory:
    """Yield a factory producing ``(Catalog, repository)`` for both backends.

    Parametrised with ids ``yaml`` and ``mongo`` so pytest reports make the
    backend obvious. Every catalog-service behavioural test runs against both
    backends. The factory shape (callable rather than direct tuple) lets
    individual tests build multiple repositories if they ever need to (e.g. a
    src vs dst backend — though the current story does not need this).
    """

    def _make() -> tuple[Catalog, EntryRepository]:
        repo: EntryRepository
        if request.param == "yaml":
            repo = YamlEntryRepository(tmp_path)
        elif request.param == "mongo":
            pytest.importorskip("pymongo")
            from akgentic.catalog.repositories.mongo import (
                MongoEntryRepository,
            )

            repo = MongoEntryRepository(entries_collection)
        else:  # pragma: no cover — guarded by pytest.fixture params
            raise AssertionError(f"Unexpected backend param: {request.param}")
        return Catalog(repo), repo

    return _make


@pytest.fixture
def api_client(tmp_path: Path) -> tuple[Any, Catalog]:
    """Yield a ``(TestClient, Catalog)`` pair wired to a YAML-backed v2 router.

    The fixture is function-scoped; ``set_catalog`` is called fresh per test so
    the module-level ``_catalog`` in ``api/router.py`` cannot leak between
    tests. ``fastapi`` is guarded via ``importorskip`` inside the fixture body
    so this conftest module stays importable when the ``api`` extra is absent.

    Story 16.7: the generic ``/catalog/{kind}`` CRUD family is gated behind
    the ``expose_generic_kind_crud`` router setting (default ``False``).
    This fixture opts **in** to keep the existing integration tests that
    drive every route exercising the full surface. Tests that need to
    verify the default-off behaviour use ``api_client_kind_crud_hidden``.
    """
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from akgentic.catalog.api._errors import add_exception_handlers
    from akgentic.catalog.api._settings import CatalogRouterSettings
    from akgentic.catalog.api.router import build_router, set_catalog

    repo = YamlEntryRepository(tmp_path)
    catalog = Catalog(repo)

    app = FastAPI(title="Akgentic Catalog")
    app.include_router(build_router(CatalogRouterSettings(expose_generic_kind_crud=True)))
    set_catalog(catalog)
    add_exception_handlers(app)

    return TestClient(app), catalog


@pytest.fixture
def api_client_kind_crud_hidden(tmp_path: Path) -> tuple[Any, Catalog]:
    """Same as ``api_client`` but with ``expose_generic_kind_crud=False``.

    Used by Story 16.7 tests that assert the kind-generic CRUD family is
    hidden (404 / absent from OpenAPI) in the default community-tier build.
    """
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from akgentic.catalog.api._errors import add_exception_handlers
    from akgentic.catalog.api._settings import CatalogRouterSettings
    from akgentic.catalog.api.router import build_router, set_catalog

    repo = YamlEntryRepository(tmp_path)
    catalog = Catalog(repo)

    app = FastAPI(title="Akgentic Catalog")
    app.include_router(build_router(CatalogRouterSettings(expose_generic_kind_crud=False)))
    set_catalog(catalog)
    add_exception_handlers(app)

    return TestClient(app), catalog


@pytest.fixture
def counting_catalog() -> tuple[Catalog, CountingEntryRepository]:
    """Build a ``Catalog`` backed by a ``CountingEntryRepository`` around a Fake.

    Backend-agnostic — used by tests that need to assert on repository call
    counts (AC5 pass-throughs, AC33 load_team single-query, AC41 clone
    atomicity). Returns ``(Catalog, CountingEntryRepository)`` so tests can
    reach into ``.calls`` directly.
    """
    fake = FakeEntryRepository()
    # FakeEntryRepository.list raises NotImplementedError by design; extend it
    # here for the counting double by delegating to list_by_namespace when a
    # namespace filter is set. Tests that use counting_catalog only exercise
    # methods Fake actually supports, so we do not wire list semantics.
    counting = CountingEntryRepository(fake)
    return Catalog(counting), counting
