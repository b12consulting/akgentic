"""Story 39.1: route tests for the shared ``/debug/memory`` worker router.

Mounts the router on a bare ``FastAPI()`` (no worker app, no ActorSystem — the
router is stateless and reads only its own heap) and drives the three endpoints
through an ``httpx.AsyncClient``, mirroring ``tests/server/routes/test_readiness.py``.
"""

from __future__ import annotations

import akgentic.core.diagnostics as core_diagnostics
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import akgentic.infra.worker.routes.memory_diagnostics as memory_diagnostics
from akgentic.infra.worker.routes.memory_diagnostics import router


def _create_test_app() -> FastAPI:
    """Bare FastAPI app with only the memory-diagnostics router mounted."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_census_echoes_label_and_returns_non_empty_counts() -> None:
    """GET /debug/memory/census echoes the label and returns a non-empty counts dict (AC #3)."""
    app = _create_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/debug/memory/census", params={"label": "baseline"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "baseline"
    assert isinstance(body["counts"], dict)
    assert body["counts"]  # the live heap always has objects


@pytest.mark.asyncio
async def test_census_diff_ranks_positive_growth_against_posted_baseline() -> None:
    """POST /debug/memory/census/diff ranks a forced-positive ``dict`` delta (AC #4)."""
    app = _create_test_app()
    transport = ASGITransport(app=app)
    baseline = {"label": "base", "counts": {"dict": 0}}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/debug/memory/census/diff", params={"top": 10}, json=baseline)
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) <= 10
    # 'dict' had baseline 0 and certainly exists now → positive delta, ranked.
    assert any(r["type_name"] == "dict" and r["delta"] > 0 for r in rows)


@pytest.mark.asyncio
async def test_referrers_names_a_holder_of_a_live_instance() -> None:
    """GET /debug/memory/referrers names the holding ``dict`` for a held probe (AC #5)."""

    class _LeakProbe:
        pass

    app = _create_test_app()
    transport = ASGITransport(app=app)
    holder = {"x": _LeakProbe()}  # the dict is the holder we expect to surface
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/debug/memory/referrers", params={"type_name": "_LeakProbe", "depth": 2}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["type_name"] == "_LeakProbe"
        assert body["live_count"] >= 1
        holder_types = {r["type_name"] for r in body["samples"][0]["referrers"]}
        assert "dict" in holder_types  # the holding dict is named
    finally:
        holder.clear()


@pytest.mark.asyncio
async def test_referrers_walk_survives_a_broken_repr() -> None:
    """The walk does not raise when a sampled instance has a raising ``__repr__`` (AC #5)."""

    class _BrokenRepr:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    app = _create_test_app()
    transport = ASGITransport(app=app)
    holder = {"x": _BrokenRepr()}  # held so it is live and referrer-walked
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/debug/memory/referrers", params={"type_name": "_BrokenRepr", "depth": 2}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["live_count"] >= 1
    finally:
        holder.clear()


def test_module_redefines_no_core_primitive() -> None:
    """The names resolve to the core types — the module redefines no primitive (AC #2)."""
    assert memory_diagnostics.ObjectCensus is core_diagnostics.ObjectCensus
    assert memory_diagnostics.TypeGrowth is core_diagnostics.TypeGrowth
    assert memory_diagnostics.ReferrerReport is core_diagnostics.ReferrerReport


def test_module_holds_no_pure_logic() -> None:
    """The route module is FastAPI-only: the referrer walk lives in core, not here.

    No ``gc`` import and no local ``_walk`` / ``_short`` / ``_SKIP_REFERRER_TYPES``
    copies — the handler delegates to ``ReferrerReport.capture`` (AC #3).
    """
    assert not hasattr(memory_diagnostics, "gc")
    assert not hasattr(memory_diagnostics, "_walk")
    assert not hasattr(memory_diagnostics, "_short")
    assert not hasattr(memory_diagnostics, "_SKIP_REFERRER_TYPES")
