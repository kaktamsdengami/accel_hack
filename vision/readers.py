"""Pluggable brand readers and surface detectors used by the router (T14). Real implementations drop in later.

The router (agents/router_policy.py, workers/router.py) only talks to these protocols:

    class SurfaceDetector(Protocol):           # T07 (OWLv2 / Grounding DINO on Modal)
        def detect(self, group: FrameGroup) -> list[DetectedSurface]: ...

    class BrandReader(Protocol):               # T09 memory (pgvector), T08 Claude vision, GPT second opinion
        reader: Reader                          # contracts enum: memory | claude | gpt | ...
        model: str                              # logged to ClickHouse agent_traces
        expected_cost_usd: float                # cost of ONE read() call, reserved from the budget first
        def read(self, group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand]) -> ReaderResult: ...

Rules for implementers:
- `read` gets only the boxes the router wants read (a subset when escalating) and must return one
  `BoxRead` per box, same `box_id`. Unknown / not a sponsor -> `brand_id=None`. Only answer with brand_ids
  from `sponsors`.
- `confidence` is the reader's probability that `brand_id` is right (0-1). `margin` = top-1 minus top-2 score
  on the same scale (memory: similarity gap between the two nearest DIFFERENT brands; Claude: ask for the
  runner-up and its confidence). Memory also fills `distance` (cosine distance to the nearest reference) and
  `refs` (active references of that brand at this stadium/surface type), which drive escalation.
- Set `unsure=True` when the reader itself knows it cannot answer (no references at all, unreadable crop).
- Report real `cost_usd`, `latency_ms`, `tokens_in`, `tokens_out`. Raise or set `error` on failure; the router
  then marks the seconds unmeasured with reason `reader_error` (never guesses).
- Images are not on Kafka. Fetch the JPEG of `group.representative.frame_id` from Postgres `evidence`
  (written by ingest) and crop with each box's normalised bbox.

Everything named `Stub*` is a deterministic fixture-driven fake for tests and the SYNTHETIC demo. Stubs report
cost from their constructor (default: an assumed Claude price), never a measured spend.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.contracts import BBox, Frame, Reader, Side, SurfaceBox, SurfaceType


@dataclass(frozen=True)
class Brand:
    """A sponsor the readers may answer with (Postgres `brands`)."""
    brand_id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass
class FrameGroup:
    """Near-identical frames of one shot that are read once (same `group_id`)."""
    match_id: str
    group_id: str
    shot_id: str
    frames: list[Frame]
    club_id: str = ""
    stadium_id: str = ""

    @property
    def representative(self) -> Frame:
        return next((f for f in self.frames if f.is_representative), self.frames[0])

    @property
    def t(self) -> float:
        return min(f.t for f in self.frames)


@dataclass(frozen=True)
class DetectedSurface:
    """A surface box plus image-quality facts the contracts `SurfaceBox` does not carry (yet).

    `sharpness=None` means not measured: the router then judges below_threshold on area only."""
    box: SurfaceBox
    sharpness: float | None = None
    occluded: bool = False


@dataclass(frozen=True)
class BoxRead:
    box_id: str
    brand_id: str | None
    confidence: float
    margin: float = 1.0               # top-1 minus top-2 score; small = two brands look alike
    distance: float | None = None     # memory only: cosine distance to the nearest reference
    refs: int | None = None           # memory only: references for this brand at this stadium/surface
    runner_up: str | None = None      # brand_id of the second best answer, when known
    unsure: bool = False


@dataclass
class ReaderResult:
    reader: Reader
    model: str
    boxes: list[BoxRead]
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None

    def by_box(self) -> dict[str, BoxRead]:
        return {b.box_id: b for b in self.boxes}


@runtime_checkable
class BrandReader(Protocol):
    reader: Reader
    model: str
    expected_cost_usd: float

    def read(self, group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand]) -> ReaderResult: ...


@runtime_checkable
class SurfaceDetector(Protocol):
    def detect(self, group: FrameGroup) -> list[DetectedSurface]: ...


def surface_key(box: SurfaceBox) -> str:
    """`<surface_type>|<side>|<board_id or ->`: the same physical surface across frames of a group."""
    return f"{box.surface_type}|{box.side}|{box.board_id or '-'}"


# --- stubs (deterministic, fixture driven) ---------------------------------------------------------------

@dataclass(frozen=True)
class StubAnswer:
    brand_id: str | None
    confidence: float
    margin: float = 0.5
    distance: float | None = None
    refs: int | None = None
    runner_up: str | None = None
    unsure: bool = False


# A fixture is a mapping or a function. Mapping keys are tried in order: box_id, "<group_id>|<surface_key>",
# "<surface_key>". A function gets (group, box) and returns a StubAnswer or None. No answer = unsure.
Fixture = Mapping[str, StubAnswer] | Callable[[FrameGroup, SurfaceBox], StubAnswer | None]


def _lookup(fixture: Fixture, group: FrameGroup, box: SurfaceBox) -> StubAnswer | None:
    if callable(fixture):
        return fixture(group, box)
    for key in (box.box_id, f"{group.group_id}|{surface_key(box)}", surface_key(box)):
        if key in fixture:
            return fixture[key]
    return None


@dataclass
class _StubReader:
    fixture: Fixture
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    fail_groups: set[str] = field(default_factory=set)   # group_ids whose read raises (reader_error tests)
    reader: Reader = Reader.memory
    model: str = "stub"
    expected_cost_usd: float = 0.0
    calls: int = 0

    def read(self, group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand]) -> ReaderResult:
        self.calls += 1
        if group.group_id in self.fail_groups:
            raise TimeoutError(f"{self.model} timed out")
        allowed = {b.brand_id for b in sponsors}
        out = []
        for box in boxes:
            a = _lookup(self.fixture, group, box)
            if a is None:
                out.append(BoxRead(box.box_id, None, 0.0, margin=0.0, unsure=True))
                continue
            brand = a.brand_id if (a.brand_id is None or a.brand_id in allowed) else None
            out.append(BoxRead(box.box_id, brand, a.confidence, a.margin, a.distance, a.refs, a.runner_up,
                               a.unsure))
        n = max(len(boxes), 1)
        return ReaderResult(self.reader, self.model, out, cost_usd=self.cost_usd, latency_ms=self.latency_ms,
                            tokens_in=self.tokens_in * n, tokens_out=self.tokens_out * n)


def StubMemoryReader(fixture: Fixture, latency_ms: float = 15.0, **kw) -> _StubReader:  # noqa: N802
    """Fake pgvector memory reader (free). Fixture answers should set distance/margin/refs."""
    return _StubReader(fixture, latency_ms=latency_ms, reader=Reader.memory, model="stub-memory",
                       expected_cost_usd=0.0, **kw)


# Assumed (not measured) price of one Claude vision call on a 640 px frame; replaced by real usage in T08.
STUB_CLAUDE_COST_USD = 0.012


def StubClaudeReader(fixture: Fixture, cost_usd: float = STUB_CLAUDE_COST_USD, latency_ms: float = 900.0,  # noqa: N802
                     **kw) -> _StubReader:
    """Fake Claude vision reader. Charges `cost_usd` per call so budget logic can be exercised."""
    return _StubReader(fixture, cost_usd=cost_usd, latency_ms=latency_ms, tokens_in=1600, tokens_out=60,
                       reader=Reader.claude, model="stub-claude", expected_cost_usd=cost_usd, **kw)


def StubGptReader(fixture: Fixture, cost_usd: float = 0.01, latency_ms: float = 1100.0,  # noqa: N802
                  **kw) -> _StubReader:
    """Fake GPT vision second opinion (only used on conflicts)."""
    return _StubReader(fixture, cost_usd=cost_usd, latency_ms=latency_ms, tokens_in=1500, tokens_out=50,
                       reader=Reader.gpt, model="stub-gpt", expected_cost_usd=cost_usd, **kw)


# --- stub surface detector + SYNTHETIC scene ---------------------------------------------------------------

SYNTH_BRANDS = [Brand("b-synth-alpha", "SYNTHETIC Alpha"), Brand("b-synth-beta", "SYNTHETIC Beta"),
                Brand("b-synth-gamma", "SYNTHETIC Gamma")]


@dataclass
class StubSurfaceDetector:
    """Every frame shows a far LED perimeter and a near static board 'A' (fixed boxes, ids from frame_id)."""
    sharpness: float | None = 120.0

    def detect(self, group: FrameGroup) -> list[DetectedSurface]:
        out = []
        for f in group.frames:
            out.append(DetectedSurface(SurfaceBox(
                box_id=f"{f.frame_id}:led", frame_id=f.frame_id, bbox=BBox(x=0.10, y=0.06, w=0.55, h=0.04),
                surface_type=SurfaceType.led_perimeter, side=Side.far, score=0.9), self.sharpness))
            out.append(DetectedSurface(SurfaceBox(
                box_id=f"{f.frame_id}:boardA", frame_id=f.frame_id, bbox=BBox(x=0.20, y=0.80, w=0.30, h=0.03),
                surface_type=SurfaceType.static_board, side=Side.near, board_id="A", score=0.85),
                self.sharpness))
        return out


def _h(*parts: str) -> float:
    """Deterministic pseudo-random number in [0, 1) from strings."""
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16) / 2**32


def synthetic_truth(group: FrameGroup, box: SurfaceBox) -> str:
    """SYNTHETIC truth: LED rotates alpha/beta/gamma every 8 s; board A is alpha, then beta after 30 s."""
    brands = [b.brand_id for b in SYNTH_BRANDS]
    if box.surface_type == SurfaceType.led_perimeter:
        return brands[int(group.t // 8) % 3]
    return brands[0] if group.t < 30 else brands[1]


def synthetic_memory(group: FrameGroup, box: SurfaceBox) -> StubAnswer:
    """SYNTHETIC memory answers: mostly confident, some ambiguous / far / thin / wrong (invented)."""
    truth = synthetic_truth(group, box)
    other = next(b.brand_id for b in SYNTH_BRANDS if b.brand_id != truth)
    u = _h(group.group_id, surface_key(box))
    if u < 0.12:   # memory confidently wrong-ish: close call between two brands
        return StubAnswer(other, 0.55, margin=0.03, distance=0.22, refs=6, runner_up=truth)
    if u < 0.22:   # far from every reference
        return StubAnswer(truth, 0.4, margin=0.2, distance=0.48, refs=6, runner_up=other)
    if u < 0.30:   # gamma-style thin memory
        return StubAnswer(truth, 0.8, margin=0.3, distance=0.2, refs=1, runner_up=other)
    return StubAnswer(truth, 0.93, margin=0.35, distance=0.12 + 0.1 * u, refs=8, runner_up=other)


def synthetic_claude(group: FrameGroup, box: SurfaceBox) -> StubAnswer:
    return StubAnswer(synthetic_truth(group, box), 0.9, margin=0.6)
