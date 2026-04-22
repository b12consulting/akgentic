"""Vector store protocol definitions, data models, and configuration.

Defines the structural contracts (``VectorStoreService``, ``EmbeddingProvider``)
and Pydantic models (``CollectionConfig``, ``SearchHit``, ``SearchResult``,
``VectorStoreConfig``) for the centralised vector storage service.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field

from akgentic.core.agent_config import BaseConfig
from akgentic.core.utils.serializer import SerializableBaseModel

if TYPE_CHECKING:
    from akgentic.tool.vector import VectorEntry


# ---------------------------------------------------------------------------
# CollectionStatus
# ---------------------------------------------------------------------------


class CollectionStatus(StrEnum):
    """Lifecycle state of a vector collection."""

    READY = "ready"
    INDEXING = "indexing"
    ERROR = "error"


# ---------------------------------------------------------------------------
# CollectionConfig
# ---------------------------------------------------------------------------


class CollectionConfig(SerializableBaseModel):
    """Configuration for a single vector collection.

    Controls the embedding dimensionality, storage backend, and persistence
    strategy for the collection.
    """

    dimension: int = Field(default=1536, ge=1, description="Embedding vector dimensionality")
    backend: Literal["inmemory", "weaviate"] = Field(
        default="inmemory", description="Storage backend for this collection"
    )
    persistence: Literal["actor_state", "workspace"] = Field(
        default="actor_state", description="Persistence mode (inmemory backend only)"
    )
    workspace_path: str | None = Field(
        default=None, description="Filesystem path when persistence is workspace"
    )
    tenant: str | None = Field(
        default=None,
        description="Weaviate tenant ID for multi-tenancy (maps to workspace/team ID)",
    )


# ---------------------------------------------------------------------------
# SearchHit
# ---------------------------------------------------------------------------


class SearchHit(SerializableBaseModel):
    """A single result from a vector similarity search.

    References the source object via ``ref_type`` and ``ref_id`` with the
    original text and cosine similarity ``score``.
    """

    ref_type: str = Field(description="Domain-specific type label for the referenced object")
    ref_id: str = Field(description="Identifier of the referenced object")
    text: str = Field(description="The text that was embedded")
    score: float = Field(description="Cosine similarity score")


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class SearchResult(SerializableBaseModel):
    """Aggregated search response from the vector store.

    Contains the ranked list of ``SearchHit`` items together with collection
    status metadata.
    """

    hits: list[SearchHit] = Field(description="Ranked search results")
    status: CollectionStatus = Field(description="Current collection lifecycle state")
    indexing_pending: int = Field(
        default=0, ge=0, description="Number of entries still being indexed"
    )


# ---------------------------------------------------------------------------
# EmbeddingProvider (Protocol)
# ---------------------------------------------------------------------------


class EmbeddingProvider(Protocol):
    """Structural contract for embedding text into vectors.

    Any class that implements an ``embed`` method with the correct signature
    satisfies this protocol via structural subtyping.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts and return one vector per input.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors, one per input text.
        """
        ...


# ---------------------------------------------------------------------------
# VectorStoreService (Protocol)
# ---------------------------------------------------------------------------


class VectorStoreService(Protocol):
    """Structural contract for a centralised vector storage backend.

    Implementations manage named collections, handle ingestion, removal,
    and similarity search without exposing backend details.
    """

    def create_collection(self, name: str, config: CollectionConfig) -> None:
        """Create or reconfigure a named collection.

        Args:
            name: Unique collection identifier.
            config: Collection configuration.
        """
        ...

    def add(self, collection: str, entries: list[VectorEntry]) -> None:
        """Ingest embedding entries into a collection.

        Args:
            collection: Target collection name.
            entries: List of vector entries to store.
        """
        ...

    def remove(self, collection: str, ref_ids: list[str]) -> None:
        """Remove entries from a collection by reference ID.

        Args:
            collection: Target collection name.
            ref_ids: List of reference IDs to remove.
        """
        ...

    def search(
        self, collection: str, query_vector: list[float], top_k: int
    ) -> SearchResult:
        """Search a collection by cosine similarity.

        Args:
            collection: Target collection name.
            query_vector: Query embedding vector.
            top_k: Maximum number of results to return.

        Returns:
            Search results with hits and collection status.
        """
        ...


# ---------------------------------------------------------------------------
# VectorStoreConfig
# ---------------------------------------------------------------------------


class VectorStoreConfig(BaseConfig):
    """Configuration for the vector store actor.

    Specifies the embedding model, provider, and optional Weaviate connection
    details.
    """

    embedding_model: str = Field(
        default="text-embedding-3-small", description="Embedding model identifier"
    )
    embedding_provider: Literal["openai", "azure"] = Field(
        default="openai", description="Embedding API provider"
    )
    weaviate_url: str | None = Field(
        default=None, description="Weaviate cluster URL"
    )
    weaviate_api_key: str | None = Field(
        default=None, description="Weaviate API key"
    )
