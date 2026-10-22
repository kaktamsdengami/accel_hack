# Build plan: live sponsor-exposure agents (Accel "Data-Centric Agents")

Working name: **SponsorPulse** (confirm in T01). GitHub issues are the source of truth for task
status; this file holds the design, the decisions and the task map.

## 1. Product in one paragraph

A club sells boards, LED rotation, shirt and backdrop space. Sponsors ask "how many seconds was I
really on TV, and where?". SponsorPulse watches a match **as it streams** and measures, per second,
which sponsor is visible on which **surface**, with frames as evidence. Measurement is a
deterministic pipeline; **agents sit where decisions under uncertainty are needed**: a router that
picks the cheapest reliable reader per shot within a budget, a memory curator that keeps each
stadium's logo memory fresh and consistent, a coverage detective that hunts unmeasured or
conflicting seconds, and a sponsor analyst that answers open questions with its own SQL and
timestamps. Memory makes each new match at the same stadium **cheaper at equal accuracy**.
A pilot with a second-tier club is done and a second is agreed (earlier prototype; this repo is a
rewrite from scratch).

## 2. Judging criteria → evidence

| Criterion | What we show | Tasks |
|---|---|---|
| 1. Customer problem & value | Commercial manager of a 2nd-tier club; pilot done, next agreed; renewals need proof | T23 |
| 2. Agent capability | Router decides per shot under a budget; curator resolves stale/conflicting memory; detective investigates gaps; analyst writes and checks SQL | T14–T17 |
| 3. Data at the core | Live Kafka stream; per-second detections and surfaces in ClickHouse; logo memory in pgvector; live counters in Valkey. Without the data the agents are useless | T05, T06, T10, T15 |
| 4. Evidence it works | Capture % and MAE vs the club's hand count; cost per match-minute; Claude-call share falling match 1→3 at equal accuracy; frame→dashboard latency; independent second model removes the "circular benchmark" issue from the pilot | T19–T21 |
| 5. Execution & pitch | Live dashboard during the pitch, LIVE/RECORDED labels, one slide, deployed on Aiven Runtime | T12, T13, T23, T24 |
| Aiven prize | Kafka, ClickHouse, PostgreSQL+pgvector, Valkey each doing a real job, plus Runtime | T03, T05, T12 |

Brief patterns covered: event-driven agent, analyst agent, agent with memory (stale/contradicting),
data-to-answer detective, self-improving agent (one target: Claude-call share at equal accuracy).

## 3. How sponsors are seen and counted (no OCR dependency)

OCR reads letters; logos are pictures. The pilot's OCR caught 18–56% of sponsor time, the vision
model ~96%. So recognition is by **appearance**, not text.

1. **Prepare.** Cut shots; mask broadcaster graphics (score bug, watermark); sample 2–4 fps; group
   near-identical frames so each view is read once; flag replays.
2. **Find ad surfaces.** Open-vocabulary detector (OWLv2 or Grounding DINO on Modal GPU) with prompts
   such as "advertising board", "LED perimeter board", "logo on shirt", "sponsor backdrop".
   Output: boxes with a surface type. No brand knowledge needed here.
3. **Identify the brand, cheapest first** (the router decides):
   - **Memory reader:** crop → SigLIP/CLIP embedding (Modal) → nearest neighbours in pgvector
     among this club's reference logos. Bootstrapped from official logos found with Keenable, then
     grown with crops Claude confirmed.
   - **Claude vision:** crop/frame + sponsor list → brand from the list only, bbox, confidence.
   - **Second opinion (GPT vision):** only on conflicts; agreement raises confidence,
     disagreement goes to the detective.
4. **Time.** Track boxes within a shot; visibility is counted as **intervals**, not frame sums.
   A brand on several surfaces at once counts once for the brand and separately per surface.
   Each interval stores size (% of frame), distance from centre, sharpness, occlusion, replay flag.
   Below a minimum size/sharpness it is "visible, below threshold", not credited.
5. **Coverage ledger.** Every second of the match is measured or unmeasured, with a reason.

## 4. Per-surface metric

| Surface | Brand | Seconds | % of match | Avg size | Close-ups |
|---|---|---|---|---|---|
| LED perimeter, far touchline | Brand X | 412 | 7.6% | 2.1% | 38 s |
| LED perimeter, far touchline | Brand Y | 389 | 7.2% | 2.0% | 41 s |
| Static board A (left goal) | Brand Z | 120 | 2.2% | 1.4% | 9 s |
| Home shirt, chest | Brand W | 640 | 11.8% | 0.8% | 210 s |

