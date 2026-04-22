"""Shared vector embedding infrastructure for akgentic-tool.

Zero domain coupling — no imports from knowledge_graph or planning.
Install akgentic-tool[vector_search] to use embedding functionality.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from akgentic.core.utils.serializer import SerializableBaseModel

if TYPE_CHECKING:
    import numpy as np
    import openai as _openai_typing

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VectorEntry
# ---------------------------------------------------------------------------


class VectorEntry(SerializableBaseModel):
    """A single embedding record stored in a VectorIndex.

    Links an embedding vector back to its source object via ``ref_type`` and
    ``ref_id``. The ``ref_type`` field accepts any string so this model remains
    domain-agnostic (knowledge graph uses "entity"/"relation"; planning uses
    other values). Used by ``VectorIndex`` for cosine similarity search.
    """

    ref_type: str = Field(
        ..., description="Domain-specific type label for the referenced object (e.g. 'entity')."
    )
    ref_id: str = Field(..., description="UUID string of the referenced object.")
    text: str = Field(..., description="The text that was embedded.")
    vector: list[float] = Field(..., description="Embedding values (one float per dimension).")


# ---------------------------------------------------------------------------
# EmbeddingService
# ---------------------------------------------------------------------------


class EmbeddingService:
    """Generates text embeddings via OpenAI or Azure OpenAI.

    The underlying client is created lazily on the first ``embed()`` call so
    that constructing an ``EmbeddingService`` never triggers network I/O or
    requires API credentials.

    Args:
        model: Embedding model identifier (e.g. ``"text-embedding-3-small"``).
        provider: Which OpenAI SDK variant to use — ``"openai"`` or ``"azure"``.
    """

    def __init__(self, model: str, provider: Literal["openai", "azure"]) -> None:
        self._model = model
        self._provider = provider
        self._client: _openai_typing.OpenAI | _openai_typing.AzureOpenAI | None = None

    # --- Internal ---

    def _get_client(self) -> _openai_typing.OpenAI | _openai_typing.AzureOpenAI:
        """Return (or lazily create) the openai client.

        Returns:
            Configured OpenAI or AzureOpenAI client instance.
        """
        import openai as _openai

        if self._client is None:
            if self._provider == "azure":
                self._client = _openai.AzureOpenAI()
            else:
                self._client = _openai.OpenAI()
        return self._client

    # --- Public API ---

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts and return one vector per input.

        Args:
            texts: List of strings to embed.

        Returns:
            List of float vectors — one per input text, in the same order.
        """
        client = self._get_client()
        response = client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# VectorIndex
# ---------------------------------------------------------------------------


