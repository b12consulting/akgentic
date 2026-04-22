"""Fire-and-forget EmbeddingActor for non-blocking embedding calls.

Spawned by ``VectorStoreActor`` when entries need embedding.  Calls
``EmbeddingService.embed()`` in its own Pykka thread, delivers results
(or errors) back to the parent via ``proxy_tell``, then stops itself.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.utils.serializer import SerializableBaseModel

if TYPE_CHECKING:
    from akgentic.tool.vector import EmbeddingService, VectorEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message models
# ---------------------------------------------------------------------------


class EmbeddingRequest(SerializableBaseModel):
    """Request sent from VectorStoreActor to EmbeddingActor.

    Contains raw entry metadata (no vectors) for a single batch.
    """

    collection: str = Field(description="Target collection name")
    entries: list[dict[str, str]] = Field(
        description="Raw entries: list of {ref_type, ref_id, text}"
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this embedding batch",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model identifier",
    )
    embedding_provider: Literal["openai", "azure"] = Field(
        default="openai",
        description="Embedding API provider",
    )


class EmbeddingResult(SerializableBaseModel):
    """Successful result sent from EmbeddingActor back to VectorStoreActor."""

    collection: str = Field(description="Target collection name")
    entries: list[VectorEntry] = Field(description="Entries with vectors populated")
    request_id: str = Field(description="Matching request identifier")


class EmbeddingError(SerializableBaseModel):
    """Error sent from EmbeddingActor back to VectorStoreActor on failure."""

    collection: str = Field(description="Target collection name")
    error: str = Field(description="Error message from embedding failure")
    request_id: str = Field(description="Matching request identifier")


# ---------------------------------------------------------------------------
# EmbeddingActor
# ---------------------------------------------------------------------------


class EmbeddingActor(Akgent[BaseConfig, BaseState]):
    """Short-lived actor that embeds a batch of texts and delivers results.

    Spawned by ``VectorStoreActor`` via ``createActor()``.  Processes a
    single ``EmbeddingRequest``, sends back ``EmbeddingResult`` or
    ``EmbeddingError``, and stops itself.
    """

    def on_start(self) -> None:  # noqa: ANN201
        """Initialise state and lazy embedding service slot."""
        self.state = BaseState()
        self.state.observer(self)
        self._embedding_svc: EmbeddingService | None = None

    def _get_or_create_embedding_svc(
        self, model: str, provider: Literal["openai", "azure"]
    ) -> EmbeddingService | None:
        """Lazily create an ``EmbeddingService`` for the given config.

        Args:
            model: Embedding model identifier.
            provider: Embedding API provider name.

        Returns:
            An ``EmbeddingService`` instance, or ``None`` on failure.
        """
        if self._embedding_svc is not None:
            return self._embedding_svc
        try:
            from akgentic.tool.vector import EmbeddingService as EmbSvc

            self._embedding_svc = EmbSvc(model=model, provider=provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to initialize EmbeddingService: %s",
                self.config.name,
                exc,
            )
            return None
        return self._embedding_svc

    def receiveMsg_EmbeddingRequest(self, msg: EmbeddingRequest) -> None:  # noqa: N802
        """Process an embedding request: embed texts and deliver results.

        On success, sends ``EmbeddingResult`` to parent.  On failure,
        sends ``EmbeddingError``.  Always stops self after delivery.

        Args:
            msg: The embedding request with raw entry metadata.
        """
        try:
            svc = self._get_or_create_embedding_svc(msg.embedding_model, msg.embedding_provider)
            if svc is None:
                self._send_error(msg, "EmbeddingService unavailable")
                return

            from akgentic.tool.vector import VectorEntry

            texts = [e["text"] for e in msg.entries]
            vectors: list[list[float]] = svc.embed(texts)

            vector_entries: list[VectorEntry] = []
            for entry_meta, vector in zip(msg.entries, vectors):
                vector_entries.append(
                    VectorEntry(
                        ref_type=entry_meta["ref_type"],
                        ref_id=entry_meta["ref_id"],
                        text=entry_meta["text"],
                        vector=vector,
                    )
                )

            from akgentic.tool.vector_store.actor import VectorStoreActor

            result = EmbeddingResult(
                collection=msg.collection,
                entries=vector_entries,
                request_id=msg.request_id,
            )
            if self._parent is None:
                logger.warning("[%s] No parent address, cannot deliver result", self.config.name)
                return
            parent_proxy = self.proxy_tell(self._parent, VectorStoreActor)
            parent_proxy.receiveMsg_EmbeddingResult(result)

        except Exception as exc:  # noqa: BLE001
            self._send_error(msg, str(exc))
        finally:
            self.stop()

    def _send_error(self, msg: EmbeddingRequest, error: str) -> None:
        """Send an ``EmbeddingError`` to the parent actor.

        Args:
            msg: The original request (for collection and request_id).
            error: Human-readable error description.
        """
        from akgentic.tool.vector_store.actor import VectorStoreActor

        err = EmbeddingError(
            collection=msg.collection,
            error=error,
            request_id=msg.request_id,
        )
        try:
            if self._parent is None:
                logger.warning("[%s] No parent address, cannot deliver error", self.config.name)
                return
            parent_proxy = self.proxy_tell(self._parent, VectorStoreActor)
            parent_proxy.receiveMsg_EmbeddingError(err)
        except Exception as send_exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to send EmbeddingError to parent: %s",
                self.config.name,
                send_exc,
            )
