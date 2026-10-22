"""Ingest worker (T06): video file -> `Frame` events on Kafka `frames` (+ evidence JPEGs in Postgres).

    python -m workers ingest --video data/test_match.mp4 --match-id m-test [--speed 1] [--source RECORDED]

Per run: registers the Match (Valkey `sp:match:<id>` + `sp:matches`, like scripts/fake_stream.py), reads the
video at `--speed` x real time, detects shot cuts on every decoded frame (HSV histogram), samples
SAMPLED_FPS frames, masks broadcaster graphics (configured rectangles + static regions learned across shots),
groups near-identical frames (perceptual hash) and for each sampled frame publishes a `Frame` (JSON only, no
images on Kafka). The first frame of each group is representative: its <=640 px JPEG goes to Postgres
`evidence`, and its masked full-resolution JPEG to `data/frames/<match_id>/<frame_no>.jpg` for the readers.

Overlay rectangles: `--overlay x,y,w,h` (repeatable, normalised) or env OVERLAY_RECTS="x,y,w,h;x,y,w,h".
Time `t` is the video clock (seconds from the start of the file), as in docs/CONTRACTS.md.
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import ROOT, settings
from app.contracts import Frame, Match, Source

log = logging.getLogger("ingest")

FRAMES_DIR = ROOT / "data" / "frames"
EVIDENCE_MAX_SIDE = 640
PRESCAN_STEP_S = 1.0
PRESCAN_MAX = 120


@dataclass
class IngestStats:
    match_id: str
    frames: int = 0           # Frame events published
    groups: int = 0           # near-duplicate groups (= representative frames)
    shots: int = 0
    cuts_s: list[float] = field(default_factory=list)
    evidence_rows: int = 0
    elapsed_s: float = 0.0
    video_s: float = 0.0
    mask_pct: float = 0.0     # share of the frame the final overlay mask covers

    @property
    def dedup_ratio(self) -> float:
        """Share of sampled frames that are near-duplicates of their group's first frame."""
        return 1 - self.groups / self.frames if self.frames else 0.0

    def summary(self) -> str:
        return (f"{self.match_id}: {self.frames} frames, {self.groups} groups "
                f"(dedup ratio {self.dedup_ratio:.2f}), {self.shots} shots, cuts at {self.cuts_s}, "
                f"{self.evidence_rows} evidence rows, mask {self.mask_pct:.1f}% of frame, "
                f"{self.video_s:.1f}s of video in {self.elapsed_s:.1f}s")


class Sinks:
    """Where results go. Default = Kafka + Postgres + Valkey; tests pass fakes (see `DrySinks`)."""

    def register(self, match: Match) -> None:
        from app import stores
        from workers.aggregate import _match_hash

        r = stores.valkey()
        r.hset(f"sp:match:{match.match_id}", mapping=_match_hash(match))
        r.sadd("sp:matches", match.match_id)
        r.expire(f"sp:match:{match.match_id}", 7 * 24 * 3600)

    def publish(self, frame: Frame) -> None:
        from app import stores

        stores.kafka_producer().send("frames", key=frame.match_id, value=frame)

    def save_evidence(self, frame: Frame, jpeg: bytes, width: int, height: int) -> None:
        from app import stores

        if not hasattr(self, "_conn"):
            self._conn = stores.postgres().connect()
        self._conn.execute(
            "INSERT INTO evidence (frame_id, match_id, t, jpeg, width, height) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (frame_id) DO UPDATE SET jpeg = EXCLUDED.jpeg, t = EXCLUDED.t, "
            "width = EXCLUDED.width, height = EXCLUDED.height",
            (frame.frame_id, frame.match_id, frame.t, jpeg, width, height))

    def close(self) -> None:
        from app import stores

        stores.kafka_producer().flush(timeout=30)
        if hasattr(self, "_conn"):
            self._conn.close()


class DrySinks(Sinks):
    """Collects everything in memory; touches no service."""

    def __init__(self):
        self.matches: list[Match] = []
        self.frames: list[Frame] = []
        self.evidence: dict[str, bytes] = {}

    def register(self, match):
        self.matches.append(match)

    def publish(self, frame):
        self.frames.append(frame)

    def save_evidence(self, frame, jpeg, width, height):
        self.evidence[frame.frame_id] = jpeg

    def close(self):
        pass


