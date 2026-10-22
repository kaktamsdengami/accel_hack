"""Evaluation harness CLI.

Run:      python -m eval.harness --pred P.json --gold G.csv --decisions D.json --minutes 10 --name my-run
Compare:  python -m eval.harness --compare eval/runs/a.json eval/runs/b.json [--chart out.png]
Rebuild:  python -m eval.harness --reproduce eval/runs/a.json   (recompute metrics from the record's inputs)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from eval.gold import load_gold
from eval.metrics import compute_all, load_pred

RUNS_DIR = Path(__file__).parent / "runs"
BASELINES = ("ocr-only", "claude-only", "router-memory")

# (column title, path into metrics dict, format)
COLUMNS = [
    ("capture%", ("capture_pct", "value"), "{:.1f}"),
    ("false-cred%", ("false_credit_pct", "value"), "{:.1f}"),
    ("MAE s/brand", ("mae_s_per_brand", "value"), "{:.1f}"),
    ("MAE s/surface", ("mae_s_per_surface", "value"), "{:.1f}"),
    ("$/match-min", ("cost_per_match_minute", "value"), "{:.4f}"),
    ("claude share", ("claude_call_share", "value"), "{:.2f}"),
    ("p50 s", ("latency", "p50_s"), "{:.2f}"),
    ("p95 s", ("latency", "p95_s"), "{:.2f}"),
]


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                             cwd=Path(__file__).parent)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _load_decisions(path: str | Path) -> list[dict]:
    data = _load_json(path)
    return data.get("decisions", []) if isinstance(data, dict) else data


def _load_latency(path: str | Path | None) -> list[tuple]:
    if not path:
        return []
    data = _load_json(path)
    return [tuple(p) for p in data]


def run_metrics(args: dict) -> tuple[dict, dict]:
    """Compute metrics from a params dict (as stored in a record). Returns (metrics, variant info)."""
    gold = load_gold(args["gold"])
    metrics = compute_all(
        load_pred(args["pred"]), gold, _load_decisions(args["decisions"]), args["minutes"],
        sponsors=args.get("sponsors") or None, latency_pairs=_load_latency(args.get("latency")),
        extra_usd=args.get("extra_usd", 0.0),
    )
    return metrics, {"capture_variant": metrics["capture_pct"]["variant"]}


def build_record(args: dict, now: datetime | None = None) -> dict:
    metrics, variant = run_metrics(args)
    now = now or datetime.now(UTC)
    inputs = {k: {"path": str(args[k]), "sha256": file_sha256(args[k])}
              for k in ("pred", "gold", "decisions", "latency") if args.get(k)}
    return {
        "name": args["name"],
        "created_at": now.isoformat(),
        "source": args["source"],
        "baseline": args.get("baseline"),
        "git_commit": git_commit(),
        "inputs": inputs,
        "params": {k: args.get(k) for k in ("minutes", "sponsors", "extra_usd")},
        "variant": variant,
        "metrics": metrics,
    }


def write_record(record: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(record["created_at"]).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{record['name']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _dig(d: dict, path: tuple[str, ...]):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def format_table(records: list[dict]) -> str:
    head = ["run", "source", "baseline", "variant"] + [c[0] for c in COLUMNS]
    rows = [head]
    for r in records:
        row = [r["name"], r["source"], r.get("baseline") or "-", r["variant"]["capture_variant"]]
        for _, path, fmt in COLUMNS:
            v = _dig(r["metrics"], path)
            row.append("n/a" if v is None else fmt.format(v))
        rows.append(row)
    widths = [max(len(row[i]) for row in rows) for i in range(len(head))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def make_chart(records: list[dict], out: str) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: chart skipped", file=sys.stderr)
        return False
    panels = [c for c in COLUMNS if c[0] in ("capture%", "MAE s/brand", "$/match-min", "p95 s")]
    fig, axes = plt.subplots(1, len(panels), figsize=(4 * len(panels), 3.5))
    names = [f"{r['name']}\n[{r['source']}]" for r in records]
    for ax, (title, path, _) in zip(axes, panels, strict=True):
        ax.bar(names, [_dig(r["metrics"], path) or 0 for r in records])
        ax.set_title(title)
        ax.tick_params(axis="x", labelsize=7)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    return True


def reproduce(record_path: str) -> int:
    rec = _load_json(record_path)
    args = {k: v["path"] for k, v in rec["inputs"].items()}
    for k, v in rec["inputs"].items():
        if file_sha256(v["path"]) != v["sha256"]:
            print(f"input {k} changed since the run (hash mismatch): {v['path']}", file=sys.stderr)
            return 2
    args.update(rec["params"], name=rec["name"], source=rec["source"], baseline=rec.get("baseline"))
    metrics, _ = run_metrics(args)
    same = json.dumps(metrics, sort_keys=True) == json.dumps(rec["metrics"], sort_keys=True)
    print("reproduced: metrics identical" if same else "MISMATCH: recomputed metrics differ from record")
    return 0 if same else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval.harness", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pred", help="predicted intervals JSON (list of contracts Interval)")
    p.add_argument("--gold", help="club hand count CSV (see eval/runs/README.md)")
    p.add_argument("--decisions", help="DecisionLog list JSON")
    p.add_argument("--minutes", type=float, help="processed match minutes")
    p.add_argument("--name", help="run name")
    p.add_argument("--baseline", choices=BASELINES)
    p.add_argument("--source", choices=("LIVE", "RECORDED"), default="RECORDED")
    p.add_argument("--sponsors", help="comma-separated sponsor brand_ids (default: brands in gold)")
    p.add_argument("--latency", help="JSON list of [frame_published_wall, stats_sse_wall] pairs")
    p.add_argument("--extra-usd", type=float, default=0.0, help="non-API spend, e.g. Modal GPU")
    p.add_argument("--out-dir", default=str(RUNS_DIR), help="where to write the run record")
    p.add_argument("--compare", nargs="+", metavar="RECORD")
    p.add_argument("--reproduce", metavar="RECORD")
    p.add_argument("--chart", metavar="PNG", help="with --compare: write a bar chart if matplotlib exists")
    a = p.parse_args(argv)

    if a.reproduce:
        return reproduce(a.reproduce)
    if a.compare:
        records = [_load_json(f) for f in a.compare]
        print(format_table(records))
        if a.chart and make_chart(records, a.chart):
            print(f"chart written: {a.chart}")
        return 0
    missing = [f"--{k}" for k in ("pred", "gold", "decisions", "minutes", "name") if getattr(a, k) is None]
    if missing:
        p.error("missing " + ", ".join(missing))
    sponsors = [s.strip() for s in a.sponsors.split(",") if s.strip()] if a.sponsors else None
    record = build_record({
        "pred": a.pred, "gold": a.gold, "decisions": a.decisions, "minutes": a.minutes, "name": a.name,
        "baseline": a.baseline, "source": a.source, "sponsors": sponsors, "latency": a.latency,
        "extra_usd": a.extra_usd,
    })
    path = write_record(record, Path(a.out_dir))
    print(format_table([record]))
    cap = record["metrics"]["capture_pct"]
    print(f"capture variant: {cap['variant']}" + (" (weaker variant: gold has totals only)"
                                                   if cap["variant"] == "totals" else ""))
    print(f"record: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
