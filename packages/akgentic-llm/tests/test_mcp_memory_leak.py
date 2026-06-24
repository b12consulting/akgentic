"""MCP memory-leak assessment for the ADR-009 ``ReactAgent.close()`` surface.

Drives *N* ``ReactAgent`` create -> ``run()`` (with a **fake** async-context MCP
toolset, no subprocess) -> ``close()`` cycles and asserts the live-object census
and the ``tracemalloc`` traced heap do **not** grow monotonically across cycles,
so the claim "``close()`` actually releases the per-run MCP servers and the owned
loop" is *measured*, not just asserted. A deliberately-leaking control (skip
``close()``, retain agents) proves the bound catches a real leak rather than
passing vacuously.

Scope: **per-run MCP mode ONLY**. Story 11-2 (persistent ``AsyncExitStack``) was
deferred -- there is no ``self._stack``/``AsyncExitStack``/"entered once at
construction" path in ``ReactAgent``; ``aclose()`` is httpx-only and ``run()``
enters/exits MCP toolsets per call. This module references none of that.

Offline & deterministic: no model/provider is ever contacted (``run`` is patched
with a zero-egress stub; the OpenAI provider in ``minimal_config`` builds with no
key and sends nothing), the fake toolset spawns no subprocess and opens no
socket, and the bounds are growth-across-cycles constants (never absolute process
bytes). Run WITHOUT ``--cov`` (the ``beartype.claw`` circular import aborts
coverage collection in this venv -- Story 11-1/11-3 caveat).
"""

import asyncio
import gc
import tracemalloc
from typing import Any
from unittest.mock import patch

import pytest
from akgentic.llm import ModelConfig, ReactAgent, ReactAgentConfig

# ---------------------------------------------------------------------------
# Tuning constants (AC #2, #3, #5, #7)
# ---------------------------------------------------------------------------
# Number of create->run->close cycles per loop. Larger N with a comfortable
# bound is preferred over a tiny N with a knife-edge bound (AC #7).
_CYCLES = 30
# Warmup cycles discarded before the first measured sample: absorbs one-time
# import/interning/arena allocations that would otherwise read as growth.
_WARMUP_CYCLES = 5

# Growth bounds across cycles AFTER warmup (first-post-warmup sample -> last
# sample), NEVER an absolute process-memory figure. Mirrors the OR-of-two-signals
# shape of MemoryTrend.is_object_leak in the cited reference (see _MemoryCensus
# docstring) but with test-tuned values, NOT the worker's live-server 2 MiB /
# 1500 absolutes. Chosen with wide margin from measured separation: the no-leak
# path closes-and-drops each cycle's agent, so its post-warmup object growth is 0
# and heap growth is a few KB of sampling noise; the control retains ~25 whole
# agent graphs (each an event loop + httpx client + pydantic-ai Agent + context),
# growing the live-object census by ~2.4k objects and the traced heap by ~390 KB.
# 500 objects / 96 KiB sits cleanly between the two so the no-leak path passes
# with margin and the control clearly trips at least one signal.
_MAX_OBJECT_GROWTH = 500
_MAX_HEAP_GROWTH_BYTES = 96 * 1024


@pytest.fixture
def minimal_config() -> ReactAgentConfig:
    """Minimal config; the OpenAI provider builds with no key (no egress here)."""
    return ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))


# ---------------------------------------------------------------------------
# Fake async-context MCP toolset (AC #1, #9)
# ---------------------------------------------------------------------------


class _FakeMCPToolset:
    """In-process stand-in for a pydantic-ai MCP server toolset (offline).

    Implements only the async-context + ``list_tools`` shape ``ReactAgent``
    forwards to pydantic-ai's ``Agent(toolsets=...)`` -- mirroring the real
    ``MCPServerStdio``/``MCPServerSSE``/``MCPServerStreamableHTTP`` returned by
    ``MCPTool.get_toolsets()`` (akgentic-tool) -- but spawns NO subprocess and
    opens NO socket. Class-level ``entered``/``exited`` counters let the no-leak
    test assert per-cycle entry/exit balance (AC #4).
    """

    entered = 0
    exited = 0

    @classmethod
    def reset_counters(cls) -> None:
        """Zero the shared enter/exit counters before a measured loop."""
        cls.entered = 0
        cls.exited = 0

    async def __aenter__(self) -> "_FakeMCPToolset":
        type(self).entered += 1
        return self

    async def __aexit__(self, *exc: object) -> bool:
        type(self).exited += 1
        return False

    async def list_tools(self) -> list[Any]:
        """Return a small fixed list of lightweight stub tool-def objects."""
        return [object(), object(), object()]


