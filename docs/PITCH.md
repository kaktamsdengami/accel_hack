# PITCH: one slide + 3-minute demo voice-over (T23 / T29 draft)

Rules for this file (AGENTS.md): every number must come from a run record in `eval/runs/`. None
exists yet, so all figures are `[[placeholders]]`; fill them only from a record and cite the record
file. Dashboard content shown today is SYNTHETIC or MOCK and must be labelled so on screen. Say
"independent reference labelling", never anything implying a club hand count. No club names, no
sponsor names, no footage that is not cleared for public use.

---

## The slide (one public Google Slide, the only thing shown on stage)

**Title:** SponsorPulse: live, evidence-backed sponsor exposure for football broadcasts

**Problem.** Clubs sell LED perimeter, boards, shirts and backdrops. At renewal, sponsors ask "how
many seconds was I really on TV, and where?". Today the answer is a manual count after the match, or
an estimate. No per-surface, per-second proof, and nothing while the match is still on.

**What we built.** A live pipeline that measures, per second, which sponsor is visible on which
surface, with the frame as evidence and a ledger of every second it could not measure. Agents only
where a decision under uncertainty is needed; everything else is deterministic code.

**How it works (one line per agent / stage):**
- **Ingest:** cuts shots, masks broadcaster graphics, samples frames, groups near-duplicates -> Kafka.
- **Router agent:** per shot, picks the cheapest reliable reader (logo memory first, vision model
  only when unsure) under a hard per-match budget (`MATCH_BUDGET_USD`, default 3) and logs every decision.
- **Memory curator** (in progress): keeps each stadium's logo memory fresh, so the next match at the
  same stadium needs fewer vision calls.
- **Coverage detective** (planned): hunts unmeasured or conflicting seconds and re-reads them.
- **Sponsor analyst** (planned): answers open questions with read-only SQL, timestamps and frames.
- **Data layer:** Aiven Kafka (stream), ClickHouse (per-second analytics), PostgreSQL + pgvector
  (logo memory, evidence), Valkey (live counters), Aiven Runtime (deploy). Modal GPU; OpenAI vision
  primary, Claude switchable.

**The dashboard.** Live seconds per sponsor and per surface, pacing against the contracted target,
LED rotation timeline, coverage %, cost and budget left, the router's decision feed.
Screenshot label: **SYNTHETIC DATA**.

**Evidence (from `eval/runs/`, independent reference labelling, held-out window):**
Measured on a recorded top-league broadcast segment (58 s, 116 frames at 2 fps, gpt-5-mini; record
`eval/runs/20261022T113439Z-ucl-vision-run.json`):
- Sponsor seconds on screen: FedEx 20.0 s, PlayStation 12.0 s, Heineken 6.5 s, Mastercard 1.0 s
- 81 logo sightings, 0 failed calls, 0 answers off the sponsor list
- Cost: $0.11 for the segment, about **$0.12 per match-minute** (~$11 for a 90-minute match at 2 fps)
- Accuracy against reference labels: not measured yet (labelling unfinished)

**Honest status.** Live on Aiven Runtime end to end with a SYNTHETIC match; router with budget guard
working with stub readers; real vision and memory readers in progress; curator, detective and analyst
next. Figures above appear only once a run record exists.

Links: deployed API https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app ·
repo https://github.com/kaktamsdengami/accel_hack- · dashboard https://pixel-perfect-recreation-849.lovable.app/dashboard?match=m-ucl-demo

---

## 3-minute demo video: voice-over script (~400 words)

Screen labels during the whole video: the source badge (**RECORDED** / **SYNTHETIC**) stays visible.

**0:00-0:20 | Hook** (title card, then a broadcast-style frame with boards highlighted; use only
cleared or generated footage)
> Every football club sells its stadium to sponsors: LED boards, static boards, shirts, the
> backdrop behind the interview. And every sponsor asks the same question at renewal: how many
> seconds was I really on TV, and where? Today nobody can answer that live, and rarely with proof.

**0:20-0:45 | What SponsorPulse is** (dashboard overview, SYNTHETIC badge in view)
> SponsorPulse watches the broadcast as it streams and measures, second by second, which sponsor is
> visible on which surface. Every credited second has a frame behind it, and every second we could
> not measure is in a coverage ledger with the reason.

**0:45-1:25 | How it works** (architecture diagram from the README, highlight each box as named)
> Frames go from the ingest worker into Aiven Kafka. A router agent decides, shot by shot, how to
> read them: logo memory in PostgreSQL with pgvector first, a vision model only when memory is
> unsure. It works under a hard per-match budget and logs every decision with its cost. Detections
> become intervals in ClickHouse, live counters sit in Valkey, and a FastAPI stream pushes them to
> the dashboard. The whole service runs on Aiven Runtime; GPU models run on Modal.

**1:25-2:10 | The dashboard live** (screen capture of the deployed demo, SYNTHETIC match)
> Here the match clock runs and each sponsor's seconds grow in real time, split by surface. This
> sponsor is behind its contracted pace, and the pace indicator shows it. On the right, the router's decisions:
> which reader it chose, how confident it was, what it cost, and how much budget is left. This demo
> match is synthetic, so the numbers on screen are not measurements. Click a sponsor and you get
> its intervals per surface, the close-ups, and the evidence frame for each one. The coverage panel
> shows how many seconds were measured, and for the rest, why not: no frame, low confidence, or a
> group the budget guard chose not to pay for.

**2:10-2:40 | Evidence** (results table, values only from eval/runs)
> On a recorded Champions League broadcast segment, fifty-eight seconds and a hundred and sixteen
> frames, the system found FedEx on screen for twenty seconds, PlayStation for twelve, Heineken for six
> and a half, Mastercard for one. Zero failed calls, zero brands outside the sponsor list. It cost eleven
> cents, about twelve cents per match-minute, so a full match is roughly the price of a sandwich.
> Every one of those seconds links back to a frame.

**2:40-3:00 | Close** (slide)
> Clubs get proof they can put in front of a sponsor, live, per surface, with frames. Next: the
> coverage detective and the sponsor analyst on top of the same data. SponsorPulse: every second,
> accounted for, and the seconds we could not account for, shown openly.

Recording notes: if no run record exists by recording time, replace the 2:10-2:40 block with
"Evaluation harness is ready; numbers are pending" and show `python -m eval.harness --help`
instead of a results table. Do not read out placeholder text.
