"""Publish a SYNTHETIC match into Kafka at real-time speed, to demo the pipeline before footage arrives.

EVERYTHING HERE IS INVENTED. The match is `m-synthetic` (source RECORDED, title says SYNTHETIC), brands are
"SYNTHETIC ..." and every number comes from a seeded random generator. It must never be shown or quoted as a
measurement. Use: python -m scripts.fake_stream [--duration 60] [--speed 1] [--match-id m-synthetic]

What it does: (1) idempotently seeds Postgres brands + contracts and the Valkey hash `sp:match:<id>` (+ set
`sp:matches`); (2) publishes `frames`, `detections` and `decisions` at 2 fps match time: an LED perimeter
rotating three brands, a static board, a close-up shirt, misses, below-threshold and unknown-brand
detections, memory/claude decisions with cost and two failed reads.
"""
from __future__ import annotations

import argparse
import random
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from app.contracts import BBox, DecisionLog, Detection, Frame, Match, Reader, Side, Source, SurfaceType

MATCH_ID = "m-synthetic"
CLUB_ID = "c-synthetic"
STADIUM_ID = "st-synthetic"
DURATION_S = 60.0
FPS = 2.0
GROUP_S = 2.0          # frames of one 2 s window share a group_id (one read decision)
SHOT_S = 30.0
BUDGET_USD = 3.0
CLAUDE_COST_USD = 0.012
BRANDS = {"b-synth-alpha": "SYNTHETIC Alpha", "b-synth-beta": "SYNTHETIC Beta",
          "b-synth-gamma": "SYNTHETIC Gamma"}
# invented contract targets (seconds per 60 s match); gamma's is high on purpose so it falls behind pace
TARGETS = {"b-synth-alpha": 25.0, "b-synth-beta": 20.0, "b-synth-gamma": 40.0}
FAILED_GROUPS = {8, 20}  # groups (2 s windows) whose read failed: confidence 0, low_confidence


def synthetic_match(match_id: str = MATCH_ID, duration_s: float = DURATION_S) -> Match:
    return Match(match_id=match_id, club_id=CLUB_ID, stadium_id=STADIUM_ID,
                 title="SYNTHETIC demo match (invented data, not a measurement)", source=Source.RECORDED,
                 duration_s=duration_s, sampled_fps=FPS, started_at=datetime.now(UTC))


def _box(rng: random.Random, x: float, y: float, w: float, h: float) -> BBox:
    j = lambda v, s: min(max(v + rng.uniform(-s, s), 0.0), 1.0)  # noqa: E731
    return BBox(x=j(x, 0.02), y=j(y, 0.01), w=w, h=h)


