"""Tests for the pure memory leak-detection primitives in akgentic.core.diagnostics.

Behavior-only (Golden Rule #8): no assertion targets an ADR/docstring/comment
string. Sampler tests drive deterministic growth and assert deltas / sign /
ordering, never absolute byte counts (RSS/heap are environment-dependent).
"""

from __future__ import annotations

from akgentic.core.diagnostics import (
    MemorySample,
    MemorySampler,
    MemoryTrend,
    ObjectCensus,
    ReferrerNode,
    ReferrerReport,
    TypeGrowth,
    census_by_type,
)
from akgentic.core.diagnostics import memory as memory_module
from akgentic.core.diagnostics.memory import (
    DEFAULT_HEAP_LEAK_BYTES,
    DEFAULT_OBJECT_LEAK_COUNT,
    _read_rss_bytes,
    _short,
    _walk,
)

# --- AC 1 / AC 2: import boundary and public surface --------------------------


def test_module_imports_without_fastapi() -> None:
    """The promoted module must not pull FastAPI into core (zero-infra invariant)."""
    # The pure module exposes none of the FastAPI router surface (which stays in
    # infra) and never registers fastapi among its own module-level imports.
    # NB: do not importlib.reload() here — an in-place reload rebinds this module's
    # classes while leaving already-imported references (package re-exports, other
    # tests' imports) pointing at the originals, corrupting model validation for
    # every subsequent test.
    assert not hasattr(memory_module, "router")
    assert not hasattr(memory_module, "census")
    assert not hasattr(memory_module, "census_diff")
    assert not hasattr(memory_module, "referrers")
    assert "fastapi" not in memory_module.__dict__


def test_module_source_has_no_fastapi_import() -> None:
    """The module source carries no FastAPI import or router decorator."""
    import inspect

    source = inspect.getsource(memory_module)
    assert "import fastapi" not in source
    assert "from fastapi" not in source
    assert "APIRouter" not in source
    assert "@router" not in source


def test_public_names_importable_from_package() -> None:
    """AC 2: the eight public names re-export from akgentic.core.diagnostics."""
    import akgentic.core.diagnostics as pkg

    expected = {
        "census_by_type",
        "MemorySample",
        "TypeGrowth",
        "ObjectCensus",
        "MemoryTrend",
        "MemorySampler",
        "ReferrerNode",
        "ReferrerReport",
    }
    assert expected.issubset(set(pkg.__all__))
    for name in expected:
        assert hasattr(pkg, name)


def test_census_by_type_counts_live_instances() -> None:
    """census_by_type reflects newly created live instances of a type."""

    class _CensusProbe:
        pass

    before = census_by_type().get("_CensusProbe", 0)
    held = [_CensusProbe() for _ in range(5)]  # noqa: F841 — keep them live
    after = census_by_type().get("_CensusProbe", 0)
    assert after - before == 5


# --- AC 3: ObjectCensus.diff ranking + format_diff ----------------------------


def test_diff_ranks_positive_deltas_worst_first() -> None:
    baseline = ObjectCensus(label="base", counts={"A": 1, "B": 10, "C": 5, "D": 3})
    candidate = ObjectCensus(label="cand", counts={"A": 4, "B": 10, "C": 2, "D": 9})

    rows = ObjectCensus.diff(baseline, candidate)

    # D grew +6, A grew +3; B unchanged (dropped), C shrank (dropped).
    assert [r.type_name for r in rows] == ["D", "A"]
    assert [r.delta for r in rows] == [6, 3]
    assert all(r.delta > 0 for r in rows)


def test_diff_honors_top_truncation() -> None:
    baseline = ObjectCensus(counts={"A": 0, "B": 0, "C": 0})
    candidate = ObjectCensus(counts={"A": 10, "B": 5, "C": 1})

    rows = ObjectCensus.diff(baseline, candidate, top=2)

    assert [r.type_name for r in rows] == ["A", "B"]
    assert len(rows) == 2


def test_diff_includes_classes_new_in_candidate() -> None:
    baseline = ObjectCensus(counts={"A": 1})
    candidate = ObjectCensus(counts={"A": 1, "New": 7})

    rows = ObjectCensus.diff(baseline, candidate)

    assert [(r.type_name, r.baseline, r.final, r.delta) for r in rows] == [("New", 0, 7, 7)]


