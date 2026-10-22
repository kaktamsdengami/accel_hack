# Lovable prompt for T13 (paste into Lovable, then paste docs/api-mock.json where it says so)

Build **SponsorPulse**, a live dashboard for a football club's commercial manager. It shows, while a
match streams, how many seconds each sponsor is visible on TV, on which surface (LED perimeter,
static boards, shirts, backdrop), and what the AI agents measuring it are doing. Audience: VC judges
at a demo, so it must look sharp, calm and trustworthy (think Linear / Vercel dashboards), dark
theme by default with a light toggle, one accent colour, numbers in tabular figures.

## Data
- All data comes from a REST + SSE backend. Base URL from env `VITE_API_BASE`.
- Until the backend is live, use **mock mode** (toggle in settings, default ON): load the JSON below
  and simulate live updates by advancing `clock_t` by 0.5 s every 500 ms and slowly growing the
  brand seconds; replay the `decision` and `alert` examples every few seconds.
- Endpoints (exact shapes are in the JSON below, keys are the endpoint names):
  `GET /matches`, `GET /matches/{id}/report`, `GET /matches/{id}/brands/{brand_id}`,
  `GET /matches/{id}/decisions?limit=50`, `GET /matches/{id}/coverage`,
  `GET /matches/{id}/stream` (Server-Sent Events: `stats` → full report, `decision`, `alert`),
  `GET /evidence/{frame_id}.jpg`, `POST /matches/{id}/chat` with `{question}`.
- Never invent fields; use only what is in the JSON.

## Screens
1. **Live match** (main screen)
   - Header: match title, big **LIVE** (red pulse) or **RECORDED** (grey) badge from `match.source`,
     match clock from `clock_t`.
   - Left: video area (placeholder image/16:9 box for now) with the coverage bar under it:
     a thin strip over the match timeline, measured = filled, unmeasured = striped, hover shows reason.
   - Right: **Sponsors** list, one row per brand: name, seconds (large), % of match, small bar,
     close-up seconds, and a **pace pill** vs contract target (ahead green / on track grey /
     behind amber / no target). Rows animate smoothly when numbers change.
   - Below: **Surfaces** table grouped by `surface_label`: brand, seconds, % of match, avg size,
     close-ups. Show it as "Board A · near: Alpha Air 9.0 s".
   - **LED rotation** timeline: horizontal track, coloured blocks per brand over time.
   - **Alerts** feed (under_pace, conflict, coverage_gap, budget) with time and icon.
2. **Agents** panel (side drawer or tab, important for the demo)
   - Live **decision log**: each row = time, which readers were tried (chips: memory, claude, gpt),
     chosen reader, confidence, cost, latency, and the `reason` sentence.
   - **Cost meter**: total spend vs budget, cost per match-minute, and **Claude-call share** as a big
     number with a small donut of `calls_by_reader`. Caption: "Share of shots that needed Claude".
3. **Sponsor detail** (click a brand): its surfaces, its intervals as a timeline with close-ups
   highlighted, evidence thumbnails (from `/evidence/{frame_id}.jpg`, placeholder if missing).
4. **Ask** (chat): input "Ask about any sponsor…", answers show the text, citation chips with time
   ranges (click → jump the timeline), a collapsible "SQL used" block, and the `not_measured_note`
   as a warning when present.

## Rules
- Mobile and laptop widths both work; no horizontal scroll.
- Every number has a unit (s, %, $). Mock mode shows a small "MOCK DATA" badge in the corner.
- Keep API calls in one module (`src/api.ts`) so the base URL and mock switch live in one place.

## Mock data (paste the full content of docs/api-mock.json below this line)