(Illustrative layout, not measured numbers.)

- **v1 (core):** surface **type** + **side** (near/far touchline, left/right) + LED **rotation
  timeline** (which brand held the LED band when). Reliable from boxes and frame position.
- **v2 (second layer, T18):** exact **board ID** ("board A") via pitch keypoints → homography →
  position along the touchline → the stadium's board map stored in memory. Main camera only.

## 5. Architecture

```
 match video / stream (replayed in real time for the demo, labelled RECORDED)
        │
 [Ingest worker] shots · overlay mask · sampling · dedup ──► Kafka: frames
        │
 [Surface detector, Modal] boxes + surface type
        │
 [Router agent] ─► memory reader (SigLIP + pgvector)
        │        ├► Claude vision (low confidence)
        │        └► GPT second opinion (conflicts)          + budget guard, decision log
        ▼  Kafka: detections
 ClickHouse: intervals per brand/surface, coverage ledger, agent traces
 Valkey:     live counters, pacing vs contract
 Postgres:   brands, stadiums, board maps, contracts, logo memory (pgvector), labels
        │
        ├─► [Memory curator]     after each match: confirm, retire stale, flag conflicts
        ├─► [Coverage detective] gaps and disagreements → re-read → note in report
        ├─► [Sponsor analyst]    chat, read-only SQL tools, timestamps + frames
        ▼
 FastAPI (SSE, report, chat) ─► dashboard (Lovable) ─► deployed on Aiven Runtime
```

## 6. Resources

### Product (runtime)

| Resource | Job | Tasks |
|---|---|---|
| Aiven Kafka | frames, detections, decision streams | T05, T06, T14 |
| Aiven ClickHouse | intervals, surfaces, coverage, traces, analyst queries, eval | T05, T10, T17, T20 |
| Aiven PostgreSQL + pgvector | logo memory, stadiums, board maps, contracts, labels | T05, T09, T15, T18 |
| Aiven Valkey | live counters and pacing | T10 |
| Aiven Runtime | deploy api + workers + web from GitHub | T03, T12, T24 |
| Anthropic API $100 | **product only**: vision identification, router, curator, detective, analyst; hard per-match budget | T08, T14–T17 |
| Modal $100 | GPU: surface detector, SigLIP embeddings, pitch keypoints; batch re-runs of matches; stretch fine-tune | T07, T09, T18, T25 |
| OpenAI $50 | second-opinion vision on conflicts + independent check; **shared with Codex fallback, Codex has priority** | T19 |
| Keenable $1000 | official logos and brand aliases to bootstrap memory; sponsorship price benchmarks for the pitch | T09, T27 |
| Lovable 300 credits | dashboard, sponsor report page, landing page | T13, T22 |
| Supabase $25 | club/sponsor login through Lovable's built-in integration; data stays on Aiven | T28 |
| n8n Pro | under-pace alert and post-match report workflow (demo sends only to our own address) | T26 |
| GitHub | issues, milestones, PRs, Actions CI, Runtime source | all |

### Development capacity (nobody codes by hand)

Order of use; switching is cheap because state lives in the repo (see section 8):

1. Claude subscription in the desktop app (this session orchestrates; task sessions write code).
2. **$90 extra Claude usage in the app** when the subscription limit is hit. Same tools, same repo.
3. **Codex CLI** on the OpenAI key (reads `AGENTS.md`). T19 then shrinks to the free minimum.
4. **Lovable** for all of `web/` at any time; it does not consume Claude usage.
5. The Anthropic API $100 is **not** used for development unless the team decides so explicitly.

Saving usage: Sonnet for routine tasks (schemas, CRUD, scripts), Opus for router, interval maths
and agent prompts; short messages; one task per session.

## 7. Tasks

Effort is a rough estimate of active work, not a measurement. `needs:human` = a human action
(accounts, footage, permission).

**V0 Setup**
- **T00** Handoff kit: AGENTS.md, CLAUDE.md, HANDOFF.md · core · infra · deps: none
- **T01** Contracts and decisions · core · infra · deps: T00
- **T02** Footage, sponsor list, hand count, permission · core · data · deps: none · needs:human
- **T03** Provision Aiven and check Runtime · core · infra · deps: none · needs:human
- **T04** Repo skeleton and CI · core · infra · deps: T01
- **T05** Data layer schemas and migrations · core · data · deps: T03, T04

