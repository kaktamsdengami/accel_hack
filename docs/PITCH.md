# PITCH: one slide + 3-minute demo voice-over (T23 / T29 draft)

Rules for this file (AGENTS.md): every number must come from a run record in `eval/runs/`
(here: `eval/runs/20261022T113439Z-ucl-vision-run.json`). Dashboard content shown today is SYNTHETIC or MOCK and must be labelled so on screen. Say
"independent reference labelling", never anything implying a club hand count. No club names, no
sponsor names, no footage that is not cleared for public use.

---

## The slide (one public Google Slide, the only thing shown on stage)

**Title:** EXPOSURE — AI agents that prove every second of sponsor exposure, live

**Problem.** Clubs sell LED boards, shirts and backdrops for millions. At renewal the sponsor asks: "how
many seconds was I really on TV, and where?" Today: a manual count after the match, or a guess.

**Three agents on a live data layer:**
| Agent | Decides | Live proof on the dashboard |
|---|---|---|
| **Vision Reader** | which sponsor is on which surface, frame by frame; only the club's sponsor list, unknown stays unknown | 116 frames read, 81 logo sightings, 0 off-list answers |
| **Budget Guard** | whether a frame is worth a model call, under a hard per-match budget | $0.11 spent, ≈ $0.12 per match-minute |
| **Sponsor Analyst** | answers open questions by calling the data API as tools, cites seconds and frames | "Who got the most screen time?" → FedEx, 20 s, with frames |

**Data at the core (Aiven):** Kafka streams frames and decisions → ClickHouse per-second intervals and
coverage → Valkey live counters → PostgreSQL evidence frames (+ pgvector logo memory) → API on Aiven Runtime.

**Evidence (recorded Champions League broadcast segment, 58 s):** FedEx 20.0 s · PlayStation 12.0 s ·
Heineken 6.5 s · Mastercard 1.0 s on screen; 0 failed calls; $0.11 total.

**Next:** logo memory makes the next match at the same stadium cheaper (router and memory built and
tested); accuracy against independent reference labelling.

Links: dashboard https://pixel-perfect-recreation-849.lovable.app/dashboard?match=m-ucl-demo ·
repo https://github.com/kaktamsdengami/accel_hack-

---

## 3-minute demo video: screen walkthrough + voice-over script

Screen recording of the live dashboard (`?match=m-ucl-demo`), voice-over from `scripts/voiceover.py`
(voice Sarah). `Screen:` = what to show/click. `[C1..C5]` = judging criterion the scene proves:
C1 problem & value, C2 agent capability, C3 data at the core, C4 evidence it works, C5 execution & pitch.
Numbers: eval/runs/20261022T113439Z-ucl-vision-run.json only.

**0:00-0:15 | Hook** (title card, then cut to the dashboard)
Screen: black title card "EXPOSURE", then the Live match tab with the broadcast frame. [C1]
> A club sells its stadium to sponsors: LED boards, shirts, backdrops. At renewal, every sponsor
> asks the same question: how many seconds was I really on TV? Today the answer is a manual count, or a guess.

**0:15-0:37 | Live match** (Live match tab)
Screen: RECORDED badge and match clock, then the KPI tiles, the sponsor leaderboard, the LED rotation
track and the surface breakdown; slow scroll. [C1, C3 freshness]
> This is EXPOSURE, built for a club's commercial manager. Here it runs on a recorded Champions League
> segment. Second by second, it measures which sponsor is on screen, on which surface, and for how long.
> The leaderboard, the LED rotation and the surface breakdown update live while the match streams.

**0:37-0:57 | Proof** (broadcast evidence + coverage bar)
Screen: zoom on the evidence frame with its time and confidence, then the coverage bar
"100 % measured · 0.0 s unmeasured". [C3 grounding, C4]
> Every second is backed by a frame. This is the exact frame the agent read, with its confidence.
> The coverage bar shows what we measured and what we did not: here one hundred percent of the
> segment is measured, and nothing unmeasured is ever counted.

**0:57-1:32 | Agents at work** (Agents tab)
Screen: "Agents at work" strip with the three agent cards, then the decision log (model, confidence,
cost, latency per row), then the cost meter. [C2]
> Three agents work as a team. The Vision Reader looks at every frame and decides which sponsor is on
> which board, choosing only from the club's sponsor list: if it is not sure, it says unknown. The
> Budget Guard prices every model call before it is made, against a hard budget per match, and stops
> the run before it overspends. Every decision lands in this log: which agent, which model, how
> confident, what it cost, and how long it took.

**1:32-2:02 | Ask the analyst** (Ask tab)
Screen: type "Which sponsor got the most screen time, and where?", show the tool calls, the answer and
its citation chips; click a chip to jump to the timeline. Record only a live answer. [C2 multi-step, C3]
> The third agent is the Sponsor Analyst. I ask: which sponsor got the most screen time, and where?
> It decides which tools it needs and calls our data API: the match report, the brand's intervals,
> the evidence frames. The answer: FedEx, twenty seconds, on the far LED perimeter, with the frames
> as citations. No number without a source.

**2:02-2:25 | Data at the core** (architecture diagram)
Screen: architecture diagram from the README (Kafka → ClickHouse → Valkey → PostgreSQL → API on Aiven Runtime). [C3]
> Underneath is a live data layer on Aiven. Frames and agent decisions stream through Kafka.
> ClickHouse turns them into per-second intervals and a coverage ledger. Valkey keeps the live
> counters this dashboard reads, and PostgreSQL stores every evidence frame.

**2:25-2:45 | Evidence** (back to Live match: KPI tiles + leaderboard)
Screen: the cost tile and the leaderboard; caption "measured run, recorded segment". [C4]
> The measured result on this fifty-eight-second segment: a hundred and sixteen frames read, eighty-one
> sponsor sightings, zero failed calls, zero brands outside the list. Eleven cents in total, about
> twelve cents per match-minute, so a full match projects to around eleven dollars.

**2:45-3:00 | Close** (the slide)
Screen: the Google Slide. [C5]
> EXPOSURE: every second of sponsor exposure, measured live, with proof. Our first users are club
> commercial teams preparing sponsor renewals. Next: logo memory, so every new match at the same
> stadium gets cheaper.

Recording note: show only what the dashboard shows; the Analyst answer must come from the live app.
If the Ask tab is not live at recording time, cut scene "Ask the analyst" and say so; never fake it.
