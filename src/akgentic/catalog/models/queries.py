"""Query models for the unified v2 catalog surface.

Exposes ``EntryQuery`` (filter model used by :meth:`Catalog.search` and
:meth:`EntryRepository.list`) and ``CloneRequest`` (payload model used by
:meth:`Catalog.clone`). Both models are frozen Pydantic ``BaseModel`` subclasses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .entry import EntryKind, NonEmptyStr

__all__ = [
    "CloneRequest",
    "EntryQuery",
]


class EntryQuery(BaseModel):
    """Query model for filtering v2 unified ``Entry`` rows.

    All fields are optional and default to ``None``. Fields combine with AND
    semantics — a ``None`` field means "don't filter by this attribute".

    ``user_id_set`` is a tri-state filter layered on top of ``user_id``:

    * ``None`` — ignore user_id scoping entirely.
    * ``True`` — include only entries whose ``user_id != "anonymous"``
      (i.e. real-user-owned entries on community-tier deployments, or any
      authenticated-caller-owned entry on department / enterprise tier).
    * ``False`` — include only entries whose ``user_id == "anonymous"``
      (i.e. the community-tier default-owner entries).

    This is orthogonal to ``user_id``: passing ``user_id="alice"`` narrows to
    exactly that user, whereas ``user_id_set=True`` narrows to "any user
    other than the community-tier default".
    """

    model_config = ConfigDict(frozen=True)

    namespace: NonEmptyStr | None = Field(default=None, description="Filter by exact namespace.")
    kind: EntryKind | None = Field(
        default=None, description="Filter by entry kind (team/agent/tool/model/prompt)."
    )
    id: NonEmptyStr | None = Field(default=None, description="Filter by exact entry id.")
    user_id: NonEmptyStr | None = Field(
        default=None,
        description="Filter by exact user_id; use user_id_set for presence-only filtering.",
    )
    user_id_set: bool | None = Field(
        default=None,
        description=(
            "Tri-state user_id scope filter: None=any, "
            "True=user_id != 'anonymous', False=user_id == 'anonymous'."
        ),
    )
    description_contains: str | None = Field(
        default=None, description="Filter by substring match in entry description."
    )


class CloneRequest(BaseModel):
    """Request model for cloning an entry from one namespace to another.

    ``dst_namespace`` is required — cloning into an unnamed target is not
    meaningful. Creating a namespace from scratch is a ``Catalog.create``
    concern, not a clone concern.
    """

    model_config = ConfigDict(frozen=True)

    src_namespace: NonEmptyStr = Field(
        description="Source namespace containing the entry to clone."
    )
    src_id: NonEmptyStr = Field(description="Id of the source entry to clone.")
    dst_namespace: NonEmptyStr = Field(description="Destination namespace for the clone.")
    dst_user_id: NonEmptyStr = Field(
        default="anonymous",
        description=(
            "Destination user_id; defaults to 'anonymous' on community tier. "
            "Department / enterprise deployments pass the authenticated "
            "caller's identifier."
        ),
    )
