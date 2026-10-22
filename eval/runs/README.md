# eval/runs: run records

This folder holds one JSON record per **real** evaluation run (`<UTC timestamp>-<name>.json`):
input file hashes, git commit, params, metrics, capture variant used, `source: LIVE|RECORDED`.
No footage, no hand counts, and **no records from synthetic data** (synthetic fixtures live only in
`tests/`). Every number in the README or pitch must come from a record here.

## Commands

```
python -m eval.harness --pred pred.json --gold gold.csv --decisions decisions.json \
    --minutes 12.5 --name router-memory-heldout [--baseline ocr-only|claude-only|router-memory] \
    [--source LIVE|RECORDED] [--sponsors b-a,b-b] [--latency latency.json] [--extra-usd 0.4]
python -m eval.harness --compare eval/runs/a.json eval/runs/b.json [--chart out.png]
python -m eval.harness --reproduce eval/runs/a.json   # recompute metrics from the record's inputs
```

## Input formats

**Predictions** (`--pred`): JSON list of contracts `Interval` objects (or `{"intervals": [...]}`).
Used fields: `brand_id`, `t_start`, `t_end`, and either `surface_label` or `surface_type` + `side`
(label becomes e.g. `LED perimeter · far`, `Board · near`).

**Decisions** (`--decisions`): JSON list of contracts `DecisionLog` objects.

**Latency** (`--latency`, optional): JSON list of `[frame_published_wall, stats_sse_wall]` pairs
(ISO 8601 UTC strings or epoch seconds).

**Gold** (`--gold`): the club's hand count as CSV with a header row. Two formats:

Intervals:
```
brand_id,surface_label,t_start,t_end
b-acme,LED perimeter · far,12.0,19.5
b-acme,LED perimeter · far,40.0,44.0
b-zeta,,100.0,130.0
```

Totals only:
```
brand_id,surface_label,seconds
b-acme,LED perimeter · far,11.5
b-zeta,,30.0
```

- `surface_label` is optional (empty or column absent = unlabelled). Matching is case and
  whitespace insensitive; use the same labels as predictions. Per-surface MAE needs labels.
- Times are seconds on the match clock (same clock as predictions). The evaluation window must be
  the held-out window only; tuning never uses it.
- A brand's time is the union of its intervals, so overlapping rows count once.
- The sponsor list defaults to the brands present in gold; pass `--sponsors` to include brands the
  club listed but did not see (missing brand = 0 s).

## Capture variants

`capture_pct.variant` is `intervals` (overlap, CONTRACTS section 7) when every gold row is an
interval. Otherwise it is `totals`, the weaker variant `sum(min(pred_s, gold_s)) / sum(gold_s)`;
false-credit % is then `n/a`. The variant is printed and stored in the record.
