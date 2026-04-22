"""Weaviate vector store backend with multi-tenancy support.

Implements the ``VectorStoreService`` protocol by delegating all vector
storage operations to a Weaviate cluster via the ``weaviate-client`` v4 API.
Vectors are provided externally (no Weaviate-side vectoriser).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    SearchHit,
    SearchResult,
)

if TYPE_CHECKING:
    import weaviate
    import weaviate.collections

    from akgentic.tool.vector import VectorEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency guard
# ---------------------------------------------------------------------------

try:
    import weaviate as _weaviate  # noqa: F811, F401
except ImportError:
    _WEAVIATE_AVAILABLE = False
else:
    _WEAVIATE_AVAILABLE = True


def _check_weaviate_dependencies() -> None:
    """Validate that ``weaviate-client`` is installed.

    Raises:
        ImportError: With install instructions when ``weaviate-client`` is missing.
    """
    if not _WEAVIATE_AVAILABLE:
        msg = (
            "Weaviate backend requires the 'weaviate-client' package. "
            "Install with: pip install akgentic-tool[weaviate]"
        )
        raise ImportError(msg)


# ---------------------------------------------------------------------------
# WeaviateBackend
# ---------------------------------------------------------------------------


class WeaviateBackend:
    """Weaviate-backed vector store implementing ``VectorStoreService``.

    This is a plain Python class (not a Pydantic model) because it holds
    non-serialisable runtime state (the Weaviate client connection).
    It satisfies the ``VectorStoreService`` protocol structurally.

    Args:
        url: Weaviate cluster URL (e.g. ``http://localhost:8080``).
        api_key: Optional API key for authentication.
        tenant: Optional default tenant ID for multi-tenancy.
    """

    def __init__(self, url: str, api_key: str | None = None, tenant: str | None = None) -> None:
        _check_weaviate_dependencies()

        import weaviate as _wv
        from weaviate.auth import AuthApiKey

        self._tenant = tenant
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 8080)
        use_https = parsed.scheme == "https"

        # gRPC defaults: same host, port 50051
        grpc_port = 50051

        auth = AuthApiKey(api_key) if api_key else None
        self._client: weaviate.WeaviateClient = _wv.connect_to_custom(
            http_host=host,
            http_port=port,
            http_secure=use_https,
            grpc_host=host,
            grpc_port=grpc_port,
            grpc_secure=use_https,
            auth_credentials=auth,
        )
        self._collections_created: set[str] = set()
        self._collection_tenants: dict[str, str] = {}

    # ------------------------------------------------------------------
    # VectorStoreService protocol methods
    # ------------------------------------------------------------------

    def create_collection(self, name: str, config: CollectionConfig) -> None:
        """Create a named Weaviate collection. No-op if it already exists.

        When multi-tenancy is enabled (``self._tenant`` or ``config.tenant``
        is set), the collection is created with multi-tenancy and the tenant
        is provisioned.

        Args:
            name: Unique collection identifier.
            config: Collection configuration.
        """
        from weaviate.classes.config import Configure, DataType, Property
        from weaviate.classes.tenants import Tenant

        tenant = getattr(config, "tenant", None) or self._tenant

        if self._client.collections.exists(name):
            self._collections_created.add(name)
            if tenant:
                self._collection_tenants[name] = tenant
                try:
                    collection = self._client.collections.get(name)
                    collection.tenants.create([Tenant(name=tenant)])
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Tenant '%s' may already exist on collection '%s'",
                        tenant,
                        name,
                    )
            return

        properties = [
            Property(name="ref_type", data_type=DataType.TEXT),
            Property(name="ref_id", data_type=DataType.TEXT),
            Property(name="text", data_type=DataType.TEXT),
        ]
        mt_config = Configure.multi_tenancy(enabled=True) if tenant else None

        self._client.collections.create(
            name=name,
            vectorizer_config=Configure.Vectorizer.none(),
            properties=properties,
            multi_tenancy_config=mt_config,
        )
        self._collections_created.add(name)

        # Create the tenant after multi-tenant collection is created
        if tenant:
            self._collection_tenants[name] = tenant
            collection = self._client.collections.get(name)
            collection.tenants.create([Tenant(name=tenant)])

    def add(self, collection: str, entries: list[VectorEntry]) -> None:
        """Ingest embedding entries into a Weaviate collection.

        Uses batch insertion with pre-populated vectors.

        Args:
            collection: Target collection name.
            entries: List of vector entries to store.

        Raises:
            ValueError: If the collection has not been created.
        """
        self._check_collection(collection)
        col = self._get_collection(collection)

        with col.batch.dynamic() as batch:
            for entry in entries:
                batch.add_object(
                    properties={
                        "ref_type": entry.ref_type,
                        "ref_id": entry.ref_id,
                        "text": entry.text,
                    },
                    vector=entry.vector,
                )

    def remove(self, collection: str, ref_ids: list[str]) -> None:
        """Remove entries from a Weaviate collection by ref_id.

        Uses ``delete_many`` with a property filter on ``ref_id``.

        Args:
            collection: Target collection name.
            ref_ids: List of reference IDs to remove.

        Raises:
            ValueError: If the collection has not been created.
        """
        from weaviate.classes.query import Filter

        self._check_collection(collection)
        col = self._get_collection(collection)
        col.data.delete_many(
            where=Filter.by_property("ref_id").contains_any(ref_ids),
        )

    def search(
        self, collection: str, query_vector: list[float], top_k: int
    ) -> SearchResult:
        """Search a Weaviate collection by cosine similarity.

        Args:
            collection: Target collection name.
            query_vector: Query embedding vector.
            top_k: Maximum number of results to return.

        Returns:
            Search results with hits ranked by distance (converted to score).

        Raises:
            ValueError: If the collection has not been created.
        """
        from weaviate.classes.query import MetadataQuery

        self._check_collection(collection)
        col = self._get_collection(collection)

        result = col.query.near_vector(
            near_vector=query_vector,
            limit=top_k,
            return_metadata=MetadataQuery(distance=True),
        )

        hits: list[SearchHit] = []
        for obj in result.objects:
            props = obj.properties
            distance = obj.metadata.distance if obj.metadata and obj.metadata.distance else 0.0
            score = max(0.0, 1.0 - distance)
            hits.append(
                SearchHit(
                    ref_type=str(props.get("ref_type", "")),
                    ref_id=str(props.get("ref_id", "")),
                    text=str(props.get("text", "")),
                    score=score,
                )
            )

        return SearchResult(
            hits=hits,
            status=CollectionStatus.READY,
            indexing_pending=0,
        )

    def close(self) -> None:
        """Disconnect the Weaviate client."""
        self._client.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_collection(self, collection: str) -> None:
        """Raise ``ValueError`` if *collection* was never created via this backend.

        Args:
            collection: Collection name to validate.

        Raises:
            ValueError: If the collection has not been created.
        """
        if collection not in self._collections_created:
            msg = f"Collection '{collection}' does not exist"
            raise ValueError(msg)

    def _get_collection(self, name: str) -> weaviate.collections.Collection:
        """Return the Weaviate collection handle, with tenant if applicable.

        Resolves the effective tenant from the per-collection mapping first,
        falling back to the backend-level default tenant.

        Args:
            name: Collection name.

        Returns:
            Weaviate collection object (optionally scoped to tenant).
        """
        col = self._client.collections.get(name)
        tenant = self._collection_tenants.get(name) or self._tenant
        if tenant:
            col = col.with_tenant(tenant)
        return col
