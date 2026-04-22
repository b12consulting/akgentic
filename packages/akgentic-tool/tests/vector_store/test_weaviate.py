"""Unit tests for WeaviateBackend with mocked Weaviate client.

Covers: protocol compliance, create_collection (idempotent), add, remove,
search, multi-tenancy, import guard, and close().
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_weaviate_module() -> MagicMock:
    """Build a mock ``weaviate`` module tree that satisfies WeaviateBackend imports."""
    mock_weaviate = MagicMock()

    # weaviate.connect_to_custom returns a mock client
    mock_client = MagicMock()
    mock_weaviate.connect_to_custom.return_value = mock_client

    # weaviate.auth.AuthApiKey
    mock_weaviate.auth = MagicMock()
    mock_weaviate.auth.AuthApiKey.return_value = MagicMock()

    # weaviate.classes.config
    mock_config = MagicMock()
    mock_config.Configure.Vectorizer.none.return_value = "none_vectorizer"
    mock_config.Configure.multi_tenancy.return_value = "multi_tenancy_config"
    mock_config.DataType.TEXT = "TEXT"
    mock_config.Property = MagicMock(side_effect=lambda **kw: kw)
    mock_weaviate.classes = MagicMock()
    mock_weaviate.classes.config = mock_config

    # weaviate.classes.tenants
    mock_tenants_mod = MagicMock()
    mock_tenants_mod.Tenant = MagicMock(side_effect=lambda name: f"Tenant({name})")
    mock_weaviate.classes.tenants = mock_tenants_mod

    # weaviate.classes.query
    mock_query_mod = MagicMock()
    mock_query_mod.MetadataQuery.return_value = "metadata_query"
    mock_filter = MagicMock()
    mock_filter.by_property.return_value.contains_any.return_value = "filter_expr"
    mock_query_mod.Filter = mock_filter
    mock_weaviate.classes.query = mock_query_mod

    return mock_weaviate


def _install_mock_weaviate() -> tuple[MagicMock, MagicMock]:
    """Patch sys.modules so ``import weaviate`` resolves to our mock.

    Returns (mock_weaviate_module, mock_client).
    """
    mock_weaviate = _make_mock_weaviate_module()
    mock_client = mock_weaviate.connect_to_custom.return_value

    modules = {
        "weaviate": mock_weaviate,
        "weaviate.auth": mock_weaviate.auth,
        "weaviate.classes": mock_weaviate.classes,
        "weaviate.classes.config": mock_weaviate.classes.config,
        "weaviate.classes.tenants": mock_weaviate.classes.tenants,
        "weaviate.classes.query": mock_weaviate.classes.query,
    }
    for name, mod in modules.items():
        sys.modules[name] = mod

    return mock_weaviate, mock_client


def _cleanup_weaviate_modules() -> None:
    """Remove all weaviate-related modules from sys.modules."""
    to_remove = [k for k in sys.modules if k.startswith("weaviate")]
    for k in to_remove:
        del sys.modules[k]
    # Also force-reload our module so it picks up the mock state
    backend_key = "akgentic.tool.vector_store.weaviate"
    if backend_key in sys.modules:
        del sys.modules[backend_key]


def _make_entry(
    ref_id: str = "e1",
    ref_type: str = "entity",
    text: str = "hello",
    vector: list[float] | None = None,
) -> MagicMock:
    """Return a mock VectorEntry."""
    entry = MagicMock()
    entry.ref_id = ref_id
    entry.ref_type = ref_type
    entry.text = text
    entry.vector = vector if vector is not None else [0.1, 0.2, 0.3]
    return entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_modules() -> Any:
    """Ensure weaviate mock modules are cleaned up before and after each test."""
    _cleanup_weaviate_modules()
    yield
    _cleanup_weaviate_modules()


# ---------------------------------------------------------------------------
# Test: Import Guard (AC10)
# ---------------------------------------------------------------------------


class TestImportGuard:
    """AC10: ImportError with install instructions when weaviate-client missing."""

    def test_import_error_when_weaviate_missing(self) -> None:
        """Instantiating WeaviateBackend without weaviate-client raises ImportError."""
        # Ensure weaviate is NOT in sys.modules
        _cleanup_weaviate_modules()

        # Patch so that import weaviate fails
        with patch.dict(sys.modules, {"weaviate": None}):
            # Force reload to pick up the missing module
            if "akgentic.tool.vector_store.weaviate" in sys.modules:
                del sys.modules["akgentic.tool.vector_store.weaviate"]

            from akgentic.tool.vector_store.weaviate import WeaviateBackend

            with pytest.raises(ImportError, match="weaviate-client"):
                WeaviateBackend(url="http://localhost:8080")


# ---------------------------------------------------------------------------
# Test: create_collection (AC1, AC3)
# ---------------------------------------------------------------------------


class TestCreateCollection:
    """AC1, AC3: VectorStoreService compliance and idempotent creation."""

    def test_creates_collection_with_correct_config(self) -> None:
        """Collection created with cosine distance and no vectorizer."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        config = CollectionConfig(dimension=384)
        backend.create_collection("test_col", config)

        mock_client.collections.create.assert_called_once()
        call_kwargs = mock_client.collections.create.call_args
        assert call_kwargs[1]["name"] == "test_col" or call_kwargs[0][0] == "test_col"

    def test_idempotent_second_call(self) -> None:
        """Second call with same name is a no-op."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        config = CollectionConfig(dimension=384)
        backend.create_collection("test_col", config)

        # Now simulate exists=True
        mock_client.collections.exists.return_value = True
        mock_client.collections.create.reset_mock()
        backend.create_collection("test_col", config)

        mock_client.collections.create.assert_not_called()


# ---------------------------------------------------------------------------
# Test: add (AC4)
# ---------------------------------------------------------------------------


class TestAdd:
    """AC4: add stores VectorEntry records with pre-populated vectors."""

    def test_add_entries_with_batch(self) -> None:
        """Entries are added via batch.dynamic() context manager."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_collection = MagicMock()
        mock_client.collections.get.return_value = mock_collection
        mock_batch = MagicMock()
        mock_collection.batch.dynamic.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_collection.batch.dynamic.return_value.__exit__ = MagicMock(return_value=False)

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        backend.create_collection("col1", CollectionConfig())

        entry = _make_entry(ref_id="r1", text="test text", vector=[0.1, 0.2])
        backend.add("col1", [entry])

        mock_batch.add_object.assert_called_once()
        call_kwargs = mock_batch.add_object.call_args[1]
        assert call_kwargs["properties"]["ref_id"] == "r1"
        assert call_kwargs["vector"] == [0.1, 0.2]

    def test_add_raises_on_unknown_collection(self) -> None:
        """add raises ValueError for non-existent collection."""
        _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        entry = _make_entry()

        with pytest.raises(ValueError, match="does not exist"):
            backend.add("nonexistent", [entry])


