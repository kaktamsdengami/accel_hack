"""Generate a synthetic 'match' video for testing ingest (T06). No real footage, nothing copyrighted.

Contents: several shots with different backgrounds separated by hard cuts, a fake score bug (dark rectangle
with text) and a fake watermark in the corners on every frame, a green band with coloured text boards, a big
moving block (so frames inside a shot differ), and one frozen stretch (near-identical frames) in the middle.

    python -m scripts.make_test_video --out data/test_match.mp4 --seconds 20
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

SIZE = (854, 480)  # w, h
FPS = 25
CUT_FRACTIONS = (0.0, 0.2, 0.45, 0.65, 0.85)  # shot starts, as a fraction of the duration
FREEZE_FRACTIONS = (0.50, 0.60)               # frozen stretch (inside shot 2), fraction of the duration
SCORE_BUG = (0.03, 0.04, 0.22, 0.09)          # normalised x, y, w, h
WATERMARK = (0.86, 0.04, 0.10, 0.08)
BOARD_TEXT = ("ACME", "BOLT", "CIRRUS", "DELTA", "EMBER")
BOARD_COLORS = ((30, 30, 220), (220, 120, 20), (20, 200, 220), (200, 40, 160), (40, 180, 60))
TINTS = ((200, 90, 60), (60, 130, 200), (90, 200, 120), (190, 190, 70), (150, 70, 190))


def _background(shot: int, w: int, h: int) -> np.ndarray:
    rng = np.random.default_rng(100 + shot)
    base = np.array(TINTS[shot % len(TINTS)], np.float32)
    blobs = rng.uniform(0.4, 1.0, (6, 8)).astype(np.float32)
    bg = cv2.resize(blobs, (w, h), interpolation=cv2.INTER_CUBIC)[..., None] * base
    return np.clip(bg, 0, 255).astype(np.uint8)


def _rect_px(r, w, h):
    return int(r[0] * w), int(r[1] * h), int((r[0] + r[2]) * w), int((r[1] + r[3]) * h)


def render_frame(shot: int, local_t: float, w: int, h: int, bg_cache: dict, noise_rng) -> np.ndarray:
    if shot not in bg_cache:
        bg = _background(shot, w, h)
        grass = int(60 + 35 * (shot % 4))
        y0 = int(h * 0.7)
        band = np.full((h - y0, w, 3), (30, grass + 60, 30), np.int16)
        band += noise_rng.integers(-25, 25, band.shape[:2])[..., None]
        bg[y0:] = np.clip(band, 0, 255).astype(np.uint8)
        for k in range(4):  # coloured text boards along the top of the green band
            x0 = int(((k * 0.24 + 0.03 * shot) % 0.78) * w)
            c = BOARD_COLORS[(k + shot) % len(BOARD_COLORS)]
            cv2.rectangle(bg, (x0, int(h * 0.62)), (x0 + int(w * 0.2), int(h * 0.69)), c, -1)
            cv2.putText(bg, BOARD_TEXT[(k + shot) % len(BOARD_TEXT)], (x0 + 8, int(h * 0.67)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        bg_cache[shot] = bg
    f = bg_cache[shot].copy()
    bw, bh = int(w * 0.25), int(h * 0.3)  # big moving block
    x = int((local_t * 0.35 * w + shot * 90) % (w - bw))
    cv2.rectangle(f, (x, int(h * 0.25)), (x + bw, int(h * 0.25) + bh), (235, 235, 235), -1)
    cv2.circle(f, (w - x - 40, int(h * 0.5)), 30, (20, 20, 20), -1)
    x0, y0, x1, y1 = _rect_px(SCORE_BUG, w, h)  # static broadcaster graphics
    cv2.rectangle(f, (x0, y0), (x1, y1), (25, 25, 25), -1)
    cv2.putText(f, "ARS 1-0 CHE", (x0 + 6, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
                cv2.LINE_AA)
    x0, y0, x1, y1 = _rect_px(WATERMARK, w, h)
    cv2.rectangle(f, (x0, y0), (x1, y1), (110, 110, 110), -1)
    cv2.putText(f, "TV", (x0 + 10, y1 - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 2, cv2.LINE_AA)
    return f


def plan(seconds: float) -> dict:
    return {"cuts_s": [round(c * seconds, 3) for c in CUT_FRACTIONS[1:]],
            "freeze_s": [round(f * seconds, 3) for f in FREEZE_FRACTIONS],
            "score_bug": SCORE_BUG, "watermark": WATERMARK, "fps": FPS, "size": SIZE}


def make_video(path: str | pathlib.Path, seconds: float = 20.0, fps: int = FPS, size=SIZE,
               seed: int = 1) -> dict:
    """Write the video and return the ground truth (cut times, frozen stretch, overlay rectangles)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        raise RuntimeError("cannot open VideoWriter (mp4v)")
    rng = np.random.default_rng(seed)
    starts = [c * seconds for c in CUT_FRACTIONS]
    fz0, fz1 = (f * seconds for f in FREEZE_FRACTIONS)
    cache: dict = {}
    frozen = None
    for n in range(int(seconds * fps)):
        t = n / fps
        shot = max(i for i, s in enumerate(starts) if t >= s - 1e-9)
        if fz0 <= t < fz1:
            if frozen is None:
                frozen = render_frame(shot, t - starts[shot], w, h, cache, rng)
            f = np.clip(frozen.astype(np.float32) + rng.normal(0, 1.0, frozen.shape), 0, 255).astype(np.uint8)
        else:
            f = render_frame(shot, t - starts[shot], w, h, cache, rng)
        vw.write(f)
    vw.release()
    return plan(seconds)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/test_match.mp4")
    ap.add_argument("--seconds", type=float, default=20.0)
    a = ap.parse_args()
    print(make_video(a.out, a.seconds))


if __name__ == "__main__":
    main()