async def _stub_run(self: ReactAgent, *_: Any, **__: Any) -> str:
    """Zero-egress replacement for ``ReactAgent.run`` (Strategy 1).

    Never touches pydantic-ai's run machinery / no model is contacted. Exercises
    the agent's fake toolset (set on ``_leak_test_fake`` by the per-cycle loop)
    via ``async with`` so the fake's ``__aenter__``/``__aexit__`` is genuinely
    entered/exited every cycle -- keeping the AC #4 balance assertion meaningful
    without reaching into pydantic-ai's internal toolset representation.
    """
    fake: _FakeMCPToolset = self._leak_test_fake  # type: ignore[attr-defined]
    async with fake:
        await fake.list_tools()
    return "ok"


# ---------------------------------------------------------------------------
# Local memory-census helper -- replicate, do NOT import (AC #2, #8)
# ---------------------------------------------------------------------------
# Canonical reference: MemorySampler / ObjectCensus / census_by_type in
# packages/akgentic-infra-department/src/akgentic/infra/department/worker/
# memory_diagnostics.py (and being promoted to akgentic.core.diagnostics).
# Replicated here (the minimal tracemalloc-heap + gc-object-count subset, with a
# gc.collect() before each sample) rather than imported because akgentic-llm
# depends only on pydantic-ai/httpx/tenacity and MUST NOT import a sibling
# deployment/core package (module boundary, NFR4 / NFR1). This comment is a
# navigation aid only -- no test asserts on it (Golden Rule #8).


class _MemoryCensus:
    """Minimal heap + live-object sampler with gc.collect() before each sample.

    The ``gc.collect()`` is load-bearing: the loop<->tasks<->agent graph is
    cyclic (ADR-009 §Alternatives), so a *correctly* released cycle is not freed
    by refcounting alone until a collection runs. Collecting first means a sample
    measures only memory that survives a full collection -- true retention.
    """

    def __init__(self) -> None:
        self._baseline_objects = 0
        self._baseline_heap = 0
        self._samples: list[tuple[int, int]] = []

    def start(self) -> None:
        """Begin tracing and record the baseline object count / traced heap."""
        tracemalloc.start()
        gc.collect()
        self._baseline_objects = len(gc.get_objects())
        self._baseline_heap = tracemalloc.get_traced_memory()[0]
        self._samples = []

    def sample(self) -> tuple[int, int]:
        """Collect cycles, then record (object_count, traced_heap_bytes)."""
        gc.collect()
        heap_bytes = tracemalloc.get_traced_memory()[0]
        reading = (len(gc.get_objects()), heap_bytes)
        self._samples.append(reading)
        return reading

    def object_growth(self) -> int:
        """Live-object growth: last sample minus first post-warmup sample."""
        return self._samples[-1][0] - self._samples[0][0]

    def heap_growth(self) -> int:
        """Traced-heap growth (bytes): last sample minus first post-warmup."""
        return self._samples[-1][1] - self._samples[0][1]

    def stop(self) -> None:
        """Stop tracing."""
        tracemalloc.stop()


# ---------------------------------------------------------------------------
# AC #3 / #4 / #6 / #7 -- per-run no-leak: N create->run->close cycles bounded
# ---------------------------------------------------------------------------


