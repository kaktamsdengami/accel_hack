# Submission form (T30): text for each field

Copy-paste ready. `[[...]]` = fill from the run record in `eval/runs/` (or `data/runs/<name>/summary.json`
for measured seconds and cost); never type a number that is not in a record.

**Project name:** SponsorPulse

**One-line pitch:** Live, evidence-backed sponsor exposure for football broadcasts: seconds per sponsor and
per surface, with the frame as proof and every unmeasured second accounted for.

**3-minute demo (MP4):** T29 recording (script: docs/PITCH.md).

**The data layer in action:**
Frames from the broadcast are cut into shots, broadcaster graphics are masked, and one `Frame` event per
sample goes to **Aiven Kafka** (`frames`); JPEG evidence is stored in **PostgreSQL** (`evidence`). The router
agent consumes frame groups, asks the cheapest reliable reader (logo memory in **PostgreSQL + pgvector**
first, a vision model only when memory is unsure) under a hard per-match budget kept atomically in
**Valkey**, and publishes `Detection` and `DecisionLog` events back to Kafka. The aggregation worker turns
them into per-second visibility intervals, a coverage ledger (measured / unmeasured with a reason) and
agent traces with tokens and cost in **ClickHouse**, and pushes a live snapshot to Valkey pub/sub. A
FastAPI service on **Aiven Runtime** streams it over SSE to the Lovable dashboard and serves evidence
frames. Today the deployed demo runs on a SYNTHETIC match; the real-footage run was recorded offline with
the same contracts (see evidence).

**Evidence it works (upload):** `data/runs/[[name]]/summary.json` (measured seconds per brand, frames,
errors, tokens, cost) and the run record `eval/runs/[[record]].json` if reference labels exist. Measured on a
5-minute RECORDED segment with `[[model]]`: `[[frames]]` frames, `[[cost_usd]]` USD
(`[[cost per match-minute]]` per match-minute). Accuracy: `[[from eval/runs record, or "not measured yet"]]`.

**One pitch slide (public link):** `[[Google Slides link, "Anyone with the link can view"]]`

**GitHub repo:** https://github.com/kaktamsdengami/accel_hack- (check: no .env, no data/, no footage; all
git-ignored)

**Demo link:** https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app (API) and
`[[Lovable dashboard URL]]`