def test_format_diff_empty_returns_no_leak_message() -> None:
    assert ObjectCensus.format_diff([]) == (
        "no class grew between the two censuses — no object leak detected"
    )


def test_format_diff_renders_ranked_table() -> None:
    rows = [TypeGrowth(type_name="Foo", baseline=1, final=4, delta=3)]
    out = ObjectCensus.format_diff(rows)
    assert out.splitlines()[0] == "leaked classes (candidate - baseline), worst first:"
    assert "Foo" in out
    assert "+     3" in out


# --- AC 4: capture + save/load round-trip -------------------------------------


def test_capture_snapshots_live_counts() -> None:
    class _CaptureProbe:
        pass

    held = [_CaptureProbe() for _ in range(3)]  # noqa: F841 — keep them live
    census = ObjectCensus.capture(label="probe")
    assert census.label == "probe"
    assert census.counts.get("_CaptureProbe", 0) >= 3


def test_save_load_json_round_trip(tmp_path) -> None:  # noqa: ANN001 — pytest fixture
    original = ObjectCensus(label="run-A", counts={"X": 12, "Y": 3})
    path = tmp_path / "census.json"

    original.save(path)
    restored = ObjectCensus.load(path)

    assert restored.label == original.label
    assert restored.counts == original.counts


# --- AC 5: is_object_leak / verdict classification ----------------------------


def _trend(
    *,
    heap_growth: int = 0,
    object_growth: int = 0,
    rss_growth: int = 0,
    rss_available: bool = True,
    heap_leak_bytes: int = DEFAULT_HEAP_LEAK_BYTES,
    object_leak_count: int = DEFAULT_OBJECT_LEAK_COUNT,
) -> MemoryTrend:
    return MemoryTrend(
        rss_available=rss_available,
        heap_growth_bytes=heap_growth,
        rss_growth_bytes=rss_growth,
        object_growth=object_growth,
        heap_leak_bytes=heap_leak_bytes,
        object_leak_count=object_leak_count,
    )


def test_is_object_leak_false_at_thresholds() -> None:
    """At-threshold is NOT a leak (strict greater-than boundary)."""
    trend = _trend(heap_growth=DEFAULT_HEAP_LEAK_BYTES, object_growth=DEFAULT_OBJECT_LEAK_COUNT)
    assert trend.is_object_leak is False


def test_is_object_leak_true_when_heap_one_over() -> None:
    trend = _trend(heap_growth=DEFAULT_HEAP_LEAK_BYTES + 1, object_growth=0)
    assert trend.is_object_leak is True


def test_is_object_leak_true_when_objects_one_over() -> None:
    trend = _trend(heap_growth=0, object_growth=DEFAULT_OBJECT_LEAK_COUNT + 1)
    assert trend.is_object_leak is True


def test_verdict_leak_path() -> None:
    trend = _trend(heap_growth=DEFAULT_HEAP_LEAK_BYTES + 1, object_growth=2000)
    assert trend.verdict.startswith("LIKELY REAL LEAK")


def test_verdict_arena_retention_path() -> None:
    """RSS grew past the heap threshold while heap did not ⇒ arena retention."""
    trend = _trend(
        heap_growth=0,
        object_growth=0,
        rss_growth=DEFAULT_HEAP_LEAK_BYTES + 1,
        rss_available=True,
    )
    assert trend.is_object_leak is False
    assert trend.verdict.startswith("ARENA RETENTION")


def test_verdict_stable_path() -> None:
    trend = _trend(heap_growth=0, object_growth=0, rss_growth=0, rss_available=True)
    assert trend.verdict.startswith("STABLE")


def test_verdict_stable_when_rss_unavailable_even_if_rss_grew() -> None:
    """Without RSS, the arena-retention branch cannot fire — falls through to STABLE."""
    trend = _trend(
        heap_growth=0,
        object_growth=0,
        rss_growth=DEFAULT_HEAP_LEAK_BYTES + 1,
        rss_available=False,
    )
    assert trend.verdict.startswith("STABLE")


# --- AC 6: threshold parameterization -----------------------------------------