def generate(match_id: str = MATCH_ID, duration_s: float = DURATION_S, seed: int = 7,
             ) -> Iterator[tuple[Frame, list[Detection], list[DecisionLog]]]:
    """Deterministic per-frame records (frame, detections, decisions) for the synthetic match."""
    rng = random.Random(seed)
    alpha, beta, gamma = "b-synth-alpha", "b-synth-beta", "b-synth-gamma"
    rotation = [alpha, beta, gamma]
    budget_left = BUDGET_USD
    group_reader = Reader.memory
    group_failed = False
    specs: list[tuple] = []

    def add(box, st, side, board, brand, area_scale=1.0, **kw):
        specs.append((box, st, side, board, brand, area_scale, kw))

    for n in range(int(duration_s * FPS)):
        t = n / FPS
        shot_no = int(t // SHOT_S)
        shot_id = f"{match_id}:s{shot_no}"
        group_no = int(t // GROUP_S)
        group_id = f"{match_id}:g{group_no}"
        frame = Frame(frame_id=f"{match_id}:{n}", match_id=match_id, shot_id=shot_id, group_id=group_id, t=t,
                      width=1280, height=720)
        decisions: list[DecisionLog] = []
        if t % GROUP_S == 0:  # first frame of the window carries the read decision
            group_failed = group_no in FAILED_GROUPS
            use_claude = (not group_failed) and group_no % 5 == 4
            group_reader = Reader.claude if use_claude else Reader.memory
            cost = CLAUDE_COST_USD if use_claude else 0.0
            budget_left = round(budget_left - cost, 6)
            decisions.append(DecisionLog(
                decision_id=f"{match_id}:dec{group_no}", match_id=match_id, shot_id=shot_id,
                group_id=group_id,
                t=t, readers_tried=[Reader.memory, Reader.claude] if use_claude else [Reader.memory],
                chosen_reader=group_reader,
                confidence=0.0 if group_failed else (0.88 if use_claude else 0.93),
                cost_usd=cost, latency_ms=850.0 if use_claude else 12.0,
                reason="low_confidence: memory below threshold" if group_failed else (
                    "memory margin too small, asked claude" if use_claude else "memory match"),
                budget_left_usd=budget_left))
        detections: list[Detection] = []
        if not group_failed:
            specs.clear()

            if rng.random() > 0.08:  # LED far: the three brands rotate every 8 s
                add(_box(rng, 0.10, 0.06, 0.55, 0.04), SurfaceType.led_perimeter, Side.far, None,
                    rotation[int(t // 8) % 3])
            if rng.random() > 0.08:  # static board A near: alpha first half, beta second half
                add(_box(rng, 0.20, 0.80, 0.30, 0.03), SurfaceType.static_board, Side.near, "A",
                    alpha if t < 30 else beta)
            if 10 <= t < 24 and rng.random() > 0.05:  # a close-up on the home shirt (large area)
                add(_box(rng, 0.40, 0.30, 0.25, 0.20), SurfaceType.shirt, Side.unknown, "home:chest", beta)
            if rng.random() < 0.10:  # a tiny, below-threshold sighting of gamma
                add(_box(rng, 0.70, 0.50, 0.02, 0.02), SurfaceType.banner, Side.left, None, gamma,
                    area_scale=0.1, below_threshold=True)
            if rng.random() < 0.08:  # an ad surface with no known brand
                add(_box(rng, 0.60, 0.07, 0.15, 0.03), SurfaceType.led_perimeter, Side.near, None, None)
            for k, (box, st, side, board, brand, scale, kw) in enumerate(specs):
                detections.append(Detection(
                    detection_id=f"{match_id}:d{n}:{k}", match_id=match_id, frame_id=frame.frame_id,
                    shot_id=shot_id, t=t, bbox=box, surface_type=st, side=side, board_id=board,
                    brand_id=brand, confidence=round(rng.uniform(0.8, 0.97), 3), reader=group_reader,
                    area_pct=round(box.w * box.h * 100 * scale, 3),
                    center_dist=round(rng.uniform(0.15, 0.6), 3),
                    sharpness=round(rng.uniform(80, 160), 1), **kw))
        yield frame, detections, decisions


def seed_services(match: Match) -> None:
    """Idempotent: Postgres brands/contracts, Valkey match hash + `sp:matches`."""
    from app import stores
    from workers.aggregate import _match_hash

    pg = stores.postgres()
    with pg.connect() as conn:
        for brand_id, name in BRANDS.items():
            conn.execute(
                "INSERT INTO brands (brand_id, club_id, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (brand_id) DO UPDATE SET club_id = EXCLUDED.club_id, name = EXCLUDED.name",
                (brand_id, CLUB_ID, name))
            conn.execute(
                "INSERT INTO contracts (club_id, brand_id, surface_type, target_seconds) "
                "VALUES (%s, %s, NULL, %s) "
                "ON CONFLICT (club_id, brand_id, (coalesce(surface_type, ''))) "
                "DO UPDATE SET target_seconds = EXCLUDED.target_seconds",
                (CLUB_ID, brand_id, TARGETS[brand_id]))
    r = stores.valkey()
    r.hset(f"sp:match:{match.match_id}", mapping=_match_hash(match))
    r.sadd("sp:matches", match.match_id)
    r.expire(f"sp:match:{match.match_id}", 7 * 24 * 3600)


def run(match_id: str = MATCH_ID, duration_s: float = DURATION_S, speed: float = 1.0, seed: int = 7,
        seed_db: bool = True, match_duration_s: float = DURATION_S) -> dict[str, int]:
    """Publish `duration_s` of match clock at `speed`x real time. Returns message counts per topic."""
    from app import stores

    match = synthetic_match(match_id, match_duration_s)
    if seed_db:
        seed_services(match)
    producer = stores.kafka_producer()
    counts = {"frames": 0, "detections": 0, "decisions": 0}
    t0 = time.monotonic()
    for frame, dets, decs in generate(match_id, duration_s, seed):
        wait = t0 + frame.t / speed - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        producer.send("frames", key=match_id, value=frame)
        counts["frames"] += 1
        for d in dets:
            producer.send("detections", key=match_id, value=d)
            counts["detections"] += 1
        for d in decs:
            producer.send("decisions", key=match_id, value=d)
            counts["decisions"] += 1
    producer.flush(timeout=30)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish a SYNTHETIC match to Kafka (invented data).")
    ap.add_argument("--match-id", default=MATCH_ID)
    ap.add_argument("--duration", type=float, default=DURATION_S, help="seconds of match clock to publish")
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-seed", action="store_true", help="do not touch Postgres/Valkey")
    a = ap.parse_args()
    print(f"SYNTHETIC stream {a.match_id}: {a.duration:.0f}s at {a.speed}x (invented, not a measurement)")
    print(run(a.match_id, a.duration, a.speed, a.seed, not a.no_seed))


if __name__ == "__main__":
    main()
