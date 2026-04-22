"""Declarative configuration ToolCard for the VectorStoreActor singleton.

Exposes a configuration-only ``VectorStoreTool(ToolCard)`` whose sole
responsibility during ``observer()`` is to ensure the singleton
``VectorStoreActor`` exists via
``orchestrator_proxy.getChildrenOrCreate(VectorStoreActor, ...)`` — idempotent
per ADR-025. It exposes no LLM tools, system prompts, commands, or toolsets;
consumer ToolCards (e.g. ``KnowledgeGraphTool``, ``PlanningTool``) will look
the actor up via ``get_team_member`` in a follow-up story.

Implements ADR-019 addendum §1 (VectorStoreTool declarative configuration
surface).
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from pydantic import Field

from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import ToolCard
from akgentic.tool.event import ActorToolObserver
from akgentic.tool.vector_store.actor import VS_ACTOR_NAME, VS_ACTOR_ROLE, VectorStoreActor
from akgentic.tool.vector_store.protocol import VectorStoreConfig


class VectorStoreTool(ToolCard):
    """Declarative configuration ToolCard for the centralised VectorStoreActor.

    This ToolCard owns the catalog-level configuration surface for the
    ``VectorStoreActor`` singleton. Its sole runtime responsibility is to
    ensure the singleton exists when the observer attaches — all vector
    operations are performed by consumer ToolCards that look the actor up
    via ``get_team_member``.

    Attributes:
        name: Human-readable ToolCard display name.
        description: Natural-language description used by ToolFactory.
        vector_store_name: Name passed to ``VectorStoreConfig.name`` so
            multiple named VectorStoreActor singletons can coexist (e.g.
            ``"#VectorStore"`` and ``"#VectorStore-RAG"``).
        embedding_model: Embedding model identifier (defaults to
            ``"text-embedding-3-small"``).
        embedding_provider: Embedding API provider — one of ``"openai"`` or
            ``"azure"``.

    Note:
        Weaviate connection fields (``weaviate_url`` / ``weaviate_api_key``)
        are deliberately NOT exposed on this ToolCard — they are
        infrastructure-level and injected into ``VectorStoreConfig`` by the
        infra layer via ``AKGENTIC_WEAVIATE_URL`` / ``AKGENTIC_WEAVIATE_API_KEY``
        env vars.
    """

    name: str = "VectorStore"
    description: str = "Centralized vector storage and embedding service"

    vector_store_name: str = Field(
        default=VS_ACTOR_NAME,
        description="Singleton actor name (allows multiple named VectorStoreActor instances)",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model identifier",
    )
    embedding_provider: Literal["openai", "azure"] = Field(
        default="openai",
        description="Embedding API provider",
    )

    # ------------------------------------------------------------------
    # Observer / actor wiring
    # ------------------------------------------------------------------

    def observer(self, observer: ActorToolObserver) -> None:  # type: ignore[override]
        """Attach observer and ensure the VectorStoreActor singleton exists.

        Calls ``orchestrator_proxy.getChildrenOrCreate(VectorStoreActor, ...)``
        once per attach. The call is idempotent per ADR-025 — repeated
        invocations (e.g. from a still-unmigrated ``KnowledgeGraphTool``)
        resolve to the same actor.

        Args:
            observer: Actor-aware observer providing access to the
                orchestrator via ``proxy_ask``.

        Raises:
            ValueError: When ``observer.orchestrator`` is ``None``.
        """
        self._observer = observer

        if observer.orchestrator is None:
            raise ValueError("VectorStoreTool requires access to the orchestrator.")

        orchestrator_proxy = observer.proxy_ask(observer.orchestrator, Orchestrator)
        orchestrator_proxy.getChildrenOrCreate(
            VectorStoreActor,
            config=VectorStoreConfig(
                name=self.vector_store_name,
                role=VS_ACTOR_ROLE,
                embedding_model=self.embedding_model,
                embedding_provider=self.embedding_provider,
            ),
        )

    # ------------------------------------------------------------------
    # ToolCard factory surface — configuration-only
    # ------------------------------------------------------------------

    def get_tools(self) -> list[Callable[..., Any]]:
        """Return no LLM tool callables — this ToolCard is configuration-only."""
        return []