def test_custom_lower_threshold_flips_is_object_leak() -> None:
    growth_heap = 1024  # well under the default 2 MiB heap threshold
    default_trend = _trend(heap_growth=growth_heap)
    assert default_trend.is_object_leak is False

    tight_trend = _trend(heap_growth=growth_heap, heap_leak_bytes=512)
    assert tight_trend.is_object_leak is True


def test_custom_object_threshold_flips_is_object_leak() -> None:
    default_trend = _trend(object_growth=100)
    assert default_trend.is_object_leak is False

    tight_trend = _trend(object_growth=100, object_leak_count=50)
    assert tight_trend.is_object_leak is True


def test_default_thresholds_match_module_defaults() -> None:
    trend = MemoryTrend(
        rss_available=False,
        heap_growth_bytes=0,
        rss_growth_bytes=0,
        object_growth=0,
    )
    assert trend.heap_leak_bytes == DEFAULT_HEAP_LEAK_BYTES
    assert trend.object_leak_count == DEFAULT_OBJECT_LEAK_COUNT


# --- AC 7: MemorySampler end-to-end -------------------------------------------


def test_sampler_reports_growth_deltas_and_ranks_types() -> None:
    class _SamplerProbe:
        pass

    sampler = MemorySampler()
    sampler.start()
    try:
        retained: list[_SamplerProbe] = []
        sampler.sample(label="iter", iteration=0)
        for i in range(1, 3):
            retained.extend(_SamplerProbe() for _ in range(500))
            sampler.sample(label="iter", iteration=i)
        trend = sampler.report()
    finally:
        sampler.stop()

    # Deltas are last-minus-first; we retained objects across samples, so both grew.
    assert len(trend.samples) == 3
    assert trend.object_growth > 0
    assert trend.heap_growth_bytes > 0
    # The probe type we accumulated must appear in the ranked growth list.
    probe_growth = [tg for tg in trend.top_type_growth if tg.type_name == "_SamplerProbe"]
    assert probe_growth and probe_growth[0].delta > 0
    # top_type_growth is ranked worst-first (descending delta).
    deltas = [tg.delta for tg in trend.top_type_growth]
    assert deltas == sorted(deltas, reverse=True)
    # Retain to the end so the sampler measures real retention.
    assert len(retained) == 1000


def test_sampler_forwards_custom_thresholds_into_report() -> None:
    sampler = MemorySampler(heap_leak_bytes=512, object_leak_count=50)
    sampler.start()
    try:
        sampler.sample(label="a", iteration=0)
        sampler.sample(label="b", iteration=1)
        trend = sampler.report()
    finally:
        sampler.stop()

    assert trend.heap_leak_bytes == 512
    assert trend.object_leak_count == 50


def test_sample_auto_increments_iteration_when_omitted() -> None:
    sampler = MemorySampler()
    sampler.start()
    try:
        first = sampler.sample()
        second = sampler.sample()
    finally:
        sampler.stop()
    assert first.iteration == 0
    assert second.iteration == 1


# --- AC 8: referrer walk depth / fanout / skip-types / seen / broken repr ------


def test_walk_stops_at_zero_depth() -> None:
    obj = object()
    assert _walk(obj, depth=0, fanout=3, seen=set()) == []


def test_walk_respects_fanout() -> None:
    target = ["leaf"]
    # Create several distinct dict referrers to the same target.
    holders = [{"ref": target} for _ in range(5)]  # noqa: F841 — keep them live
    nodes = _walk(target, depth=1, fanout=2, seen={id(target), id(holders)})
    assert len(nodes) <= 2


def test_walk_skips_configured_types() -> None:
    target = {"k": "v"}
    holder = [target]  # a 'list' referrer — must be skipped
    nodes = _walk(target, depth=2, fanout=5, seen={id(target)})
    # The list holder is in _SKIP_REFERRER_TYPES, so it is never emitted as a node.
    assert all(node.type_name != "list" for node in nodes)
    assert holder  # keep the holder live


def test_walk_skips_already_seen_ids() -> None:
    target = {"k": "v"}
    holder = {"ref": target}
    # Pre-seed `seen` with the holder's id so the walk skips it.
    nodes = _walk(target, depth=2, fanout=5, seen={id(target), id(holder)})
    assert all(node.detail != _short(holder) for node in nodes)


