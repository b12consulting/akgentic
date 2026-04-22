"""Centralised vector storage service — protocols, models, and configuration.

Re-exports all public types from ``protocol.py`` so consumers can import
directly from ``akgentic.tool.vector_store``.
"""

from __future__ import annotations

from akgentic.tool.vector_store.actor import (
    VS_ACTOR_NAME,
    VS_ACTOR_ROLE,
    VectorStoreActor,
    VectorStoreState,
)
from akgentic.tool.vector_store.embedding_actor import (
    EmbeddingActor,
    EmbeddingError,
    EmbeddingRequest,
    EmbeddingResult,
)
from akgentic.tool.vector_store.inmemory import InMemoryBackend

try:
    from akgentic.tool.vector_store.weaviate import WeaviateBackend
except ImportError:
    WeaviateBackend = None  # type: ignore[assignment,misc]
from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    EmbeddingProvider,
    SearchHit,
    SearchResult,
    VectorStoreConfig,
    VectorStoreService,
)
from akgentic.tool.vector_store.tool import VectorStoreTool

__all__ = [
    "CollectionConfig",
    "CollectionStatus",
    "EmbeddingActor",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResult",
    "InMemoryBackend",
    "WeaviateBackend",
    "SearchHit",
    "SearchResult",
    "VS_ACTOR_NAME",
    "VS_ACTOR_ROLE",
    "VectorStoreActor",
    "VectorStoreConfig",
    "VectorStoreService",
    "VectorStoreState",
    "VectorStoreTool",
]
