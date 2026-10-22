"""Load the club's hand count (gold). Formats are documented in eval/runs/README.md.

Intervals CSV: ``brand_id,surface_label,t_start,t_end``  (surface_label optional)
Totals CSV:    ``brand_id,surface_label,seconds``        (surface_label optional)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from eval.metrics import norm_label, union_length


@dataclass(frozen=True)
class GoldRow:
    brand_id: str
    surface_label: str  # normalised; "" = not labelled
    t_start: float | None = None
    t_end: float | None = None
    seconds: float | None = None


@dataclass
class Gold:
    rows: list[GoldRow]

    @property
    def has_intervals(self) -> bool:
        """True only if every row is an interval (otherwise the totals variant is used)."""
        return bool(self.rows) and all(r.t_start is not None for r in self.rows)

    @property
    def brands(self) -> list[str]:
        return sorted({r.brand_id for r in self.rows})

    @property
    def has_surface_labels(self) -> bool:
        return any(r.surface_label for r in self.rows)

    def _seconds(self, rows: list[GoldRow]) -> float:
        iv = [(r.t_start, r.t_end) for r in rows if r.t_start is not None]
        tot = sum(r.seconds for r in rows if r.t_start is None and r.seconds is not None)
        return union_length(iv) + tot

    def brand_seconds(self, brand_id: str) -> float:
        return self._seconds([r for r in self.rows if r.brand_id == brand_id])

    def intervals(self, brand_id: str) -> list[tuple[float, float]]:
        return [(r.t_start, r.t_end) for r in self.rows if r.brand_id == brand_id and r.t_start is not None]

    def surface_pairs(self) -> list[tuple[str, str]]:
        return sorted({(r.surface_label, r.brand_id) for r in self.rows if r.surface_label})

    def pair_seconds(self, label: str, brand_id: str) -> float:
        return self._seconds([r for r in self.rows if r.brand_id == brand_id and r.surface_label == label])


def _num(value: str | None, field: str, line: int) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"gold line {line}: {field!r} is not a number: {value!r}") from None


def load_gold(path: str | Path) -> Gold:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = {c.strip() for c in (reader.fieldnames or [])}
        if "brand_id" not in cols:
            raise ValueError("gold CSV needs a brand_id column")
        if {"t_start", "t_end"} <= cols:
            kind = "intervals"
        elif "seconds" in cols:
            kind = "totals"
        else:
            raise ValueError("gold CSV needs t_start,t_end (intervals) or seconds (totals)")
        rows: list[GoldRow] = []
        for i, raw in enumerate(reader, start=2):
            rec = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if not rec.get("brand_id"):
                continue
            label = norm_label(rec.get("surface_label", ""))
            if kind == "intervals":
                s, e = _num(rec["t_start"], "t_start", i), _num(rec["t_end"], "t_end", i)
                if e < s:
                    raise ValueError(f"gold line {i}: t_end < t_start")
                rows.append(GoldRow(rec["brand_id"], label, t_start=s, t_end=e))
            else:
                sec = _num(rec["seconds"], "seconds", i)
                if sec < 0:
                    raise ValueError(f"gold line {i}: negative seconds")
                rows.append(GoldRow(rec["brand_id"], label, seconds=sec))
    return Gold(rows)
