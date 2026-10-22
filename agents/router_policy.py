"""Router policy (T14): which reader identifies the brands of one frame group, within the match budget.

Cheapest first: memory (pgvector, ~free) -> Claude vision -> GPT vision (optional, only on conflicts).

A memory answer for a box is accepted unless one of these escalation causes holds (checked in this order):
- `unsure`   the reader says it cannot answer (no references, unreadable crop);
- `far`      cosine distance to the nearest reference > `memory_max_distance`;
- `margin`   top-1 minus top-2 score < `memory_min_margin` (two brands look alike);
- `thin`     fewer than `min_refs` references of that brand at this stadium;
- `off_list` the answer is not on this match's sponsor list.
Escalated boxes go to Claude in ONE call. If Claude disagrees with a memory answer that was close (not `far`
or `unsure`), that is a `conflict`: GPT is asked when available and affordable, majority wins; otherwise
Claude's answer is kept at reduced confidence and the reason says "conflict" (the detective picks it up).

Budget guard: before every paid call, the reader's `expected_cost_usd` must be <= what is left AND must be
reserved atomically from the `Budget`; otherwise the reader is not called and the decision is written with
confidence 0 and a reason starting `budget_cap` (so the aggregation marks those seconds unmeasured with
reason budget_cap). The same applies to reader failures (`reader_error`) and to groups where no reader could
read anything (`low_confidence`). Boxes memory read confidently keep their brand in every case.

Thresholds are per stadium (Postgres `router_thresholds`, defaults from env), and `learn_thresholds` picks
the loosest memory thresholds that keep accuracy on confirmed past reads (the T15 self-improving loop).

`route_group` is I/O free apart from the injected readers and budget, so it is unit tested with stubs.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from app import config  # noqa: F401  (loads .env so the env defaults below see it)
from app.contracts import DecisionLog, Reader, SurfaceBox
from app.labels import surface_label
from vision.readers import BoxRead, Brand, BrandReader, FrameGroup, ReaderResult

# --- thresholds ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    memory_max_distance: float = 0.35
    memory_min_margin: float = 0.08
    min_refs: int = 3
    claude_min_confidence: float = 0.5
    conflict_confidence: float = 0.6     # confidence cap when a conflict stays unresolved
    source: str = "default"              # default | learned | human


def default_thresholds() -> Thresholds:
    """Env overrides (docs/CONTRACTS.md section 9): MEMORY_MAX_DISTANCE, MEMORY_MIN_MARGIN, MEMORY_MIN_REFS,
    CLAUDE_MIN_CONFIDENCE."""
    d = Thresholds()
    return Thresholds(
        memory_max_distance=float(os.getenv("MEMORY_MAX_DISTANCE", d.memory_max_distance)),
        memory_min_margin=float(os.getenv("MEMORY_MIN_MARGIN", d.memory_min_margin)),
        min_refs=int(os.getenv("MEMORY_MIN_REFS", d.min_refs)),
        claude_min_confidence=float(os.getenv("CLAUDE_MIN_CONFIDENCE", d.claude_min_confidence)),
    )


def load_thresholds(stadium_id: str, pg=None) -> Thresholds:
    """Per-stadium thresholds from Postgres `router_thresholds`; env defaults when absent or unreachable."""
    base = default_thresholds()
    if pg is None:
        from app import stores
        pg = stores.postgres()
    try:
        rows = pg.fetch("SELECT memory_max_distance, memory_min_margin, min_refs, source "
                        "FROM router_thresholds WHERE stadium_id = %s", (stadium_id,))
    except Exception:
        return base
    if not rows:
        return base
    dist, margin, refs, source = rows[0]
    return replace(base, memory_max_distance=float(dist), memory_min_margin=float(margin), min_refs=int(refs),
                   source=str(source))


def save_thresholds(stadium_id: str, th: Thresholds, samples: int = 0, accuracy: float | None = None,
                    escalation_rate: float | None = None, pg=None) -> None:
    if pg is None:
        from app import stores
        pg = stores.postgres()
    pg.execute(
        "INSERT INTO router_thresholds (stadium_id, memory_max_distance, memory_min_margin, min_refs, "
        "source, samples, accuracy, escalation_rate, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (stadium_id) DO UPDATE SET "
        "memory_max_distance = EXCLUDED.memory_max_distance, memory_min_margin = EXCLUDED.memory_min_margin, "
        "min_refs = EXCLUDED.min_refs, source = EXCLUDED.source, samples = EXCLUDED.samples, "
        "accuracy = EXCLUDED.accuracy, escalation_rate = EXCLUDED.escalation_rate, updated_at = now()",
        (stadium_id, th.memory_max_distance, th.memory_min_margin, th.min_refs, th.source, samples, accuracy,
         escalation_rate))


def escalation_cause(read: BoxRead, th: Thresholds, sponsor_ids: set[str] | None = None) -> str | None:
    """None = accept the memory answer; otherwise unsure | far | margin | thin | off_list."""
    if read.unsure or read.brand_id is None:
        return "unsure"
    if read.distance is not None and read.distance > th.memory_max_distance:
        return "far"
    if read.margin < th.memory_min_margin:
        return "margin"
    if read.refs is not None and read.refs < th.min_refs:
        return "thin"
    if sponsor_ids is not None and read.brand_id not in sponsor_ids:
        return "off_list"
    return None


# --- learning (T15 calls this) ------------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryObservation:
    """One past memory read of one box (from agent_traces output, see workers/router.py)."""
    key: str                  # box_id
    brand_id: str | None
    distance: float | None
    margin: float
    refs: int | None = None
    unsure: bool = False

    def as_read(self) -> BoxRead:
        return BoxRead(self.key, self.brand_id, 0.0, self.margin, self.distance, self.refs,
                       unsure=self.unsure)


@dataclass(frozen=True)
class LearnResult:
    thresholds: Thresholds
    samples: int
    accuracy: float
    escalation_rate: float
    baseline_accuracy: float
    baseline_escalation_rate: float


DISTANCE_GRID = tuple(round(0.05 + 0.025 * i, 3) for i in range(23))   # 0.05 .. 0.60
MARGIN_GRID = tuple(round(0.01 * i, 3) for i in range(31))             # 0.00 .. 0.30


def _score(obs: Sequence[MemoryObservation], truth: Mapping[str, str | None], th: Thresholds,
           escalated_accuracy: float) -> tuple[float, float]:
    correct = escalated = 0.0
    for o in obs:
        if escalation_cause(o.as_read(), th) is None:
            correct += o.brand_id == truth[o.key]
        else:
            escalated += 1
            correct += escalated_accuracy
    n = len(obs)
    return correct / n, escalated / n


def learn_thresholds(past_decisions: Iterable[MemoryObservation], gold_or_confirmed: Mapping[str, str | None],
                     current: Thresholds | None = None, escalated_accuracy: float = 1.0,
                     min_samples: int = 20,
                     distance_grid: Sequence[float] = DISTANCE_GRID,
                     margin_grid: Sequence[float] = MARGIN_GRID) -> LearnResult:
    """Grid search over (memory_max_distance, memory_min_margin): fewest escalations to Claude whose
    accuracy is at least the accuracy of `current`. An escalated box counts as right with probability
    `escalated_accuracy` (1.0 when the truth IS Claude's confirmed answer).
    Ties: higher accuracy, then the stricter pair.
    With fewer than `min_samples` labelled observations, `current` is returned unchanged."""
    current = current or default_thresholds()
    obs = [o for o in past_decisions if o.key in gold_or_confirmed]
    if len(obs) < min_samples:
        acc, esc = _score(obs, gold_or_confirmed, current, escalated_accuracy) if obs else (0.0, 0.0)
        return LearnResult(current, len(obs), acc, esc, acc, esc)
    base_acc, base_esc = _score(obs, gold_or_confirmed, current, escalated_accuracy)
    best: tuple | None = None
    for d in distance_grid:
        for m in margin_grid:
            th = replace(current, memory_max_distance=d, memory_min_margin=m)
            acc, esc = _score(obs, gold_or_confirmed, th, escalated_accuracy)
            if acc + 1e-9 < base_acc:
                continue
            key = (esc, -acc, d, -m)
            if best is None or key < best[0]:
                best = (key, th, acc, esc)
    if best is None or best[3] >= base_esc:
        return LearnResult(current, len(obs), base_acc, base_esc, base_acc, base_esc)
    _, th, acc, esc = best
    return LearnResult(replace(th, source="learned"), len(obs), acc, esc, base_acc, base_esc)


# --- budget -------------------------------------------------------------------------------------------------

class Budget(Protocol):
    limit_usd: float

    def left(self) -> float: ...
    def reserve(self, usd: float) -> bool: ...                 # atomic; False = would exceed the limit
    def settle(self, reserved: float, actual: float) -> None: ...  # replace a reservation by the real cost


@dataclass
class LocalBudget:
    """In-process budget (tests, single worker without Valkey)."""
    limit_usd: float
    spent_usd: float = 0.0

    def left(self) -> float:
        return round(self.limit_usd - self.spent_usd, 6)

    def reserve(self, usd: float) -> bool:
        if self.spent_usd + usd > self.limit_usd + 1e-9:
            return False
        self.spent_usd += usd
        return True

    def settle(self, reserved: float, actual: float) -> None:
        self.spent_usd += actual - reserved


# --- routing one group --------------------------------------------------------------------------------------

@dataclass
class BoxOutcome:
    box_id: str
    brand_id: str | None
    confidence: float
    reader: Reader


@dataclass
class RouteOutcome:
    decision: DecisionLog
    boxes: dict[str, BoxOutcome]
    calls: list[ReaderResult] = field(default_factory=list)
    causes: dict[str, str] = field(default_factory=dict)      # box_id -> escalation cause
    conflicts: list[str] = field(default_factory=list)        # box_ids
    unmeasured_reason: str | None = None                      # budget_cap | reader_error | low_confidence


CAUSE_ORDER = ("off_list", "far", "unsure", "thin", "margin")   # most telling cause last -> wins in reason


def _names(ids: Iterable[str | None], names: Mapping[str, str]) -> str:
    out = []
    for i in ids:
        n = "no sponsor" if i is None else names.get(i, i)
        if n not in out:
            out.append(n)
    return " and ".join([", ".join(out[:-1]), out[-1]] if len(out) > 2 else out) if out else "nothing"


def _where(box: SurfaceBox) -> str:
    return surface_label(box.surface_type, box.side, box.board_id)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _call(reader: BrandReader, group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand],
          clock: Callable[[], float]) -> ReaderResult:
    t0 = clock()
    try:
        res = reader.read(group, boxes, sponsors)
    except Exception as e:  # a reader failure must never crash the router
        res = ReaderResult(reader.reader, reader.model, [], error=f"{type(e).__name__}: {e}"[:200])
    if not res.latency_ms:
        res.latency_ms = round((clock() - t0) * 1000, 1)
    return res


def route_group(group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand],
                readers: Mapping[Reader, BrandReader], th: Thresholds, budget: Budget,
                clock: Callable[[], float] = time.perf_counter) -> RouteOutcome:
    """Decide the brand of every box of one group. Readers are called at most once each."""
    names = {b.brand_id: b.name for b in sponsors}
    sponsor_ids = set(names)
    by_id = {b.box_id: b for b in boxes}
    calls: list[ReaderResult] = []
    tried: list[Reader] = []
    final: dict[str, BoxOutcome] = {}

    def decision(chosen: Reader, confidence: float, reason: str) -> DecisionLog:
        return DecisionLog(
            decision_id=f"{group.group_id}:router", match_id=group.match_id,
            shot_id=group.shot_id, group_id=group.group_id, t=group.t, readers_tried=tried or [chosen],
            chosen_reader=chosen, confidence=round(min(max(confidence, 0.0), 1.0), 3),
            cost_usd=round(sum(c.cost_usd for c in calls), 6),
            latency_ms=round(sum(c.latency_ms for c in calls), 1), reason=reason,
            budget_left_usd=round(budget.left(), 6))

    def done(chosen: Reader, reason: str, unmeasured: str | None = None, causes=None, conflicts=None):
        for b in boxes:
            final.setdefault(b.box_id, BoxOutcome(b.box_id, None, 0.0, chosen))
        conf = 0.0 if unmeasured else sum(o.confidence for o in final.values()) / max(len(final), 1)
        return RouteOutcome(decision(chosen, conf, reason), final, calls, causes or {}, conflicts or [],
                            unmeasured)

    if not boxes:
        tried.append(Reader.own_detector)
        out = done(Reader.own_detector,
                   "No sponsor surfaces in view, so nothing needed reading and nothing was spent.")
        out.decision.confidence = 1.0
        return out

    # 1. memory
    mem = readers.get(Reader.memory)
    mem_reads: dict[str, BoxRead] = {}
    causes: dict[str, str] = {}
    if mem is not None and mem.expected_cost_usd <= budget.left() and budget.reserve(mem.expected_cost_usd):
        res = _call(mem, group, boxes, sponsors, clock)
        budget.settle(mem.expected_cost_usd, res.cost_usd)
        calls.append(res)
        tried.append(Reader.memory)
        mem_reads = res.by_box()
    for b in boxes:
        r = mem_reads.get(b.box_id)
        cause = "unsure" if r is None else escalation_cause(r, th, sponsor_ids)
        if cause is None:
            final[b.box_id] = BoxOutcome(b.box_id, r.brand_id, r.confidence, Reader.memory)
        else:
            causes[b.box_id] = cause

    if not causes:
        d = min((r.distance for r in mem_reads.values() if r.distance is not None), default=None)
        where = _where(boxes[0]) if len({_where(b) for b in boxes}) == 1 else _plural(len(boxes), "surface")
        extra = f" (closest match {d:.2f} away)" if d is not None and len(boxes) == 1 else ""
        seen = _names((o.brand_id for o in final.values()), names)
        return done(Reader.memory, f"Logo memory recognised {seen} on {where}{extra} on its own, "
                                   f"at zero model cost.")

    esc = [by_id[i] for i in causes]
    lead = max(causes.values(), key=CAUSE_ORDER.index)
    lead_box = by_id[next(i for i, c in causes.items() if c == lead)]
    lead_read = mem_reads.get(lead_box.box_id)

    def why() -> str:
        if not mem_reads:
            return "Logo memory had no answer"
        w = _where(lead_box)
        if lead == "margin":
            other = names.get(lead_read.runner_up, "a look-alike")
            return (f"Memory could not tell {names.get(lead_read.brand_id, lead_read.brand_id)} from "
                    f"{other} on {w} (margin {lead_read.margin:.2f})")
        if lead == "thin":
            return (f"Memory holds only {_plural(lead_read.refs or 0, 'reference image')} of "
                    f"{names.get(lead_read.brand_id, lead_read.brand_id)} at this stadium")
        if lead == "off_list":
            return f"Memory matched {w} to a brand that is not on this match's sponsor list"
        return f"The {w} sign looked unlike any logo in memory"

    # 2. Claude on the escalated boxes, if affordable
    cl = readers.get(Reader.claude)
    n_esc = _plural(len(esc), "uncertain surface") + (" stays" if len(esc) == 1 else " stay")
    if cl is None:
        return done(Reader.memory, f"low_confidence: {why()} and no vision model is configured, so {n_esc} "
                                   f"unmeasured.", "low_confidence", causes)
    if cl.expected_cost_usd > budget.left() or not budget.reserve(cl.expected_cost_usd):
        # Claude is NOT called, so it is not in readers_tried and the group does not count as a Claude read.
        left = max(budget.left(), 0.0)
        money = ("the match budget is used up" if left < 0.0005
                 else f"only ${left:.3f} of the match budget is left")
        return done(Reader.memory, f"budget_cap: {why()}, but {money}, so {n_esc} unmeasured instead of "
                                   f"paying ${cl.expected_cost_usd:.3f} for Claude.", "budget_cap", causes)
    res = _call(cl, group, esc, sponsors, clock)
    budget.settle(cl.expected_cost_usd, res.cost_usd)
    calls.append(res)
    tried.append(Reader.claude)
    if res.error:
        err = res.error.split(":")[0]
        return done(Reader.claude, f"reader_error: Claude did not answer ({err}), so {n_esc} "
                                   f"unmeasured rather than guessed.", "reader_error", causes)
    cl_reads = res.by_box()

    # 3. conflicts: memory had a close answer and Claude disagrees
    conflicts = []
    for b in esc:
        m, c = mem_reads.get(b.box_id), cl_reads.get(b.box_id)
        if (m and c and causes[b.box_id] in ("margin", "thin") and m.brand_id and c.brand_id
                and c.brand_id != m.brand_id):
            conflicts.append(b.box_id)
    gpt_reads: dict[str, BoxRead] = {}
    gpt = readers.get(Reader.gpt)
    gpt_skipped = None
    if conflicts and gpt is not None:
        if gpt.expected_cost_usd <= budget.left() and budget.reserve(gpt.expected_cost_usd):
            gres = _call(gpt, group, [by_id[i] for i in conflicts], sponsors, clock)
            budget.settle(gpt.expected_cost_usd, gres.cost_usd)
            calls.append(gres)
            tried.append(Reader.gpt)
            gpt_reads = {} if gres.error else gres.by_box()
            gpt_skipped = "GPT failed" if gres.error else None
        else:
            gpt_skipped = "budget too low"

    chosen = Reader.claude
    for b in esc:
        c = cl_reads.get(b.box_id)
        if c is None or c.confidence < th.claude_min_confidence:
            final[b.box_id] = BoxOutcome(b.box_id, None, c.confidence if c else 0.0, Reader.claude)
            continue
        if b.box_id in conflicts:
            g, m = gpt_reads.get(b.box_id), mem_reads[b.box_id]
            if g is not None and g.brand_id in (c.brand_id, m.brand_id):
                chosen = Reader.gpt
                agree = c if g.brand_id == c.brand_id else m
                final[b.box_id] = BoxOutcome(b.box_id, g.brand_id, max(agree.confidence, g.confidence),
                                             Reader.gpt)
            elif g is not None:
                chosen = Reader.gpt
                final[b.box_id] = BoxOutcome(b.box_id, None, 0.0, Reader.gpt)
            else:
                final[b.box_id] = BoxOutcome(b.box_id, c.brand_id, min(c.confidence, th.conflict_confidence),
                                             Reader.claude)
            continue
        final[b.box_id] = BoxOutcome(b.box_id, c.brand_id, c.confidence, Reader.claude)

    if all(o.confidence <= 0 for o in final.values()):
        return done(chosen, f"low_confidence: {why()} and Claude could not read it either, so {n_esc} "
                            f"unmeasured.", "low_confidence", causes, conflicts)
    cost = sum(c.cost_usd for c in calls)
    if conflicts:
        cb = by_id[conflicts[0]]
        mb, cbr = mem_reads[cb.box_id].brand_id, cl_reads[cb.box_id].brand_id
        head = f"Memory said {names.get(mb, mb)} but Claude said {names.get(cbr, cbr)} on {_where(cb)}"
        if gpt_reads:
            winner = _names([final[cb.box_id].brand_id], names)
            reason = f"{head}; GPT broke the tie: {winner} (${cost:.3f} total)."
        else:
            tail = f" (no second opinion: {gpt_skipped})" if gpt_skipped else ""
            reason = f"{head}; Claude's answer counts at lower confidence and the conflict is flagged{tail}."
    else:
        got = [final[b.box_id].brand_id for b in esc]
        same = all(mem_reads.get(b.box_id) and mem_reads[b.box_id].brand_id == final[b.box_id].brand_id
                   for b in esc)
        verdict = f"confirmed {_names(got, names)}" if same else f"it is {_names(got, names)}"
        reason = f"{why()}, so Claude checked {_plural(len(esc), 'surface')} for ${cost:.3f}: {verdict}."
    return done(chosen, reason, None, causes, conflicts)
