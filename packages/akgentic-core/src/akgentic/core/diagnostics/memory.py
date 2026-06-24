"""Memory leak-detection primitives — object census, heap-vs-RSS sampler, referrer walk.

Pure, infrastructure-agnostic toolkit (stdlib + pydantic only — no FastAPI, no
sibling-package imports) so any package layered on ``akgentic-core`` shares one
diagnostic surface instead of copying it. The FastAPI debug router that exposes
these primitives is a shared worker route in ``akgentic-infra`` (ADR-015 §2); it
stays out of core to preserve core's zero-infrastructure-deps invariant.

``ObjectCensus.capture`` snapshots live-instance counts per class.

``ObjectCensus.diff`` ranks the per-class growth between two snapshots (the A/B
leak primitive — which *types* accumulated). 

``ReferrerReport.capture`` then names *who* still holds a leaked type, 
walking ``gc.get_referrers`` from sampled instances up to the long-lived 
root pinning them. ``MemorySampler`` records, once per iteration, three 
independent signals so a plateau can be classified instead of guessed at:

* **heap** — ``tracemalloc`` current traced bytes (live Python objects). Grows
  linearly with iterations ⇒ a real object leak.
* **rss** — process resident set size (OS footprint). Grows while heap stays
  flat ⇒ allocator/arena retention (CPython keeps freed arenas), *not* a leak.
* **object census** — ``gc.get_objects`` counts per type, so the report can name
  which object types are actually accumulating.

Each sample runs ``gc.collect()`` first, so cycles are reclaimed and only truly
retained memory is measured. Stdlib only; RSS uses ``psutil`` if importable,
else ``/proc`` on Linux, else reports unavailable.
"""

from __future__ import annotations

import gc
import os
import tracemalloc
from pathlib import Path

from pydantic import BaseModel, Field


def census_by_type() -> dict[str, int]:
    """Live-instance count per type name across the whole heap (no gc.collect)."""
    counts: dict[str, int] = {}
    for obj in gc.get_objects():
        name = type(obj).__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


# A run is a likely real leak if EITHER the traced heap grew past this many bytes
# OR the live object count grew past this many instances (post-warmup). Heap
# catches few-large-object leaks; object count catches many-small-object leaks
# (e.g. per-actor wrappers). tracemalloc measures live Python allocations *after*
# gc.collect(), so growth there means real retention — arena retention shows up
# only in RSS, never in tracemalloc. These are the DEFAULTS; callers tune the
# bounds per run via MemoryTrend / MemorySampler params (no need to edit core).
DEFAULT_HEAP_LEAK_BYTES = 2 * 1024 * 1024
DEFAULT_OBJECT_LEAK_COUNT = 1500


class MemorySample(BaseModel):
    """One iteration's memory reading, taken after ``gc.collect()``."""

    label: str = Field(description="Caller label for this iteration")
    iteration: int = Field(description="Zero-based iteration index")
    heap_bytes: int = Field(description="tracemalloc current traced bytes (live Python heap)")
    rss_bytes: int = Field(description="Process RSS in bytes, or 0 if unavailable")
    gc_objects: int = Field(description="len(gc.get_objects()) after collection")


class TypeGrowth(BaseModel):
    """Per-type live-instance change between two censuses."""

    type_name: str = Field(description="Class name")
    baseline: int = Field(description="Live count in the baseline census")
    final: int = Field(description="Live count in the candidate census")
    delta: int = Field(description="final - baseline (positive == accumulating)")


