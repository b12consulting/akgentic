"""Shared, temporary, opt-in ``/debug/memory`` leak-diagnostics router.

A sibling of the readiness/teams worker routers. Stateless: the baseline census
is held by the client (posted back to ``/diff``); the server only ever reads its
own heap. ALL diagnostic logic — census, diff, and the referrer trace — lives in
``akgentic.core.diagnostics`` (core owns the primitives); this module is purely
the FastAPI surface, every handler a thin delegate. Diagnostic only — mounted
opt-in per tier behind ``AKGENTIC_WORKER_DEBUG_MEMORY``; remove before GA (ADR-015 §2).
"""

from __future__ import annotations

from fastapi import APIRouter

from akgentic.core.diagnostics import ObjectCensus, ReferrerReport, TypeGrowth

router = APIRouter(prefix="/debug/memory", tags=["debug"])


@router.get("/census", response_model=ObjectCensus)
async def census(label: str = "") -> ObjectCensus:
    """Return the worker's current live per-class object census (post gc.collect)."""
    return ObjectCensus.capture(label=label)


@router.post("/census/diff", response_model=list[TypeGrowth])
async def census_diff(baseline: ObjectCensus, top: int | None = None) -> list[TypeGrowth]:
    """Diff a client-supplied baseline census against the worker's current census.

    Returns the per-class growth (current - baseline), positive deltas only,
    ranked worst-first — the classes this worker retained since the baseline.
    """
    return ObjectCensus.diff(baseline, ObjectCensus.capture(label="current"), top=top)


@router.get("/referrers", response_model=ReferrerReport)
async def referrers(
    type_name: str,
    depth: int = 4,
    fanout: int = 3,
    samples: int = 3,
    newest: bool = True,
) -> ReferrerReport:
    """Trace who still holds live instances of ``type_name`` — names the leak root.

    Picks ``samples`` live instances of the named class and walks up
    ``gc.get_referrers`` ``depth`` hops (``fanout`` referrers per hop) so the
    chain ends at the long-lived root pinning them. Run it on a class from the
    census diff, e.g. ``?type_name=_ProxyHistogram``. ``newest=True`` (default)
    samples the LAST instances in heap order — those most likely allocated during
    the load run, i.e. the leaked ones.
    """
    return ReferrerReport.capture(
        type_name, depth=depth, fanout=fanout, samples=samples, newest=newest
    )
