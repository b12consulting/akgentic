"""Test that vector_store.__all__ matches actual exports (AC10)."""

from __future__ import annotations

import akgentic.tool.vector_store as vs


class TestPublicApi:
    """Validate vector_store public API re-exports."""

    def test_all_matches_actual_exports(self) -> None:
        """Every name in __all__ must be importable from the package."""
        for name in vs.__all__:
            assert hasattr(vs, name), f"{name} listed in __all__ but not importable"

    def test_expected_names_in_all(self) -> None:
        """All expected public types are re-exported."""
        expected = {
            "CollectionConfig",
            "CollectionStatus",
            "EmbeddingActor",
            "EmbeddingError",
            "EmbeddingProvider",
            "EmbeddingRequest",
            "EmbeddingResult",
            "InMemoryBackend",
            "SearchHit",
            "SearchResult",
            "VS_ACTOR_NAME",
            "VS_ACTOR_ROLE",
            "VectorStoreActor",
            "VectorStoreConfig",
            "VectorStoreService",
            "VectorStoreState",
            "VectorStoreTool",
            "WeaviateBackend",
        }
        assert set(vs.__all__) == expected