class VectorIndex:
    """In-memory cosine-similarity index for VectorEntry records.

    Uses a pre-allocated numpy matrix that grows exponentially (like Python
    list internals) so that ``search_cosine`` operates on a pre-built
    (N, D) view with no matrix construction overhead — keeping search latency
    well under 1 ms for ≤10 000 entries at 1536 dims (NFR-KG-1).

    Python-float-to-numpy conversion happens during ``add()`` (O(D) per
    entry, outside the search hot path).
    """

    _INITIAL_CAPACITY: int = 64
    _GROWTH_FACTOR: int = 2

    def __init__(self) -> None:
        import numpy as np

        self._entries: list[VectorEntry] = []
        self._count: int = 0
        self._dim: int = 0
        self._capacity: int = 0
        # Pre-allocated backing stores resized geometrically.
        self._buf: np.ndarray = np.empty((0, 0), dtype=np.float64)
        # Row norms pre-computed at add() time to avoid re-reading matrix during search.
        self._norms_buf: np.ndarray = np.empty(0, dtype=np.float64)

    def _ensure_capacity(self, dim: int) -> None:
        """Grow the backing buffers if the current row count equals capacity.

        Uses ``self._dim`` (the index's established dimensionality) for the new
        buffer shape.  The ``dim`` parameter is used only when initializing the
        first buffer (``self._dim == 0``), ensuring consistent column counts
        across resizes regardless of the caller's argument.
        """
        import numpy as np

        if self._count < self._capacity:
            return
        effective_dim = self._dim if self._dim > 0 else dim
        new_cap = max(self._INITIAL_CAPACITY, self._capacity * self._GROWTH_FACTOR)
        new_buf = np.empty((new_cap, effective_dim), dtype=np.float64)
        new_norms = np.empty(new_cap, dtype=np.float64)
        if self._count > 0:
            new_buf[: self._count] = self._buf[: self._count]
            new_norms[: self._count] = self._norms_buf[: self._count]
        self._buf = new_buf
        self._norms_buf = new_norms
        self._capacity = new_cap

    def add(self, entry: VectorEntry) -> None:
        """Append a VectorEntry to the index.

        The entry's vector is written into the pre-allocated numpy buffer and
        its L2 norm is pre-computed so that ``search_cosine`` only needs one
        matrix read (the dot-product pass), not two.

        Args:
            entry: The embedding record to store.
        """
        import numpy as np

        dim = len(entry.vector)
        if self._dim == 0:
            self._dim = dim
        self._ensure_capacity(dim)
        self._buf[self._count] = entry.vector  # numpy converts list[float] → float64 row
        self._norms_buf[self._count] = np.linalg.norm(self._buf[self._count])
        self._count += 1
        self._entries.append(entry)

    def remove(self, ref_ids: set[str]) -> None:
        """Remove all entries whose ``ref_id`` is in ``ref_ids``.

        Compacts the backing buffers to retain only surviving rows.

        Args:
            ref_ids: Set of UUID strings to remove. Unknown IDs are silently ignored.
        """
        import numpy as np

        keep = [i for i, e in enumerate(self._entries) if e.ref_id not in ref_ids]
        if len(keep) == self._count:
            return  # Nothing removed — fast path
        new_count = len(keep)
        new_cap = max(self._INITIAL_CAPACITY, new_count * self._GROWTH_FACTOR)
        dim = self._dim or 1
        new_buf = np.empty((new_cap, dim), dtype=np.float64)
        new_norms = np.empty(new_cap, dtype=np.float64)
        for new_i, old_i in enumerate(keep):
            new_buf[new_i] = self._buf[old_i]
            new_norms[new_i] = self._norms_buf[old_i]
        self._buf = new_buf
        self._norms_buf = new_norms
        self._capacity = new_cap
        self._entries = [self._entries[i] for i in keep]
        self._count = new_count

    def __len__(self) -> int:
        """Return the number of entries currently stored in the index."""
        return self._count

    def search_cosine(self, query_vector: list[float], top_k: int) -> list[tuple[str, float]]:
        """Return the top-k most similar entries by cosine similarity.

        Operates on zero-copy views of pre-built buffers, with row norms
        pre-computed at insertion time. Returns an empty list when the index
        contains no entries.

        Args:
            query_vector: The query embedding (must match index dimensionality).
            top_k: Maximum number of results to return.

        Returns:
            List of ``(ref_id, score)`` tuples sorted by score descending,
            limited to ``top_k`` entries.
        """
        import numpy as np

        if not self._entries:
            return []

        matrix = self._buf[: self._count]  # zero-copy view, O(1)
        row_norms = self._norms_buf[: self._count]  # zero-copy view, O(1)
        q = np.array(query_vector, dtype=np.float64)  # (D,)

        dot_products = matrix @ q  # (N,) — single BLAS dgemv pass
        q_norm = float(np.linalg.norm(q))
        norms = row_norms * q_norm  # (N,) — elementwise, no matrix read
        scores = dot_products / np.maximum(norms, 1e-10)  # (N,) cosine similarity

        indices = np.argsort(-scores)[:top_k]
        return [(self._entries[int(i)].ref_id, float(scores[int(i)])) for i in indices]


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------


def _check_vector_search_dependencies() -> None:
    """Validate that [vector_search] optional dependencies are installed.

    Raises:
        ImportError: With install instructions when ``numpy`` or ``openai`` is missing.
    """
    missing: list[str] = []
    try:
        import numpy as _np  # noqa: F401
    except ImportError:
        missing.append("numpy")
    try:
        import openai as _openai  # noqa: F401
    except ImportError:
        missing.append("openai")
    if missing:
        raise ImportError(
            f"Vector search requires extra dependencies ({', '.join(missing)}). "
            "Install with: pip install akgentic-tool[vector_search]"
        )


__all__ = ["VectorEntry", "EmbeddingService", "VectorIndex", "_check_vector_search_dependencies"]
