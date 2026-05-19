"""End-to-end tests for meta-only namespaces (Story 17.10).

Proves the full lifecycle of a namespace bootstrapped by a meta entry
(no team entry): create meta -> create entry -> list -> get -> delete
-> export -> import. If this test suite passes, the invariant
relaxation is correct.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from akgentic.catalog.catalog import Catalog  # noqa: E402
from akgentic.catalog.models.entry import Entry  # noqa: E402

_NAMESPACE_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _model_payload() -> dict[str, Any]:
    """Return a minimal valid payload for a kind=model entry using AgentCard."""
    return {
        "role": "r",
        "description": "",
        "skills": [],
        "agent_class": "akgentic.core.agent.Akgent",
        "config": {"name": "m", "role": "r"},
        "routes_to": [],
        "metadata": {},
    }


class TestMetaOnlyNamespaceEndToEnd:
    """Full lifecycle of a meta-only namespace through the REST API."""

    def test_full_lifecycle_no_team(self, api_client: tuple[TestClient, Catalog]) -> None:
        """Create meta, create model entry, list, get, delete, export/import
        — all in a team-less namespace.
        """
        client, catalog = api_client
        ns = "global"

        # 1. Create namespace via PUT meta (no team)
        meta_body = {"name": "Global Library", "description": "Shared assets", "properties": {}}
        resp = client.put(f"/catalog/namespace/{ns}/meta", json=meta_body)
        assert resp.status_code == 201
        meta_entry = resp.json()
        assert meta_entry["user_id"] == "anonymous"
        assert meta_entry["kind"] == "meta"

        # 2. Create a kind="model" entry in the meta-only namespace
        model_entry = Entry(
            id="shared-model",
            kind="model",
            namespace=ns,
            user_id="anonymous",
            model_type="akgentic.core.agent_card.AgentCard",
            description="A shared model",
            payload=_model_payload(),
        )
        created = catalog.create(model_entry)
        assert created.namespace == ns
        assert created.id == "shared-model"

        # 3. List entries in namespace
        entries = catalog.list_by_namespace(ns)
        ids = {e.id for e in entries}
        assert "_meta" in ids
        assert "shared-model" in ids

        # 4. Get the model entry
        fetched = catalog.get(ns, "shared-model")
        assert fetched.kind == "model"

        # 5. Delete the model entry
        catalog.delete(ns, "shared-model")
        remaining = catalog.list_by_namespace(ns)
        remaining_ids = {e.id for e in remaining}
        assert "shared-model" not in remaining_ids
        assert "_meta" in remaining_ids

        # 6. Re-create the model entry for export
        catalog.create(model_entry)

        # 7. Export the namespace bundle
        resp_export = client.get(f"/catalog/namespace/{ns}/export")
        assert resp_export.status_code == 200
        yaml_text = resp_export.text
        assert "shared-model" in yaml_text

        # 8. Delete all entries to prepare for import
        catalog.delete(ns, "shared-model")
        catalog.delete(ns, "_meta")

        # 9. Import the bundle back
        resp_import = client.post(
            "/catalog/namespace/import",
            content=yaml_text.encode("utf-8"),
        )
        assert resp_import.status_code == 201

        # 10. Verify entries are restored with consistent user_id
        restored = catalog.list_by_namespace(ns)
        restored_ids = {e.id for e in restored}
        assert "_meta" in restored_ids
        assert "shared-model" in restored_ids
        restored_meta = catalog.get(ns, "_meta")
        assert restored_meta.user_id == "anonymous"