def test_short_returns_placeholder_for_broken_repr() -> None:
    class _BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    out = _short(_BadRepr())
    assert out == "<_BadRepr repr failed>"


def test_short_truncates_long_repr() -> None:
    long_value = "x" * 500
    out = _short(long_value)
    assert len(out) <= 121 + 2  # 120 chars + ellipsis (+ surrounding quotes from repr)
    assert out.endswith("…")


def test_walk_does_not_raise_on_broken_repr() -> None:
    class _BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    instance = _BadRepr()
    holder = {"held": instance}  # a dict referrer with a known type name
    # Walking from the broken instance must not raise; safe placeholder is used.
    nodes = _walk(instance, depth=2, fanout=3, seen={id(instance)})
    assert isinstance(nodes, list)
    assert holder  # keep referrer live


def test_referrer_node_and_report_instantiate() -> None:
    """model_rebuild() resolves the self-referential field so models construct."""
    child = ReferrerNode(type_name="dict", detail="{...}")
    parent = ReferrerNode(type_name="list", detail="[...]", referrers=[child])
    report = ReferrerReport(type_name="Foo", live_count=2, samples=[parent])

    assert report.samples[0].referrers[0].type_name == "dict"
    assert report.live_count == 2


class _ReferrerTarget:
    """Distinctly-named class so the capture can find its live instances by name."""


def test_referrer_report_capture_traces_live_instances() -> None:
    """capture() finds live instances of the named class and walks up to a holder."""
    held = [_ReferrerTarget(), _ReferrerTarget()]  # a 'list' holder keeps them live

    report = ReferrerReport.capture("_ReferrerTarget", depth=3, fanout=3, samples=2)

    assert report.type_name == "_ReferrerTarget"
    assert report.live_count >= 2
    assert len(report.samples) == 2
    assert all(node.type_name == "_ReferrerTarget" for node in report.samples)
    assert held  # keep the targets alive through the assertion


def test_referrer_report_capture_caps_samples_and_handles_absent_type() -> None:
    """samples slices to the cap; an unknown type yields an empty, zero-count report."""
    held = [_ReferrerTarget() for _ in range(4)]
    capped = ReferrerReport.capture("_ReferrerTarget", samples=1)
    assert len(capped.samples) == 1

    missing = ReferrerReport.capture("ClassThatHasNoLiveInstances_xyz", samples=3)
    assert missing.live_count == 0
    assert missing.samples == []
    assert held  # keep the targets alive through the assertion


# --- helper smoke -------------------------------------------------------------


def test_read_rss_bytes_returns_non_negative() -> None:
    assert _read_rss_bytes() >= 0


def test_memory_sample_model_roundtrips() -> None:
    sample = MemorySample(label="x", iteration=2, heap_bytes=10, rss_bytes=20, gc_objects=30)
    restored = MemorySample.model_validate_json(sample.model_dump_json())
    assert restored == sample


def test_trend_format_renders_verdict_samples_types_and_sites() -> None:
    trend = MemoryTrend(
        rss_available=True,
        heap_growth_bytes=0,
        rss_growth_bytes=0,
        object_growth=0,
        samples=[
            MemorySample(label="warm", iteration=0, heap_bytes=10, rss_bytes=20, gc_objects=5)
        ],
        top_type_growth=[TypeGrowth(type_name="Foo", baseline=1, final=4, delta=3)],
        top_alloc_sites=["foo.py:10: size=1 KiB"],
    )

    out = trend.format()

    assert out.splitlines()[0] == trend.verdict
    assert "iter   0 warm" in out
    assert "Foo" in out
    assert "foo.py:10" in out


def test_trend_format_uses_na_when_rss_unavailable() -> None:
    trend = MemoryTrend(
        rss_available=False,
        heap_growth_bytes=0,
        rss_growth_bytes=0,
        object_growth=0,
        samples=[MemorySample(label="x", iteration=0, heap_bytes=10, rss_bytes=0, gc_objects=5)],
    )
    assert "rss=       n/a" in trend.format()


def test_alloc_sites_empty_without_baseline_snapshot() -> None:
    """report() before start() has no snapshot ⇒ _alloc_sites returns []."""
    sampler = MemorySampler()
    assert sampler._alloc_sites(top=5) == []