class ObjectCensus(BaseModel):
    """A snapshot of live-instance counts per class, for A/B leak comparison.

    Capture one at the same lifecycle point in two separate processes — e.g.
    just before server shutdown in a *no-teams* run (baseline) and a
    *created-then-stopped-teams* run (candidate) — then ``diff`` them. Classes
    with a positive delta are exactly the objects the team activity left
    resident: the leak suspects. Run the two captures in SEPARATE processes so
    the baseline run's residue cannot pollute the candidate.
    """

    label: str = Field(default="", description="Caller label, e.g. 'no-teams' / 'with-teams'")
    counts: dict[str, int] = Field(
        default_factory=dict, description="type name -> live instance count (post gc.collect)"
    )

    @classmethod
    def capture(cls, label: str = "") -> ObjectCensus:
        """Collect cycles, then snapshot live counts per class. Call before shutdown."""
        gc.collect()
        return cls(label=label, counts=census_by_type())

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write the census as JSON (one file per process run)."""
        Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> ObjectCensus:
        """Read a census written by :meth:`save`."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def diff(
        baseline: ObjectCensus, candidate: ObjectCensus, top: int | None = None
    ) -> list[TypeGrowth]:
        """Per-class growth ``candidate - baseline``, positive deltas, descending.

        The ranked result is the leak: classes the candidate run retained that the
        baseline run did not. ``top`` truncates to the worst N.
        """
        names = set(baseline.counts) | set(candidate.counts)
        rows = [
            TypeGrowth(
                type_name=name,
                baseline=baseline.counts.get(name, 0),
                final=candidate.counts.get(name, 0),
                delta=candidate.counts.get(name, 0) - baseline.counts.get(name, 0),
            )
            for name in names
        ]
        rows = [r for r in rows if r.delta > 0]
        rows.sort(key=lambda r: r.delta, reverse=True)
        return rows[:top] if top is not None else rows

    @staticmethod
    def format_diff(rows: list[TypeGrowth]) -> str:
        """Render a diff as a ranked table for logs / CLI output."""
        if not rows:
            return "no class grew between the two censuses — no object leak detected"
        lines = ["leaked classes (candidate - baseline), worst first:"]
        for r in rows:
            lines.append(f"  +{r.delta:>6}  {r.type_name}  ({r.baseline} -> {r.final})")
        return "\n".join(lines)


class MemoryTrend(BaseModel):
    """Classified outcome of a sampled load run."""

    samples: list[MemorySample] = Field(default_factory=list)
    rss_available: bool = Field(description="Whether RSS could be read on this platform")
    heap_growth_bytes: int = Field(description="Last heap minus first heap")
    rss_growth_bytes: int = Field(description="Last RSS minus first RSS")
    object_growth: int = Field(description="Last gc object count minus first")
    top_type_growth: list[TypeGrowth] = Field(
        default_factory=list, description="Types accumulating the most instances"
    )
    top_alloc_sites: list[str] = Field(
        default_factory=list, description="tracemalloc allocation sites that grew most"
    )
    heap_leak_bytes: int = Field(
        default=DEFAULT_HEAP_LEAK_BYTES,
        description="Heap-growth threshold (bytes) above which a run is a likely leak",
    )
    object_leak_count: int = Field(
        default=DEFAULT_OBJECT_LEAK_COUNT,
        description="Object-growth threshold (count) above which a run is a likely leak",
    )

    @property
    def is_object_leak(self) -> bool:
        """Heap OR live-object count grew materially ⇒ objects are being retained."""
        return (
            self.heap_growth_bytes > self.heap_leak_bytes
            or self.object_growth > self.object_leak_count
        )

    @property
    def verdict(self) -> str:
        """One-line classification of the run."""
        if self.is_object_leak:
            return (
                f"LIKELY REAL LEAK — Python heap grew {_mib(self.heap_growth_bytes)}, "
                f"live objects grew +{self.object_growth} (post-warmup). See top_type_growth."
            )
        if self.rss_available and self.rss_growth_bytes > self.heap_leak_bytes:
            return (
                f"ARENA RETENTION (not an object leak) — RSS grew "
                f"{_mib(self.rss_growth_bytes)} but heap only {_mib(self.heap_growth_bytes)}; "
                "freed memory held by the allocator, not by live objects."
            )
        return f"STABLE — heap grew {_mib(self.heap_growth_bytes)}, no accumulation."

    def format(self) -> str:
        """Render trend, verdict, and the accumulating types for logs."""
        lines = [self.verdict, "  " + "-" * 70]
        for s in self.samples:
            rss = _mib(s.rss_bytes) if self.rss_available else "n/a"
            lines.append(
                f"  iter {s.iteration:>3} {s.label:<14} "
                f"heap={_mib(s.heap_bytes):>10}  rss={rss:>10}  objs={s.gc_objects}"
            )
        if self.top_type_growth:
            lines.append("  top accumulating types (final - baseline):")
            for tg in self.top_type_growth:
                lines.append(f"    +{tg.delta:>6}  {tg.type_name}  ({tg.baseline} -> {tg.final})")
        if self.top_alloc_sites:
            lines.append("  top growing allocation sites:")
            lines.extend(f"    {site}" for site in self.top_alloc_sites)
        return "\n".join(lines)