def _prescan(cap: Any, learner: Any, fps: float, n_frames: int) -> None:
    """Seek through the file once and feed one frame per shot to the static-region learner."""
    import cv2

    from vision import preprocess as pp

    step = max(1, round(PRESCAN_STEP_S * fps))
    idxs = list(range(0, n_frames, step))
    if len(idxs) > PRESCAN_MAX:
        idxs = idxs[:: -(-len(idxs) // PRESCAN_MAX)]
    prev, shot = None, 0
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        h = pp.frame_hist(frame)
        if prev is not None and pp.hist_distance(prev, h) > pp.CUT_THRESHOLD:
            shot += 1
        prev = h
        learner.add(frame, shot)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)


def ingest(video: str | pathlib.Path, match_id: str, *, speed: float = 1.0, source: str = "RECORDED",
           sampled_fps: float | None = None, overlay: list | None = None, club_id: str = "unknown",
           stadium_id: str = "unknown", title: str | None = None, sinks: Sinks | None = None,
           frames_dir: pathlib.Path | None = None, prescan: bool = True, max_seconds: float | None = None,
           ) -> IngestStats:
    """Run the pipeline on `video`. `speed` = playback speed (1 = real time, 0 = as fast as possible)."""
    import cv2
    import numpy as np

    from vision import preprocess as pp

    sinks = sinks or Sinks()
    sfps = sampled_fps or settings().sampled_fps
    overlay = overlay if overlay is not None else pp.parse_rects(os.getenv("OVERLAY_RECTS", ""))
    out_dir = (frames_dir or FRAMES_DIR) / match_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = n_total / fps if n_total else None
    if max_seconds is not None:
        duration = min(duration, max_seconds) if duration else max_seconds

    learner = pp.StaticRegionLearner()
    if prescan and n_total:
        _prescan(cap, learner, fps, n_total)

    match = Match(match_id=match_id, club_id=club_id, stadium_id=stadium_id,
                  title=title or f"Ingest of {pathlib.Path(video).name}", source=Source(source),
                  duration_s=duration, sampled_fps=sfps, started_at=datetime.now(UTC))
    sinks.register(match)

    st = IngestStats(match_id)
    wall0 = time.monotonic()
    prev_hist = None
    shot_no, last_cut_t = 0, -1e9
    next_k = 0                      # index of the next sample
    group_no, anchor, anchor_shot = -1, None, None
    mask = None
    n = -1
    last_t = 0.0
    while True:
        if not cap.grab():
            break
        n += 1
        t = n / fps
        if max_seconds is not None and t >= max_seconds:
            break
        ok, frame = cap.retrieve()
        if not ok:
            break
        last_t = t
        hist = pp.frame_hist(frame)
        if prev_hist is not None and pp.hist_distance(prev_hist, hist) > pp.CUT_THRESHOLD \
                and t - last_cut_t >= pp.MIN_SHOT_S:
            shot_no += 1
            last_cut_t = t
            st.cuts_s.append(round(t, 3))
        prev_hist = hist
        if t + 1e-9 < next_k / sfps:
            continue
        while t + 1e-9 >= (next_k + 1) / sfps:  # sampling slower than decoding rate: skip, never burst
            next_k += 1
        # ---- sampled frame ----
        if speed > 0:
            wait = wall0 + t / speed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        h, w = frame.shape[:2]
        if learner.add(frame, shot_no) or mask is None:
            learned = learner.mask((h, w))
            mask = pp.rects_to_mask((h, w), overlay) | learned if overlay else learned
        masked = pp.apply_mask(frame, mask)
        ph = pp.phash(masked)
        shot_id = f"{match_id}:s{shot_no}"
        new_group = anchor is None or shot_no != anchor_shot or pp.hamming(anchor, ph) > pp.HASH_MAX_DIST
        if new_group:
            group_no += 1
            anchor, anchor_shot = ph, shot_no
        frame_no = next_k
        fr = Frame(frame_id=f"{match_id}:{frame_no}", match_id=match_id, shot_id=shot_id,
                   group_id=f"{match_id}:g{group_no}", t=round(t, 3), width=w, height=h,
                   is_representative=new_group)
        if new_group:
            jpg = pp.to_jpeg(masked, EVIDENCE_MAX_SIDE)
            ev_h, ev_w = cv2.imdecode(np.frombuffer(jpg, np.uint8), 1).shape[:2]
            sinks.save_evidence(fr, jpg, ev_w, ev_h)
            st.evidence_rows += 1
            (out_dir / f"{frame_no}.jpg").write_bytes(pp.to_jpeg(masked, None, 90))
        sinks.publish(fr)
        st.frames += 1
        next_k += 1
    cap.release()
    sinks.close()
    st.groups = group_no + 1
    st.shots = shot_no + 1
    st.video_s = last_t
    st.elapsed_s = time.monotonic() - wall0
    if mask is not None:
        st.mask_pct = 100 * float((mask > 0).mean())
    return st


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ingest a video into Kafka `frames` (T06).")
    ap.add_argument("--video", required=True)
    ap.add_argument("--match-id", required=True)
    ap.add_argument("--speed", type=float, default=1.0, help="1 = real time, 0 = as fast as possible")
    ap.add_argument("--source", default="RECORDED", choices=["LIVE", "RECORDED"])
    ap.add_argument("--overlay", action="append", default=None, help="normalised x,y,w,h (repeatable)")
    ap.add_argument("--club-id", default="unknown")
    ap.add_argument("--stadium-id", default="unknown")
    ap.add_argument("--title")
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--no-prescan", action="store_true", help="learn static overlays online only")
    ap.add_argument("--dry-run", action="store_true", help="touch no service (no Kafka/Postgres/Valkey)")
    argv = sys.argv[2:] if argv is None and sys.argv[1:2] == ["ingest"] else argv  # python -m workers ingest
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    from vision import preprocess as pp

    overlay = [r for spec in a.overlay for r in pp.parse_rects(spec)] if a.overlay else None
    stats = ingest(a.video, a.match_id, speed=a.speed, source=a.source, overlay=overlay, club_id=a.club_id,
                   stadium_id=a.stadium_id, title=a.title, prescan=not a.no_prescan,
                   max_seconds=a.max_seconds, sinks=DrySinks() if a.dry_run else None)
    print(stats.summary())


if __name__ == "__main__":
    main()
