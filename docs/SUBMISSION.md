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

**Evidence it works (upload):** `eval/runs/20261022T113439Z-ucl-vision-run.json`, measured on a 58 s RECORDED top-league broadcast segment with
gpt-5-mini at 2 fps: 116 frames, 81 logo sightings, 0 errors, 0 off-list answers; FedEx 20.0 s, PlayStation
12.0 s, Heineken 6.5 s, Mastercard 1.0 s on screen; $0.114 total, about $0.12 per match-minute. Accuracy against
reference labels: not measured yet.

**One pitch slide (public link):** `[[Google Slides link, "Anyone with the link can view"]]`

**GitHub repo:** https://github.com/kaktamsdengami/accel_hack- (check: no .env, no data/, no footage; all
git-ignored)

**Demo link:** https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app (API) and
https://pixel-perfect-recreation-849.lovable.app/dashboard?match=m-ucl-demo (dashboard)