def _mib(num_bytes: int) -> str:
    """Format a byte count as MiB."""
    return f"{num_bytes / 1024 / 1024:.1f}MiB"


def _read_rss_bytes() -> int:
    """Current process RSS in bytes, or 0 if no source is available."""
    try:
        import psutil  # type: ignore[import-untyped]  # noqa: PLC0415

        return int(psutil.Process().memory_info().rss)
    except Exception:
        pass
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


class MemorySampler:
    """Per-iteration heap/RSS/object sampler for a load loop.

    Usage::

        sampler = MemorySampler()
        sampler.start()
        for i in range(n):
            run_one_iteration()
            sampler.sample(label="cycle", iteration=i)
        print(sampler.report().format())
        sampler.stop()
    """

    def __init__(
        self,
        traceback_frames: int = 10,
        heap_leak_bytes: int = DEFAULT_HEAP_LEAK_BYTES,
        object_leak_count: int = DEFAULT_OBJECT_LEAK_COUNT,
    ) -> None:
        self._frames = traceback_frames
        self._heap_leak_bytes = heap_leak_bytes
        self._object_leak_count = object_leak_count
        self._samples: list[MemorySample] = []
        self._baseline_census: dict[str, int] = {}
        self._baseline_snapshot: tracemalloc.Snapshot | None = None
        self._rss_available = _read_rss_bytes() > 0

    def start(self) -> None:
        """Begin tracing and record the baseline census/snapshot."""
        tracemalloc.start(self._frames)
        gc.collect()
        self._baseline_census = self._census()
        self._baseline_snapshot = tracemalloc.take_snapshot()
        self._samples = []

    def _census(self) -> dict[str, int]:
        """Live-instance count per type name across the whole heap."""
        return census_by_type()

    def sample(self, label: str = "", iteration: int | None = None) -> MemorySample:
        """Collect cycles, then record heap/RSS/object counts for this iteration."""
        gc.collect()
        heap_current, _peak = tracemalloc.get_traced_memory()
        sample = MemorySample(
            label=label,
            iteration=iteration if iteration is not None else len(self._samples),
            heap_bytes=heap_current,
            rss_bytes=_read_rss_bytes(),
            gc_objects=len(gc.get_objects()),
        )
        self._samples.append(sample)
        return sample

    def report(self, top: int = 15) -> MemoryTrend:
        """Build the classified trend from the recorded samples."""
        first, last = self._samples[0], self._samples[-1]
        return MemoryTrend(
            samples=self._samples,
            rss_available=self._rss_available,
            heap_growth_bytes=last.heap_bytes - first.heap_bytes,
            rss_growth_bytes=last.rss_bytes - first.rss_bytes,
            object_growth=last.gc_objects - first.gc_objects,
            top_type_growth=self._type_growth(top),
            top_alloc_sites=self._alloc_sites(top),
            heap_leak_bytes=self._heap_leak_bytes,
            object_leak_count=self._object_leak_count,
        )

    def _type_growth(self, top: int) -> list[TypeGrowth]:
        """Types with the largest positive instance growth since baseline."""
        final = self._census()
        growth = [
            TypeGrowth(
                type_name=name,
                baseline=self._baseline_census.get(name, 0),
                final=count,
                delta=count - self._baseline_census.get(name, 0),
            )
            for name, count in final.items()
        ]
        growth = [g for g in growth if g.delta > 0]
        growth.sort(key=lambda g: g.delta, reverse=True)
        return growth[:top]

    def _alloc_sites(self, top: int) -> list[str]:
        """tracemalloc allocation sites that grew most since baseline."""
        if self._baseline_snapshot is None:
            return []
        current = tracemalloc.take_snapshot()
        stats = current.compare_to(self._baseline_snapshot, "lineno")
        return [str(stat) for stat in stats[:top]]

    def stop(self) -> None:
        """Stop tracing."""
        tracemalloc.stop()