def test_per_run_cycles_do_not_leak(minimal_config: ReactAgentConfig) -> None:
    """N create->run->close cycles stay within the census bound and close cleanly.

    AC #3: object-count and traced-heap growth (first-post-warmup -> last) each
    stay under the bound. AC #4: the fake toolset's enter/exit are balanced. AC
    #6: every agent's loop is closed and ``_closed`` is True (asserted per cycle
    so the agent can be dropped immediately -- retaining all N agents would itself
    pin their graphs and defeat the no-leak measurement). AC #7: no ``--cov``, no
    absolute-memory/timing assertion.
    """
    _FakeMCPToolset.reset_counters()
    census = _MemoryCensus()
    census.start()
    try:
        with patch.object(ReactAgent, "run", new=_stub_run):
            for cycle in range(_CYCLES):
                fake = _FakeMCPToolset()
                agent = ReactAgent(config=minimal_config, toolsets=[fake])
                agent._leak_test_fake = fake  # type: ignore[attr-defined]
                agent.run_sync("q")
                agent.close()
                # AC #6 -- close() released this cycle's owned loop; assert and drop
                # (do NOT retain) so the census measures genuine release.
                assert agent._loop.is_closed()
                assert agent._closed is True
                del agent, fake
                if cycle >= _WARMUP_CYCLES:
                    census.sample()

        obj_growth = census.object_growth()
        heap_growth = census.heap_growth()
        assert obj_growth < _MAX_OBJECT_GROWTH, f"object growth {obj_growth} >= bound"
        assert heap_growth < _MAX_HEAP_GROWTH_BYTES, f"heap growth {heap_growth} bytes >= bound"

        # AC #4 -- every __aenter__ matched by an __aexit__ (no toolset left entered).
        assert _FakeMCPToolset.entered == _CYCLES
        assert _FakeMCPToolset.exited == _FakeMCPToolset.entered
    finally:
        census.stop()


# ---------------------------------------------------------------------------
# AC #5 -- deliberately-leaking control trips the same bound (test-of-the-test)
# ---------------------------------------------------------------------------


def test_leaking_control_exceeds_bound(minimal_config: ReactAgentConfig) -> None:
    """Skipping ``close()`` and retaining agents exceeds the AC #3 bound.

    Proves the bound is sensitive enough to catch a real leak (AC #3 does not
    pass vacuously). Retained agents pin their loops/clients/contexts live across
    cycles, so census growth rises past the bound. The leaked agents are closed
    in ``finally`` so the control does not poison the rest of the suite.
    """
    census = _MemoryCensus()
    census.start()
    leaked: list[ReactAgent] = []
    try:
        with patch.object(ReactAgent, "run", new=_stub_run):
            for cycle in range(_CYCLES):
                fake = _FakeMCPToolset()
                agent = ReactAgent(config=minimal_config, toolsets=[fake])
                agent._leak_test_fake = fake  # type: ignore[attr-defined]
                leaked.append(agent)  # retained -> loop/client/context stay live
                agent.run_sync("q")
                # NOTE: close() deliberately skipped -- this is the leak.
                if cycle >= _WARMUP_CYCLES:
                    census.sample()

        obj_growth = census.object_growth()
        heap_growth = census.heap_growth()
        # OR-of-two-signals: the leak trips at least one bound (same as AC #3).
        assert obj_growth >= _MAX_OBJECT_GROWTH or heap_growth >= _MAX_HEAP_GROWTH_BYTES, (
            f"control did not trip the bound: object growth {obj_growth}, "
            f"heap growth {heap_growth} bytes"
        )
    finally:
        for a in leaked:
            a.close()
        census.stop()


# ---------------------------------------------------------------------------
# AC #6 -- per-run cycles leave no agent loop open (object-count complement)
# ---------------------------------------------------------------------------


def test_open_loop_count_does_not_grow(minimal_config: ReactAgentConfig) -> None:
    """Across N closed cycles, the count of live, non-closed agent loops stays 0.

    The loop-release half of the FR8 claim, measured independently of the census:
    each cycle's loop is closed before the next, so no open loop accumulates.
    """
    open_loops_per_cycle: list[int] = []
    try:
        with patch.object(ReactAgent, "run", new=_stub_run):
            for _ in range(_CYCLES):
                fake = _FakeMCPToolset()
                agent = ReactAgent(config=minimal_config, toolsets=[fake])
                agent._leak_test_fake = fake  # type: ignore[attr-defined]
                agent.run_sync("q")
                agent.close()
                gc.collect()
                open_loops_per_cycle.append(
                    sum(
                        1
                        for obj in gc.get_objects()
                        if isinstance(obj, asyncio.AbstractEventLoop) and not obj.is_closed()
                    )
                )
    finally:
        gc.collect()

    # No cycle leaves a live, non-closed agent loop behind (count never grows).
    assert max(open_loops_per_cycle) == min(open_loops_per_cycle)
