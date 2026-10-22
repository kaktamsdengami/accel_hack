"""Evaluation metrics, exactly the definitions in docs/CONTRACTS.md section 7.

Brand time is the union of its intervals (overlaps counted once). All functions are pure.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle with eval.gold
    from eval.gold import Gold

Span = tuple[float, float]

SURFACE_NAMES = {
    "led_perimeter": "LED perimeter",
    "static_board": "Board",
    "shirt": "Shirt",
    "banner": "Banner",
    "backdrop": "Backdrop",
    "other": "Other",
}


def norm_label(label: str | None) -> str:
    """Case/space-insensitive surface label ('' = unlabelled)."""
    return " ".join((label or "").lower().split())


# --- interval arithmetic --------------------------------------------------------

def merge(spans: Iterable[Span]) -> list[Span]:
    """Sorted, non-overlapping union of spans (empty or inverted spans dropped)."""
    out: list[Span] = []
    for s, e in sorted(sp for sp in spans if sp[1] > sp[0]):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def union_length(spans: Iterable[Span]) -> float:
    return sum(e - s for s, e in merge(spans))


def overlap_length(a: Iterable[Span], b: Iterable[Span]) -> float:
    """Seconds covered by both unions."""
    ma, mb = merge(a), merge(b)
    i = j = 0
    total = 0.0
    while i < len(ma) and j < len(mb):
        lo, hi = max(ma[i][0], mb[j][0]), min(ma[i][1], mb[j][1])
        if hi > lo:
            total += hi - lo
        if ma[i][1] < mb[j][1]:
            i += 1
        else:
            j += 1
    return total


# --- predictions ----------------------------------------------------------------

class Pred:
    """One predicted interval, reduced to what the metrics need."""

    __slots__ = ("brand_id", "label", "t_start", "t_end")

    def __init__(self, brand_id: str, label: str, t_start: float, t_end: float):
        self.brand_id, self.label, self.t_start, self.t_end = brand_id, norm_label(label), t_start, t_end

    @property
    def span(self) -> Span:
        return (self.t_start, self.t_end)


def pred_label(item: dict) -> str:
    """'LED perimeter · far' from surface_type + side, unless an explicit surface_label is given."""
    if item.get("surface_label"):
        return str(item["surface_label"])
    st = item.get("surface_type")
    if not st:
        return ""
    name = SURFACE_NAMES.get(str(st), str(st))
    side = item.get("side")
    return f"{name} · {side}" if side and side != "unknown" else name


def load_pred(path: str | Path) -> list[Pred]:
    """Predicted intervals JSON: a list of contracts `Interval` objects (or {"intervals": [...]})."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("intervals", [])
    return [Pred(d["brand_id"], pred_label(d), float(d["t_start"]), float(d["t_end"])) for d in data]


def _pred_spans(pred: Sequence[Pred], brand_id: str, label: str | None = None) -> list[Span]:
    return [p.span for p in pred if p.brand_id == brand_id and (label is None or p.label == label)]


# --- accuracy metrics -----------------------------------------------------------

def capture_pct(pred: Sequence[Pred], gold: Gold) -> dict:
    """Capture % with the variant used. Interval overlap when all gold rows are intervals,
    otherwise the weaker totals variant sum(min(pred_s, gold_s)) / sum(gold_s)."""
    gold_total = sum(gold.brand_seconds(b) for b in gold.brands)
    if gold.has_intervals:
        variant = "intervals"
        num = sum(overlap_length(_pred_spans(pred, b), gold.intervals(b)) for b in gold.brands)
    else:
        variant = "totals"
        num = sum(min(union_length(_pred_spans(pred, b)), gold.brand_seconds(b)) for b in gold.brands)
    return {
        "variant": variant,
        "value": None if gold_total == 0 else 100.0 * num / gold_total,
        "numerator_s": num,
        "gold_s": gold_total,
    }


def false_credit_pct(pred: Sequence[Pred], gold: Gold) -> dict:
    """Pred seconds not overlapping gold / pred seconds. Defined for interval gold only (else None).
    Pred credited to brands absent from gold counts fully as false credit."""
    if not gold.has_intervals:
        return {"value": None, "pred_s": None, "false_s": None, "note": "gold has totals only"}
    brands = sorted({p.brand_id for p in pred} | set(gold.brands))
    pred_s = false_s = 0.0
    for b in brands:
        spans = _pred_spans(pred, b)
        total = union_length(spans)
        pred_s += total
        false_s += total - overlap_length(spans, gold.intervals(b))
    return {"value": None if pred_s == 0 else 100.0 * false_s / pred_s, "pred_s": pred_s, "false_s": false_s}