# Referrer types that are never the leak root — the walk's own plumbing and
# generic frames — so the chain shows the real holder, not our call stack.
_SKIP_REFERRER_TYPES = frozenset({"frame", "ReferrerNode", "list"})


class ReferrerNode(BaseModel):
    """One hop in a referrer chain: who references the object below it."""

    type_name: str = Field(description="Class name of the referring object")
    detail: str = Field(description="Trimmed repr of the referrer")
    referrers: list[ReferrerNode] = Field(
        default_factory=list, description="What references THIS referrer (next hop up)"
    )


class ReferrerReport(BaseModel):
    """Referrer chains for sampled live instances of one class — names the leak root.

    Build one with :meth:`capture`, passing a class flagged by an
    :class:`ObjectCensus` diff (e.g. the worst-growing type): it samples live
    instances and walks up ``gc.get_referrers`` to the long-lived object still
    pinning them, so the report points at the holder rather than the leaked type.
    """

    type_name: str = Field(description="Class whose holders were traced")
    live_count: int = Field(description="Total live instances of this class")
    samples: list[ReferrerNode] = Field(
        default_factory=list, description="One referrer tree per sampled instance"
    )

    @classmethod
    def capture(
        cls,
        type_name: str,
        *,
        depth: int = 4,
        fanout: int = 3,
        samples: int = 3,
        newest: bool = True,
    ) -> ReferrerReport:
        """Trace who still holds live instances of ``type_name`` — names the leak root.

        Collects cycles, samples ``samples`` live instances of the class (the LAST in
        heap order when ``newest`` — the ones most likely allocated during a load run,
        i.e. the leaked ones), and walks up ``gc.get_referrers`` ``depth`` hops
        (``fanout`` per hop) so each chain ends at the long-lived root pinning them.
        """
        gc.collect()
        instances = [o for o in gc.get_objects() if type(o).__name__ == type_name]
        chosen = instances[-samples:] if newest else instances[:samples]
        trees = [
            ReferrerNode(
                type_name=type_name,
                detail=_short(inst),
                referrers=_walk(inst, depth, fanout, {id(inst), id(instances), id(chosen)}),
            )
            for inst in chosen
        ]
        return cls(type_name=type_name, live_count=len(instances), samples=trees)


ReferrerNode.model_rebuild()  # resolve the self-referential `referrers` field


def _short(obj: object) -> str:
    """Trimmed repr for a referrer (never raises, never huge)."""
    try:
        rep = repr(obj)
    except Exception:  # noqa: BLE001 — a broken __repr__ must not break the drill
        return f"<{type(obj).__name__} repr failed>"
    return rep[:120] + "…" if len(rep) > 120 else rep


def _walk(obj: object, depth: int, fanout: int, seen: set[int]) -> list[ReferrerNode]:
    """Walk up gc.get_referrers from ``obj`` to ``depth``, ``fanout`` per level."""
    if depth <= 0:
        return []
    nodes: list[ReferrerNode] = []
    for ref in gc.get_referrers(obj):
        if type(ref).__name__ in _SKIP_REFERRER_TYPES or id(ref) in seen:
            continue
        seen.add(id(ref))
        nodes.append(
            ReferrerNode(
                type_name=type(ref).__name__,
                detail=_short(ref),
                referrers=_walk(ref, depth - 1, fanout, seen),
            )
        )
        if len(nodes) >= fanout:
            break
    return nodes
