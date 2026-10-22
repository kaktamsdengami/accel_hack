"""Frame pre-processing for the ingest worker (T06). Pure functions: no I/O, no Kafka, no globals.

Needs the `vision` extra (opencv-python-headless, numpy). Nothing else in the repo imports this module at
import time, so CI jobs without OpenCV keep working.

Pieces:
- shot cuts: `frame_hist` + `hist_distance` + `detect_cuts` (HSV colour histogram difference);
- overlay mask: configured normalised rectangles (`rects_to_mask`), plus `StaticRegionLearner` which finds
  broadcaster graphics (score bug, watermark) as regions that stay identical across several shots;
- near-duplicate grouping: `phash` / `hamming` / `group_near_duplicates`;
- `is_replay`: hook, always False until a replay detector exists.
"""
from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

Rect = tuple[float, float, float, float]  # normalised x, y, w, h (origin top-left), as BBox in CONTRACTS

HIST_BINS = (16, 4, 4)       # H, S, V
CUT_THRESHOLD = 0.45         # total-variation distance (0..1) between consecutive frame histograms
MIN_SHOT_S = 0.5             # ignore a "cut" closer than this to the previous one
MASK_FILL = 127
HASH_MAX_DIST = 6            # Hamming bits (of 64) still counted as a near-duplicate


# --- shot cuts ------------------------------------------------------------------------------------

def frame_hist(frame_bgr: np.ndarray, width: int = 160) -> np.ndarray:
    """Normalised HSV histogram (sums to 1) of a frame shrunk to `width` px."""
    h, w = frame_bgr.shape[:2]
    small = cv2.resize(frame_bgr, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BINS), [0, 180, 0, 256, 0, 256]).ravel()
    return hist / max(float(hist.sum()), 1e-9)


def hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Total-variation distance between two normalised histograms: 0 identical, 1 disjoint."""
    return float(0.5 * np.abs(a - b).sum())


def detect_cuts(hists: Sequence[np.ndarray], times: Sequence[float], threshold: float = CUT_THRESHOLD,
                min_shot_s: float = MIN_SHOT_S) -> list[int]:
    """Indices i (>= 1) where frame i starts a new shot (hist(i) differs from hist(i-1) by > threshold)."""
    cuts: list[int] = []
    last_t = -1e9
    for i in range(1, len(hists)):
        if hist_distance(hists[i - 1], hists[i]) > threshold and times[i] - last_t >= min_shot_s:
            cuts.append(i)
            last_t = times[i]
    return cuts


# --- overlay mask ---------------------------------------------------------------------------------

def parse_rects(spec: str) -> list[Rect]:
    """'x,y,w,h;x,y,w,h' (normalised) -> rect list. Empty string -> []."""
    out: list[Rect] = []
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        x, y, w, h = (float(v) for v in part.split(","))
        out.append((x, y, w, h))
    return out


def rects_to_mask(shape: tuple[int, int], rects: Sequence[Rect]) -> np.ndarray:
    """uint8 mask (255 = overlay) of `shape` (h, w) from normalised rectangles."""
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for x, y, rw, rh in rects:
        x0, y0 = max(0, int(np.floor(x * w))), max(0, int(np.floor(y * h)))
        x1, y1 = min(w, int(np.ceil((x + rw) * w))), min(h, int(np.ceil((y + rh) * h)))
        mask[y0:y1, x0:x1] = 255
    return mask


def detect_static_regions(grays: Sequence[np.ndarray], std_max: float = 9.0, min_area: float = 0.001,
                          max_area: float = 0.15, edge_margin: float = 0.15, grow_px: int = 2) -> np.ndarray:
    """Mask (255) of regions that stay (nearly) identical across `grays` (frames from different shots).

    Keeps connected blobs whose area is within [min_area, max_area] of the frame and that touch the outer
    `edge_margin` band of the frame (score bugs and watermarks sit in corners, not in the middle of play).
    """
    if len(grays) < 2:
        return np.zeros(grays[0].shape[:2] if grays else (1, 1), np.uint8)
    stack = np.stack([g.astype(np.float32) for g in grays])
    static = (stack.std(axis=0) < std_max).astype(np.uint8) * 255
    static = cv2.morphologyEx(static, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    static = cv2.morphologyEx(static, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(static, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(static, contours, -1, 255, thickness=cv2.FILLED)  # fill outlines (hollow logo boxes)
    h, w = static.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(static, connectivity=8)
    out = np.zeros_like(static)
    mx, my = edge_margin * w, edge_margin * h
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (min_area * w * h <= area <= max_area * w * h):
            continue
        if x < mx or y < my or x + bw > w - mx or y + bh > h - my:
            out[labels == i] = 255
    if grow_px:
        out = cv2.dilate(out, np.ones((2 * grow_px + 1, 2 * grow_px + 1), np.uint8))
    return out


class StaticRegionLearner:
    """Collects one small gray frame per shot and derives the static-overlay mask once enough shots exist."""

    def __init__(self, min_shots: int = 3, max_frames: int = 16, width: int = 320, **kw):
        self.min_shots, self.max_frames, self.width, self.kw = min_shots, max_frames, width, kw
        self.grays: list[np.ndarray] = []
        self.shot_ids: list[int] = []
        self._cached: np.ndarray | None = None

    def add(self, frame_bgr: np.ndarray, shot_no: int) -> bool:
        """Add a frame of shot `shot_no` (first one per shot is kept). True if the mask may have changed."""
        if shot_no in self.shot_ids:
            return False
        h, w = frame_bgr.shape[:2]
        small = cv2.resize(frame_bgr, (self.width, max(1, round(h * self.width / w))),
                           interpolation=cv2.INTER_AREA)
        self.grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        self.shot_ids.append(shot_no)
        if len(self.grays) > self.max_frames:
            self.grays.pop(0)
            self.shot_ids.pop(0)
        self._cached = None
        return len(self.grays) >= self.min_shots

    def mask(self, shape: tuple[int, int]) -> np.ndarray:
        """Learned mask resized to `shape` (h, w); all zero until `min_shots` shots were seen."""
        h, w = shape[:2]
        if len(self.grays) < self.min_shots:
            return np.zeros((h, w), np.uint8)
        if self._cached is None:
            self._cached = detect_static_regions(self.grays, **self.kw)
        return cv2.resize(self._cached, (w, h), interpolation=cv2.INTER_NEAREST)


def apply_mask(frame_bgr: np.ndarray, mask: np.ndarray, fill: int = MASK_FILL) -> np.ndarray:
    """Copy of the frame with masked pixels set to a flat gray."""
    out = frame_bgr.copy()
    if mask.shape[:2] != out.shape[:2]:
        mask = cv2.resize(mask, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_NEAREST)
    out[mask > 0] = fill
    return out


def mask_coverage(mask: np.ndarray, rect: Rect) -> float:
    """Fraction of the normalised rectangle covered by the mask (for tests and logging)."""
    h, w = mask.shape[:2]
    x, y, rw, rh = rect
    sub = mask[int(y * h):int(np.ceil((y + rh) * h)), int(x * w):int(np.ceil((x + rw) * w))]
    return float((sub > 0).mean()) if sub.size else 0.0


# --- near-duplicate grouping ----------------------------------------------------------------------

def phash(frame_bgr: np.ndarray) -> int:
    """64-bit perceptual hash (DCT of a 32x32 gray thumbnail, 8x8 low frequencies vs their median)."""
    g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    g = cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(g)[:8, :8].ravel()
    bits = low > np.median(low[1:])
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def group_near_duplicates(hashes: Sequence[int], shot_ids: Sequence[int | str] | None = None,
                          max_dist: int = HASH_MAX_DIST) -> list[int]:
    """Group index per frame. A frame joins the current group if it is within `max_dist` bits of the
    group's first frame and in the same shot; otherwise it starts a new group (groups are contiguous)."""
    groups: list[int] = []
    anchor: int | None = None
    anchor_shot: int | str | None = None
    g = -1
    for i, h in enumerate(hashes):
        shot = shot_ids[i] if shot_ids is not None else None
        if anchor is None or shot != anchor_shot or hamming(anchor, h) > max_dist:
            g += 1
            anchor, anchor_shot = h, shot
        groups.append(g)
    return groups


def is_replay(frame_bgr: np.ndarray, shot_frames: Sequence[np.ndarray] | None = None) -> bool:
    """Replay detector hook (logo wipe, slow motion cues). Returns False until implemented."""
    return False


def to_jpeg(frame_bgr: np.ndarray, max_side: int | None = None, quality: int = 80) -> bytes:
    """JPEG bytes, optionally shrunk so the longest side is <= max_side (never upscaled)."""
    h, w = frame_bgr.shape[:2]
    if max_side and max(h, w) > max_side:
        s = max_side / max(h, w)
        frame_bgr = cv2.resize(frame_bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()
