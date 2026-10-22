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
- Capture: `[[capture_pct from eval/runs/<record>]]`, MAE `[[mae_s from eval/runs/<record>]]` s/brand
- Cost per match-minute: `[[cost_per_match_minute from eval/runs/<record>]]`
- Vision-call share, match 1 -> 3 at equal accuracy: `[[claude_call_share from eval/runs/<records>]]`
- Frame -> dashboard latency p50: `[[latency p50 from eval/runs/<record>]]`

**Honest status.** Live on Aiven Runtime end to end with a SYNTHETIC match; router with budget guard
working with stub readers; real vision and memory readers in progress; curator, detective and analyst
next. Figures above appear only once a run record exists.

Links: deployed API https://01a16333-2589-7299-babc-33f2830bf66a-8000.eur-1.aiven.app ·
repo `[[GitHub URL]]` · dashboard `[[Lovable URL]]`

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
> We measured against an independent reference labelling of a held-out window. Capture was
> [[capture_pct]], with an average error of [[mae_s]] seconds per brand, at [[cost_per_match_minute]]
> per match-minute. And because memory grows, the share of vision calls went from [[share match 1]]
> to [[share match 3]] at equal accuracy.

**2:40-3:00 | Close** (slide)
> Clubs get proof they can put in front of a sponsor, live, per surface, with frames. Next: the
> coverage detective and the sponsor analyst on top of the same data. SponsorPulse: every second,
> accounted for, and the seconds we could not account for, shown openly.

Recording notes: if no run record exists by recording time, replace the 2:10-2:40 block with
"Evaluation harness is ready; numbers are pending" and show `python -m eval.harness --help`
instead of a results table. Do not read out placeholder text.
