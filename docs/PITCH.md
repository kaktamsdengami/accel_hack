# PITCH: one slide + 3-minute demo voice-over (T23 / T29 draft)

Rules for this file (AGENTS.md): every number must come from a run record in `eval/runs/`
(here: `eval/runs/20261022T113439Z-ucl-vision-run.json`). Dashboard content shown today is SYNTHETIC or MOCK and must be labelled so on screen. Say
"independent reference labelling", never anything implying a club hand count. No club names, no
sponsor names, no footage that is not cleared for public use.

---

## The slide (one public Google Slide, the only thing shown on stage)

**Title:** SponsorPulse — AI agents that prove every second of sponsor exposure, live

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

## 3-minute demo video: voice-over script (~380 words)

**0:00-0:15 | Hook** (title card → the dashboard with the UCL frame)
> Clubs sell their stadium to sponsors: LED boards, shirts, backdrops. When the contract comes up, the
> sponsor asks one question: how many seconds was I really on TV? Nobody can answer it live.

**0:15-0:40 | The product** (dashboard, sponsor leaderboard growing)
> SponsorPulse watches the broadcast and measures, second by second, which sponsor is visible, on
> which surface, with the frame as proof. Here it is on a recorded Champions League segment.

**0:40-1:20 | The agents** (zoom on "Agents at work", one card at a time)
> Three agents do the work. The Vision Reader looks at every frame and decides which sponsor is on
> which board, but only from the club's sponsor list: if it is not sure, it says unknown, it never
> guesses. The Budget Guard prices every model call before it is made, against a hard budget per
> match, so a full match stays cheap. And the Sponsor Analyst answers questions in plain English.

**1:20-1:55 | Ask the analyst** (Ask tab: type "Which sponsor got the most screen time and where?")
> I ask: which sponsor got the most screen time? The analyst calls our data API as tools, the report,
> the brand intervals, the frames, and answers: FedEx, twenty seconds, on the LED perimeter, with the
> frames to prove it. No number without a source.

**1:55-2:25 | Data at the core** (architecture diagram from the README)
> Under the hood it is a live data pipeline on Aiven. Frames and decisions stream through Kafka.
> ClickHouse turns them into per-second intervals and a coverage ledger. Valkey keeps live counters
> for the dashboard, PostgreSQL stores every evidence frame. The API runs on Aiven Runtime.

**2:25-2:45 | Evidence** (leaderboard + cost card)
> On this fifty-eight-second segment: FedEx twenty seconds, PlayStation twelve, Heineken six and a
> half, Mastercard one. Zero failed calls, zero brands outside the list. Eleven cents in total, about
> twelve cents per match-minute.

**2:45-3:00 | Close** (slide)
> SponsorPulse: every second of sponsor exposure, measured live, with proof. Next: logo memory, so
> every new match at the same stadium gets cheaper.

Recording note: show only what the dashboard shows; the Analyst answer must come from the live app.