def mae_per_brand(pred: Sequence[Pred], gold: Gold, sponsors: Sequence[str] | None = None) -> dict:
    """Mean |pred_s - gold_s| over the sponsor list (default: brands in gold). Missing brand = 0 s."""
    brands = list(sponsors) if sponsors else gold.brands
    errs = {b: abs(union_length(_pred_spans(pred, b)) - gold.brand_seconds(b)) for b in brands}
    return {
        "value": None if not brands else sum(errs.values()) / len(brands),
        "n_brands": len(brands),
        "per_brand": errs,
    }


def mae_per_surface(pred: Sequence[Pred], gold: Gold, sponsors: Sequence[str] | None = None) -> dict:
    """MAE per (surface_label, brand) over pairs in gold (missing = 0 s) plus sponsor pairs seen in pred
    on a label the gold uses. Needs surface labels in gold, else value is None."""
    if not gold.has_surface_labels:
        return {"value": None, "n_pairs": 0, "per_pair": {}, "note": "gold has no surface labels"}
    labels = {lab for lab, _ in gold.surface_pairs()}
    sponsor_set = set(sponsors) if sponsors else set(gold.brands)
    pairs = set(gold.surface_pairs()) | {
        (p.label, p.brand_id) for p in pred if p.label in labels and p.brand_id in sponsor_set
    }
    errs = {
        f"{lab} | {b}": abs(union_length(_pred_spans(pred, b, lab)) - gold.pair_seconds(lab, b))
        for lab, b in sorted(pairs)
    }
    return {"value": sum(errs.values()) / len(errs), "n_pairs": len(errs), "per_pair": errs}


# --- cost, routing, latency -----------------------------------------------------

def cost_per_match_minute(decisions: Sequence, minutes: float, extra_usd: float = 0.0) -> dict:
    """(model API spend from DecisionLog cost_usd + extra e.g. Modal GPU spend) / processed minutes."""
    api = sum(_get(d, "cost_usd", 0.0) for d in decisions)
    total = api + extra_usd
    return {
        "value": None if minutes <= 0 else total / minutes,
        "usd_total": total,
        "usd_api": api,
        "usd_extra": extra_usd,
        "minutes": minutes,
    }


def claude_call_share(decisions: Sequence) -> dict:
    """Groups read by claude / groups read by any reader (one count per group_id, last decision wins)."""
    final: dict[str, str] = {}
    for d in decisions:
        final[str(_get(d, "group_id", _get(d, "decision_id", id(d))))] = str(_get(d, "chosen_reader", ""))
    n = len(final)
    claude = sum(1 for r in final.values() if r == "claude")
    by_reader: dict[str, int] = {}
    for r in final.values():
        by_reader[r] = by_reader.get(r, 0) + 1
    return {"value": None if n == 0 else claude / n, "claude_groups": claude, "groups": n,
            "groups_by_reader": by_reader}


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy default), q in 0-100."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _ts(v) -> float:
    if isinstance(v, datetime):
        return v.timestamp()
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    return float(v)


def latency_stats(pairs: Sequence[tuple]) -> dict:
    """Latency from (frame_published_wall, stats_sse_wall) pairs (datetime, ISO string or epoch s)."""
    lat = [_ts(b) - _ts(a) for a, b in pairs]
    return {"p50_s": percentile(lat, 50), "p95_s": percentile(lat, 95), "n": len(lat)}


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def compute_all(pred: Sequence[Pred], gold: Gold, decisions: Sequence, minutes: float,
                sponsors: Sequence[str] | None = None, latency_pairs: Sequence[tuple] = (),
                extra_usd: float = 0.0) -> dict:
    return {
        "capture_pct": capture_pct(pred, gold),
        "false_credit_pct": false_credit_pct(pred, gold),
        "mae_s_per_brand": mae_per_brand(pred, gold, sponsors),
        "mae_s_per_surface": mae_per_surface(pred, gold, sponsors),
        "cost_per_match_minute": cost_per_match_minute(decisions, minutes, extra_usd),
        "claude_call_share": claude_call_share(decisions),
        "latency": latency_stats(latency_pairs),
    }
