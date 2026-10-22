"""Replay a recorded vision run (python -m vision.identify run ...) into the live pipeline, for the demo.

Every value comes from the run folder: detections.json, decisions.json, summary.json and frames/<n>.jpg,
measured by the vision model on a RECORDED broadcast. Nothing is generated. The replay publishes them at
`--speed`x real time to Kafka (`frames`, `detections`, `decisions`), stores each frame as evidence in Postgres
(<= 640 px JPEG) and registers the match and brands, exactly like scripts/fake_stream.py, so the deployed
aggregation worker and the dashboard show it live.

    python -m scripts.replay_run --run data/runs/ucl --sponsors data/gold/sponsors.reference.csv \
        --match-id m-ucl-demo --title "UCL broadcast segment (RECORDED)" [--speed 1]
"""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import time
from datetime import UTC, datetime

from app.contracts import DecisionLog, Detection, Frame, Match, Source

CLUB_ID = "c-demo"
STADIUM_ID = "st-demo"


def thumb(jpeg: bytes, max_side: int = 640) -> tuple[bytes, int, int, int, int]:
    """(<=640 px JPEG, thumb w, thumb h, original w, original h)."""
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = im.size
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue(), im.width, im.height, W, H


def rename(match_id: str, old: str, value: str) -> str:
    """Ids in the run start with the run's match id; move them to the replay's match id."""
    return match_id + value[len(old) :] if value.startswith(old + ":") else value


def load(run: pathlib.Path, match_id: str):
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    old = summary["match_id"]
    dets = [
        Detection.model_validate(d) for d in json.loads((run / "detections.json").read_text(encoding="utf-8"))
    ]
    decs = [
        DecisionLog.model_validate(d)
        for d in json.loads((run / "decisions.json").read_text(encoding="utf-8"))
    ]
    for x in (*dets, *decs):
        x.match_id = match_id
        for f in ("frame_id", "shot_id", "group_id", "detection_id", "decision_id"):
            if hasattr(x, f) and isinstance(getattr(x, f), str):
                setattr(x, f, rename(match_id, old, getattr(x, f)))
    return summary, old, dets, decs


def main() -> None:
    from app import stores
    from vision.identify import load_sponsors
    from workers.aggregate import _match_hash

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--sponsors", required=True)
    ap.add_argument("--match-id", default="m-ucl-demo")
    ap.add_argument("--title", default="Broadcast segment (RECORDED)")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()

    run = pathlib.Path(a.run)
    summary, old, dets, decs = load(run, a.match_id)
    fps = float(summary["fps"])
    duration = round(len(decs) / fps, 3)
    match = Match(
        match_id=a.match_id,
        club_id=CLUB_ID,
        stadium_id=STADIUM_ID,
        title=a.title,
        source=Source.RECORDED,
        duration_s=duration,
        sampled_fps=fps,
        started_at=datetime.now(UTC),
    )

    pg = stores.postgres()
    with pg.connect() as conn:
        for b in load_sponsors(a.sponsors):
            conn.execute(
                "INSERT INTO brands (brand_id, club_id, name, aliases) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (brand_id) DO UPDATE SET club_id = EXCLUDED.club_id, name = EXCLUDED.name, "
                "aliases = EXCLUDED.aliases",
                (b.brand_id, CLUB_ID, b.name, list(b.aliases)),
            )
    r = stores.valkey()
    r.hset(f"sp:match:{a.match_id}", mapping=_match_hash(match))
    r.sadd("sp:matches", a.match_id)
    r.expire(f"sp:match:{a.match_id}", 7 * 24 * 3600)

    by_frame: dict[str, list[Detection]] = {}
    for d in dets:
        by_frame.setdefault(d.frame_id, []).append(d)
    producer = stores.kafka_producer()
    print(
        f"replaying {len(decs)} frames of {run} as {a.match_id} at {a.speed}x ({summary['model']}, RECORDED)"
    )
    t0 = time.monotonic()
    for dec in sorted(decs, key=lambda d: d.t):
        frame_id = dec.group_id
        n = frame_id.rsplit(":", 1)[-1]
        jpg = run / "frames" / f"{n}.jpg"
        w, h = 1280, 720
        if jpg.exists():
            small, _, _, w, h = thumb(jpg.read_bytes())
            with pg.connect() as conn:
                conn.execute(
                    "INSERT INTO evidence (frame_id, match_id, t, jpeg, width, height) "
                    "VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (frame_id) DO UPDATE SET jpeg = EXCLUDED.jpeg",
                    (frame_id, a.match_id, dec.t, small, w, h),
                )
        wait = t0 + dec.t / a.speed - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        producer.send(
            "frames",
            key=a.match_id,
            value=Frame(
                frame_id=frame_id,
                match_id=a.match_id,
                shot_id=dec.shot_id,
                group_id=frame_id,
                t=dec.t,
                width=w,
                height=h,
                is_representative=True,
            ),
        )
        for d in by_frame.get(frame_id, []):
            producer.send("detections", key=a.match_id, value=d)
        producer.send("decisions", key=a.match_id, value=dec)
        producer.flush(timeout=10)
        print(f"t={dec.t:6.1f}s {len(by_frame.get(frame_id, []))} logos | {dec.reason[:90]}")
    producer.flush(timeout=30)
    print("done")


if __name__ == "__main__":
    main()