**V1 Vertical slice (M1)**
- **T06** Live ingest worker · core · data · deps: T04, T05, T02
- **T07** Surface detector on Modal · core · vision · deps: T04, T02
- **T08** Claude vision identifier · core · vision · deps: T01, T02
- **T09** Logo memory and embedding reader · core · vision · deps: T05, T07
- **T10** Intervals, per-surface aggregation, coverage ledger, live counters · core · data · deps: T05
- **T11** API (SSE, report, evidence) · core · infra · deps: T10
- **T12** Integrate the slice and deploy on Aiven Runtime · core · infra · deps: T06, T07, T08, T10, T11, T03
- **T13** Dashboard in Lovable · core · ui · deps: T01 (mock), T11 (live)

**V2 Agents and memory (M2)**
- **T14** Router agent with budget guard · core · agents · deps: T08, T09, T12
- **T15** Memory curator and learning across matches · core · agents · deps: T14
- **T16** Coverage detective agent · second-layer · agents · deps: T10, T14
- **T17** Sponsor analyst agent · core · agents · deps: T10, T11
- **T18** Board IDs via pitch homography + LED rotation timeline · second-layer · vision · deps: T07, T10
- **T19** GPT second opinion and independent check · second-layer · vision · deps: T14

**V3 Evidence and pitch (M3, M4)**
- **T20** Evaluation harness and baselines · core · eval · deps: T02, T14
- **T21** Final runs and frozen numbers · core · eval · deps: T20, T15, T12
- **T22** Sponsor report export · second-layer · ui · deps: T10
- **T23** Pitch slide, README, diagram · core · submission · deps: T21 (numbers); start early
- **T24** Final verification and rehearsal · core · submission · deps: all core

**Stretch**
- **T25** Fine-tune own logo detector on Modal from accumulated labels
- **T26** n8n alert and report workflow
- **T27** Keenable sponsorship price benchmarks for the pitch
- **T28** Club/sponsor login with Supabase via Lovable

## 8. Order, parallel work, milestones

```
T00 ─► T01 ─► T04 ─► T05 ─┬─► T06 ─┐
T02 (human) ──────────────┤  T07 ─┼─► T12 (M1) ─► T14 ─► T15 ─► T20 ─► T21 (M3) ─► T23 ─► T24 (M4)
T03 (human) ──────────────┘  T08 ─┤               │ T16, T17, T19 in parallel
                             T10 ─► T11 ┘          T09 (after T07) feeds T14
T13 in Lovable from T01 (mock data) → live after T11; T18, T22 any time after T10
```

Parallel sessions (no shared files): after T05 → T06, T07, T08, T10 in four sessions + T13 in
Lovable; after T12 → T14 then T15; T16, T17, T19, T22 alongside.

Milestones: **M1** slice online · **M2** router + memory · **M3** numbers frozen · **M4** pitch-ready.

## 9. Handoff between models

- State lives in the repo: `AGENTS.md` (rules for every agent), `docs/HANDOFF.md` (live log),
  issues (each self-contained with a copy-paste prompt), `docs/CONTRACTS.md`.
- Small commits, push after each working step; WIP branches are pushed too.
- At ~80% of a usage limit the current agent updates `HANDOFF.md`, pushes, and prints the prompt
  for the next agent.
- Next agent prompt: "Read AGENTS.md and docs/HANDOFF.md. Take issue Txx, work on branch
  txx-<name>, meet its acceptance checks, update HANDOFF.md, open a PR linked to the issue."

## 10. Risks and fallbacks

| Risk | Fallback |
|---|---|
| Footage cannot be shown publicly | Blur frames and show numbers; or a short clip with written club permission |
| Aiven Runtime access blocked | Ask Aiven mentors at once; temporary deploy elsewhere, data stays on Aiven |
| Claude product spend | Per-match budget guard; Haiku for routing, stronger model only for vision escalation |
| Memory does not cut Claude calls | Report honestly; show cost and latency instead |
| Usage limits during build | Section 6 ladder; repo-based handoff |
| Live demo fails on stage | Recorded backup video (T24) |
| Prior-work rule | Code rewritten here; pilot results credited to the earlier prototype |
