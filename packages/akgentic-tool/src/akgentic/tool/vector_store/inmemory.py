"""In-memory vector store backend with per-collection VectorIndex management.

Implements the ``VectorStoreService`` protocol using numpy-backed
``VectorIndex`` instances. Supports two persistence modes:

- **actor_state**: serialisable snapshot for orchestrator-driven persistence.
- **workspace**: numpy/JSON persistence to the filesystem.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from akgentic.tool.vector import VectorEntry, VectorIndex, _check_vector_search_dependencies
from akgentic.tool.vector_store.protocol import (
    CollectionConfig,
    CollectionStatus,
    SearchHit,
    SearchResult,
)

logger = logging.getLogger(__name__)


class InMemoryBackend:
    """In-memory vector store managing one ``VectorIndex`` per collection.

    This is a plain Python class (not a Pydantic model) because it holds
    non-serialisable runtime state (numpy arrays inside ``VectorIndex``).
    It satisfies the ``VectorStoreService`` protocol structurally.

    Args:
        None. Instantiation validates that ``[vector_search]`` extras are
        installed.
    """

    def __init__(self) -> None:
        _check_vector_search_dependencies()
        self._collections: dict[str, VectorIndex] = {}
        self._configs: dict[str, CollectionConfig] = {}

    # ------------------------------------------------------------------
    # VectorStoreService protocol methods
    # ------------------------------------------------------------------

    def create_collection(self, name: str, config: CollectionConfig) -> None:
        """Create a named collection. No-op if the collection already exists.

        Args:
            name: Unique collection identifier.
            config: Collection configuration.
        """
        if name in self._collections:
            return
        self._collections[name] = VectorIndex()
        self._configs[name] = config

    def add(self, collection: str, entries: list[VectorEntry]) -> None:
        """Ingest embedding entries into a collection.

        Args:
            collection: Target collection name.
            entries: List of vector entries to store.

        Raises:
            ValueError: If the collection does not exist.
        """
        index = self._get_index(collection)
        for entry in entries:
            index.add(entry)

    def remove(self, collection: str, ref_ids: list[str]) -> None:
        """Remove entries from a collection by reference ID.

        Args:
            collection: Target collection name.
            ref_ids: List of reference IDs to remove.

        Raises:
            ValueError: If the collection does not exist.
        """
        index = self._get_index(collection)
        index.remove(set(ref_ids))

    def search(
        self, collection: str, query_vector: list[float], top_k: int
    ) -> SearchResult:
        """Search a collection by cosine similarity.

        Args:
            collection: Target collection name.
            query_vector: Query embedding vector.
            top_k: Maximum number of results to return.

        Returns:
            Search results with hits ranked by cosine similarity, collection
            status ``READY``, and ``indexing_pending=0``.

        Raises:
            ValueError: If the collection does not exist.
        """
        index = self._get_index(collection)
        results = index.search_cosine(query_vector, top_k)
        hits = self._map_search_hits(index, results)
        return SearchResult(
            hits=hits,
            status=CollectionStatus.READY,
            indexing_pending=0,
        )

    # ------------------------------------------------------------------
    # actor_state persistence
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return a serialisable snapshot of all collections.

        The returned dict is suitable for inclusion in a Pydantic ``BaseState``
        model (Story 10.3). Each collection is stored as its config plus a list
        of ``VectorEntry`` dicts.

        Returns:
            Nested dict keyed by collection name.
        """
        return {
            "collections": {
                name: {
                    "config": self._configs[name].model_dump(),
                    "entries": [e.model_dump() for e in index._entries],
                }
                for name, index in self._collections.items()
            }
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Rebuild all collections from a previously-saved state snapshot.

        Args:
            state: Dict produced by ``get_state()``.
        """
        self._collections.clear()
        self._configs.clear()
        collections = state.get("collections", {})
        for name, col_data in collections.items():
            config = CollectionConfig.model_validate(col_data["config"])
            self._configs[name] = config
            index = VectorIndex()
            for entry_data in col_data["entries"]:
                index.add(VectorEntry.model_validate(entry_data))
            self._collections[name] = index

    # ------------------------------------------------------------------
    # workspace persistence
    # ------------------------------------------------------------------

    def save_collection(self, name: str, workspace_path: str) -> None:
        """Persist a single collection to the filesystem.

        Writes vectors as a compressed numpy array and metadata as a JSON
        sidecar to ``{workspace_path}/.vector_store/{name}.npz`` and
        ``{workspace_path}/.vector_store/{name}.json``.

        Args:
            name: Collection to save.
            workspace_path: Root workspace directory.

        Raises:
            ValueError: If the collection does not exist.
        """
        import numpy as np

        index = self._get_index(name)
        base = Path(workspace_path) / ".vector_store"
        base.mkdir(parents=True, exist_ok=True)

        # Save vectors
        if len(index) > 0:
            vectors = index._buf[: index._count].copy()
        else:
            vectors = np.empty((0, 0), dtype=np.float64)
        np.savez_compressed(str(base / f"{name}.npz"), vectors=vectors)

        # Save metadata (entries + config) as JSON
        meta = {
            "config": self._configs[name].model_dump(),
            "entries": [
                {"ref_type": e.ref_type, "ref_id": e.ref_id, "text": e.text}
                for e in index._entries
            ],
        }
        (base / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")

    def load_collection(self, name: str, config: CollectionConfig, workspace_path: str) -> None:
        """Restore a collection from workspace persistence files.

        If no persisted data exists on disk the collection starts empty.

        Args:
            name: Collection name.
            config: Collection configuration.
            workspace_path: Root workspace directory.
        """
        import numpy as np

        base = Path(workspace_path) / ".vector_store"
        npz_path = base / f"{name}.npz"
        json_path = base / f"{name}.json"

        self._configs[name] = config
        index = VectorIndex()

        if npz_path.exists() and json_path.exists():
            data = np.load(str(npz_path))
            vectors = data["vectors"]
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            entries_meta = meta.get("entries", [])

            for i, em in enumerate(entries_meta):
                if i < len(vectors):
                    entry = VectorEntry(
                        ref_type=em["ref_type"],
                        ref_id=em["ref_id"],
                        text=em["text"],
                        vector=vectors[i].tolist(),
                    )
                    index.add(entry)

        self._collections[name] = index

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_index(self, collection: str) -> VectorIndex:
        """Return the ``VectorIndex`` for *collection* or raise.

        Args:
            collection: Collection name to look up.

        Returns:
            The ``VectorIndex`` instance.

        Raises:
            ValueError: If the collection does not exist.
        """
        try:
            return self._collections[collection]
        except KeyError:
            msg = f"Collection '{collection}' does not exist"
            raise ValueError(msg) from None

    @staticmethod
    def _map_search_hits(
        index: VectorIndex, results: list[tuple[str, float]]
    ) -> list[SearchHit]:
        """Convert raw ``(ref_id, score)`` tuples to ``SearchHit`` models.

        Builds a lookup from ``VectorIndex._entries`` for O(1) metadata
        resolution.

        Args:
            index: The VectorIndex that produced the results.
            results: Raw search output from ``search_cosine``.

        Returns:
            List of ``SearchHit`` models with full metadata.
        """
        entries_by_id: dict[str, VectorEntry] = {e.ref_id: e for e in index._entries}
        hits: list[SearchHit] = []
        for ref_id, score in results:
            entry = entries_by_id.get(ref_id)
            if entry is None:
                logger.warning("Search returned ref_id '%s' not found in entries", ref_id)
                continue
            hits.append(
                SearchHit(
                    ref_type=entry.ref_type,
                    ref_id=entry.ref_id,
                    text=entry.text,
                    score=score,
                )
            )
        return hits