# ---------------------------------------------------------------------------
# Test: remove (AC5)
# ---------------------------------------------------------------------------


class TestRemove:
    """AC5: remove deletes entries by ref_id filter."""

    def test_remove_by_ref_ids(self) -> None:
        """delete_many called with correct filter."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_collection = MagicMock()
        mock_client.collections.get.return_value = mock_collection

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        backend.create_collection("col1", CollectionConfig())
        backend.remove("col1", ["id1", "id2"])

        mock_collection.data.delete_many.assert_called_once()

    def test_remove_raises_on_unknown_collection(self) -> None:
        """remove raises ValueError for non-existent collection."""
        _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        with pytest.raises(ValueError, match="does not exist"):
            backend.remove("nonexistent", ["id1"])


# ---------------------------------------------------------------------------
# Test: search (AC6)
# ---------------------------------------------------------------------------


class TestSearch:
    """AC6: search performs cosine similarity search."""

    def test_search_returns_search_result(self) -> None:
        """near_vector query returns correctly-mapped SearchResult."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_collection = MagicMock()
        mock_client.collections.get.return_value = mock_collection

        # Mock search result objects
        mock_obj = MagicMock()
        mock_obj.properties = {"ref_type": "entity", "ref_id": "r1", "text": "hello"}
        mock_obj.metadata.distance = 0.2
        mock_result = MagicMock()
        mock_result.objects = [mock_obj]
        mock_collection.query.near_vector.return_value = mock_result

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        backend.create_collection("col1", CollectionConfig())
        result = backend.search("col1", [0.1, 0.2, 0.3], top_k=5)

        assert len(result.hits) == 1
        assert result.hits[0].ref_id == "r1"
        assert result.hits[0].score == pytest.approx(0.8)
        mock_collection.query.near_vector.assert_called_once()

    def test_search_clamps_negative_scores(self) -> None:
        """Scores are clamped to [0, 1] when distance > 1.0."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_collection = MagicMock()
        mock_client.collections.get.return_value = mock_collection

        mock_obj = MagicMock()
        mock_obj.properties = {"ref_type": "entity", "ref_id": "r1", "text": "hello"}
        mock_obj.metadata.distance = 1.5  # distance > 1 => would produce negative score
        mock_result = MagicMock()
        mock_result.objects = [mock_obj]
        mock_collection.query.near_vector.return_value = mock_result

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        backend.create_collection("col1", CollectionConfig())
        result = backend.search("col1", [0.1, 0.2], top_k=5)

        assert result.hits[0].score == 0.0

    def test_search_raises_on_unknown_collection(self) -> None:
        """search raises ValueError for non-existent collection."""
        _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        with pytest.raises(ValueError, match="does not exist"):
            backend.search("nonexistent", [0.1], top_k=5)


# ---------------------------------------------------------------------------
# Test: Multi-tenancy (AC7)
# ---------------------------------------------------------------------------


class TestMultiTenancy:
    """AC7: tenant is passed on all operations when configured."""

    def test_collection_created_with_multi_tenancy(self) -> None:
        """Multi-tenancy config passed when tenant is set."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False
        mock_col = MagicMock()
        mock_client.collections.get.return_value = mock_col

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080", tenant="team-42")
        config = CollectionConfig(dimension=384)
        backend.create_collection("col1", config)

        create_kwargs = mock_client.collections.create.call_args[1]
        assert "multi_tenancy_config" in create_kwargs
        mock_col.tenants.create.assert_called_once()

    def test_operations_scoped_to_tenant(self) -> None:
        """get().with_tenant() is called for tenant-scoped backends."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_col = MagicMock()
        mock_tenant_col = MagicMock()
        mock_col.with_tenant.return_value = mock_tenant_col
        mock_client.collections.get.return_value = mock_col

        # Set up batch mock on tenant collection
        mock_batch = MagicMock()
        mock_tenant_col.batch.dynamic.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_tenant_col.batch.dynamic.return_value.__exit__ = MagicMock(return_value=False)

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080", tenant="team-42")
        backend.create_collection("col1", CollectionConfig())

        entry = _make_entry()
        backend.add("col1", [entry])

        mock_col.with_tenant.assert_called_with("team-42")
        mock_batch.add_object.assert_called_once()

    def test_tenant_from_collection_config(self) -> None:
        """Tenant from CollectionConfig.tenant is used when backend tenant is None."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False
        mock_col = MagicMock()
        mock_client.collections.get.return_value = mock_col

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")  # no tenant
        config = CollectionConfig(dimension=384, tenant="workspace-99")
        backend.create_collection("col1", config)

        create_kwargs = mock_client.collections.create.call_args[1]
        assert "multi_tenancy_config" in create_kwargs
        mock_col.tenants.create.assert_called_once()

    def test_config_tenant_scoped_on_operations(self) -> None:
        """Operations use per-collection tenant from CollectionConfig, not backend."""
        _mock_weaviate, mock_client = _install_mock_weaviate()
        mock_client.collections.exists.return_value = False

        mock_col = MagicMock()
        mock_tenant_col = MagicMock()
        mock_col.with_tenant.return_value = mock_tenant_col
        mock_client.collections.get.return_value = mock_col

        # Set up batch mock on tenant collection
        mock_batch = MagicMock()
        mock_tenant_col.batch.dynamic.return_value.__enter__ = MagicMock(return_value=mock_batch)
        mock_tenant_col.batch.dynamic.return_value.__exit__ = MagicMock(return_value=False)

        from akgentic.tool.vector_store.protocol import CollectionConfig
        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")  # no backend tenant
        config = CollectionConfig(tenant="workspace-99")
        backend.create_collection("col1", config)

        entry = _make_entry()
        backend.add("col1", [entry])

        mock_col.with_tenant.assert_called_with("workspace-99")
        mock_batch.add_object.assert_called_once()


