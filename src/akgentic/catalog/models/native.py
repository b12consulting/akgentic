"""``NativeValue`` — scalar carrier for ref-addressable catalog entries.

This module exposes a single Pydantic ``BaseModel`` (:class:`NativeValue`) used
as the payload-validation class for catalog entries whose ``model_type`` is
``akgentic.catalog.NativeValue``. A native-value entry stores a single scalar
(``str`` / ``int`` / ``float`` / ``bool``) or container (``list`` / ``dict``)
under the field name ``value``.

The resolver in :mod:`akgentic.catalog.resolver` knows about ``NativeValue`` in
exactly one place — the final step of :func:`~akgentic.catalog.resolver._populate_ref_marker`
unwraps a validated ``NativeValue`` instance and returns ``instance.value`` (the
bare scalar / list / dict) so a typed field like ``str`` / ``int`` / ``bool``
on the consuming entry can be assigned via ``{__ref__: "..."}`` instead of
inlined. See ADR-015 §"Decision" for the rationale and the worked example.

Key properties:

* **Direct retrieval is unchanged.** ``Catalog.get(namespace, id)`` on a
  ``NativeValue`` entry returns the ``Entry`` like any other entry; the
  caller reads ``entry.payload["value"]`` or
  ``NativeValue.model_validate(entry.payload).value`` to get the scalar.
  The resolver only unwraps at the ref-splice site.
* **Anti-pattern callout — ``value: dict[str, Any]``.** The ``dict`` arm of
  the union exists for boundary-crossing JSON literals (e.g. a free-form
  config dict consumed as opaque data by the splice site). It is NOT a
  back door for storing structured catalog content. If a consumer needs
  structured data with typed fields, write a real ``BaseModel`` and store
  it as a normal entry. The catalog does NOT mechanically block this
  misuse — the resolver does not introspect ``value`` — so the discipline
  is on the catalog author. ADR-015 §"Out of scope" / §"Risks".

The model is fully Pydantic-serializable per Golden Rule #1b: no
``ConfigDict(arbitrary_types_allowed=True)``, no ``PrivateAttr``, no
runtime state. The single field uses the declared union of JSON-shaped
types. ``NativeValue`` does not declare any reserved ref-sentinel field
(``__ref__`` / ``__type__`` / ``__namespace__``), so the resolver's
:func:`~akgentic.catalog.resolver.load_model_type` accepts the FQCN
``akgentic.catalog.NativeValue`` unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["NativeValue"]


class NativeValue(BaseModel):
    """Catalog-owned wrapper for a scalar (or container) addressable via ``__ref__``.

    A ``NativeValue`` entry carries a single payload field ``value`` holding a
    scalar (``str`` / ``int`` / ``float`` / ``bool``) or container
    (``list[Any]`` / ``dict[str, Any]``). The resolver unwraps the validated
    instance at the ref-splice site so a typed field on a consuming entry
    receives the bare value, not the wrapper.

    Worked example (ADR-015 §"Worked example") — a shared prompt body and
    role string referenced from a ``PromptTemplate`` entry:

    .. code-block:: yaml

        # id_team_template (NativeValue carrying a str)
        id: id_team_template
        kind: prompt
        namespace: agent-team
        model_type: akgentic.catalog.NativeValue
        payload:
          value: "You are {role}. Collaborate with your team."

        # id_team_prompt (PromptTemplate referencing the NativeValue)
        id: id_team_prompt
        kind: prompt
        namespace: agent-team
        model_type: akgentic.llm.prompts.PromptTemplate
        payload:
          template: {__ref__: "id_team_template"}   # resolves to str
          role:     {__ref__: "id_team_role"}       # resolves to str

    Resolving ``id_team_prompt`` produces a ``PromptTemplate`` whose
    ``.template`` and ``.role`` are bare strings — the resolver unwrapped
    the ``NativeValue`` at each ``__ref__`` splice site.

    Attributes:
        value: The carried scalar or container. The declared union spans
            ``str``, ``int``, ``float``, ``bool``, ``list[Any]``, and
            ``dict[str, Any]``. Pydantic validates the input against this
            union at construction time.

    Anti-pattern note (ADR-015 §"Out of scope"): the ``dict[str, Any]``
    arm is for boundary-crossing JSON literals only. It is NOT a back door
    for storing structured catalog content — write a real ``BaseModel``
    when typed structure is needed. The catalog does not mechanically
    block this misuse.
    """

    value: str | int | float | bool | list[Any] | dict[str, Any] = Field(
        description=(
            "The carried scalar or container. Resolved through ``__ref__`` "
            "the resolver unwraps the wrapper and splices ``value`` into the "
            "consuming field. Direct retrieval returns the wrapper as the "
            "entry payload like any other entry."
        ),
    )