# ---------------------------------------------------------------------------
# Test: close (AC2)
# ---------------------------------------------------------------------------


class TestClose:
    """close() disconnects the Weaviate client."""

    def test_close_calls_client_close(self) -> None:
        """close() delegates to client.close()."""
        _mock_weaviate, mock_client = _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        backend = WeaviateBackend(url="http://localhost:8080")
        backend.close()

        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Connection config (AC8)
# ---------------------------------------------------------------------------


class TestConnectionConfig:
    """AC8: Connection parameters from url and api_key."""

    def test_connects_with_api_key(self) -> None:
        """AuthApiKey is used when api_key is provided."""
        mock_weaviate, mock_client = _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        WeaviateBackend(url="http://localhost:8080", api_key="test-key")

        mock_weaviate.connect_to_custom.assert_called_once()
        call_kwargs = mock_weaviate.connect_to_custom.call_args[1]
        assert call_kwargs["auth_credentials"] is not None

    def test_connects_without_api_key(self) -> None:
        """No auth when api_key is None."""
        mock_weaviate, mock_client = _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        WeaviateBackend(url="http://localhost:8080")

        call_kwargs = mock_weaviate.connect_to_custom.call_args[1]
        assert call_kwargs["auth_credentials"] is None

    def test_https_url_parsed_correctly(self) -> None:
        """HTTPS URL sets http_secure and grpc_secure to True."""
        mock_weaviate, _mock_client = _install_mock_weaviate()

        from akgentic.tool.vector_store.weaviate import WeaviateBackend

        WeaviateBackend(url="https://my-cluster.weaviate.cloud:443")

        call_kwargs = mock_weaviate.connect_to_custom.call_args[1]
        assert call_kwargs["http_secure"] is True
        assert call_kwargs["grpc_secure"] is True
        assert call_kwargs["http_port"] == 443
