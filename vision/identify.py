"""Vision identifier (T08): sponsor brands from frames with a vision LLM. OpenAI default, Claude switchable.

`VISION_PROVIDER=openai|anthropic` picks the provider
(model: `OPENAI_VISION_MODEL` / `ANTHROPIC_VISION_MODEL`).
Three modes, all answering JSON constrained by a schema (prompts: agents/prompts/identify.md):

- read      crops of detected surfaces -> one brand per box (`VisionReader`, the router's BrandReader).
- find      a whole frame -> sponsor logos with bbox, surface type, side (no surface detector needed).
- discover  a whole frame -> every brand name it can read (builds data/sponsors.draft.csv).

Brands are from the sponsor list only: the schema enum allows only listed brand_ids or null, and anything else
that still comes back is dropped and counted as `off_list`. Unknown stays unknown, a frame without sponsors
returns an empty list. Every call logs tokens and cost (log line + data/costs/vision_calls.jsonl); the SDK
retries timeouts, 429 and 5xx (`VISION_MAX_RETRIES`, `VISION_TIMEOUT_S`).

Prices are USD per million tokens from PRICES or `VISION_PRICE_IN_PER_MTOK` / `VISION_PRICE_OUT_PER_MTOK`;
a model without a price is refused, because the budget guard needs a cost per call.

CLI (needs .env with the provider key):

    python -m vision.identify run --video "data/videos/5min .mp4" \
        --sponsors data/gold/sponsors.reference.csv --fps 1 --max-usd 1.0 --name gpt-5min
        # -> data/runs/<name>/pred.json + decisions.json for eval.harness
    python -m vision.identify check --frames data/frames/<match_id> --sponsors S.csv --n 20  # contracts
    python -m vision.identify discover --video V --fps 0.2 --max-usd 0.3   # writes data/sponsors.draft.csv
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import logging
import math
import os
import pathlib
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app import config  # noqa: F401  (loads .env)
from app.contracts import BBox, DecisionLog, Detection, Reader, Side, SurfaceBox, SurfaceType
from vision.readers import BoxRead, Brand, FrameGroup, ReaderResult

log = logging.getLogger("identify")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agents" / "prompts" / "identify.md"
COST_LOG = ROOT / "data" / "costs" / "vision_calls.jsonl"

# USD per 1M tokens (input, output). Sources: platform.openai.com/pricing, the Claude API model table.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_MODEL = {"openai": "gpt-5-mini", "anthropic": "claude-opus-5-5"}
READER_OF = {"openai": Reader.gpt, "anthropic": Reader.claude}
SURFACES = [s.value for s in SurfaceType]
SIDES = [s.value for s in Side]
_LOG_LOCK = threading.Lock()
# SDK errors that no retry fixes (same class names in openai and anthropic).
FATAL_ERRORS = {"AuthenticationError", "PermissionDeniedError", "NotFoundError"}
# Assumed tokens of one call, only to reserve budget before the real usage is known.
EXPECTED_TOKENS_IN, EXPECTED_TOKENS_OUT = 2500, 600


def load_prompts(path: pathlib.Path = PROMPTS) -> dict[str, str]:
    parts = re.split(r"^## (\w+)\s*$", path.read_text(encoding="utf-8"), flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def sponsor_lines(sponsors: Sequence[Brand]) -> str:
    out = []
    for b in sponsors:
        alias = f" (also: {', '.join(b.aliases)})" if b.aliases else ""
        out.append(f"- {b.brand_id}: {b.name}{alias}")
    return "\n".join(out) or "- (empty list: answer null / empty)"


def load_sponsors(path: str | pathlib.Path) -> list[Brand]:
    """CSV with header `brand_id,name[,aliases]`; aliases separated by `;`."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        bid = (r.get("brand_id") or "").strip()
        if not bid:
            continue
        aliases = tuple(a.strip() for a in (r.get("aliases") or "").split(";") if a.strip())
        out.append(Brand(bid, (r.get("name") or bid).strip(), aliases))
    return out


def slug(name: str) -> str:
    return "b-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown")


# --- schemas ---


def _brand_field(ids: Sequence[str]) -> dict:
    return {"type": ["string", "null"], "enum": [*ids, None]} if ids else {"type": "null"}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def schema_read(ids: Sequence[str]) -> dict:
    box = _obj(
        {
            "box_id": {"type": "string"},
            "brand_id": _brand_field(ids),
            "confidence": {"type": "number"},
            "runner_up": _brand_field(ids),
            "runner_up_confidence": {"type": "number"},
            "readable": {"type": "boolean"},
        }
    )
    return _obj({"boxes": {"type": "array", "items": box}})


def schema_find(ids: Sequence[str]) -> dict:
    bbox = _obj({k: {"type": "number"} for k in ("x", "y", "w", "h")})
    item = _obj(
        {
            "brand_id": _brand_field(ids),
            "confidence": {"type": "number"},
            "bbox": bbox,
            "surface_type": {"type": "string", "enum": SURFACES},
            "side": {"type": "string", "enum": SIDES},
        }
    )
    return _obj({"logos": {"type": "array", "items": item}})


def schema_discover() -> dict:
    item = _obj({"name": {"type": "string"}, "surface_type": {"type": "string", "enum": SURFACES}})
    return _obj({"brands": {"type": "array", "items": item}})


# --- providers ---


@dataclass
class CallResult:
    data: dict
    tokens_in: int
    tokens_out: int
    latency_ms: float
    model: str


Image = tuple[str, bytes]  # (label shown before the image, JPEG bytes)


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str, client=None, timeout_s: float = 60.0, max_retries: int = 3):
        self.model = model
        if client is None:
            import openai

            client = openai.OpenAI(timeout=timeout_s, max_retries=max_retries)
        self.client = client

    def call(
        self, system: str, text: str, images: Sequence[Image], schema: dict, schema_name: str
    ) -> CallResult:
        content: list[dict] = [{"type": "text", "text": text}]
        for label, jpeg in images:
            if label:
                content.append({"type": "text", "text": label})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                        "detail": "high",
                    },
                }
            )
        kw = {}
        if self.model.startswith("gpt-5"):
            kw["reasoning_effort"] = os.getenv("OPENAI_REASONING_EFFORT", "low")
        t0 = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=self.model,
            max_completion_tokens=4000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
            **kw,
        )
        ms = (time.perf_counter() - t0) * 1000
        choice = resp.choices[0]
        msg = choice.message
        if getattr(msg, "refusal", None):
            raise RuntimeError(f"refused: {msg.refusal}")
        if choice.finish_reason == "length":
            raise RuntimeError("output truncated (finish_reason=length)")
        u = resp.usage
        return CallResult(
            json.loads(msg.content),
            int(u.prompt_tokens),
            int(u.completion_tokens),
            ms,
            getattr(resp, "model", self.model) or self.model,
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, client=None, timeout_s: float = 60.0, max_retries: int = 3):
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(timeout=timeout_s, max_retries=max_retries)
        self.client = client

    def call(
        self, system: str, text: str, images: Sequence[Image], schema: dict, schema_name: str
    ) -> CallResult:
        content: list[dict] = [{"type": "text", "text": text}]
        for label, jpeg in images:
            if label:
                content.append({"type": "text", "text": label})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode(),
                    },
                }
            )
        t0 = time.perf_counter()
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": os.getenv("ANTHROPIC_EFFORT", "low"),
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        ms = (time.perf_counter() - t0) * 1000
        if resp.stop_reason == "refusal":
            raise RuntimeError("refused")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("output truncated (max_tokens)")
        txt = next(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return CallResult(
            json.loads(txt), int(u.input_tokens), int(u.output_tokens), ms, resp.model or self.model
        )


def price_of(model: str) -> tuple[float, float]:
    pin, pout = os.getenv("VISION_PRICE_IN_PER_MTOK"), os.getenv("VISION_PRICE_OUT_PER_MTOK")
    if pin and pout:
        return float(pin), float(pout)
    for name in sorted(PRICES, key=len, reverse=True):  # dated snapshots: "gpt-5-mini-2025-08-07"
        if model == name or model.startswith(name + "-"):
            return PRICES[name]
    raise ValueError(
        f"no price for model {model!r}: set VISION_PRICE_IN_PER_MTOK and VISION_PRICE_OUT_PER_MTOK"
    )


def make_provider(provider: str | None = None, model: str | None = None, client=None):
    provider = (provider or os.getenv("VISION_PROVIDER", "openai")).lower()
    timeout = float(os.getenv("VISION_TIMEOUT_S", 60))
    retries = int(os.getenv("VISION_MAX_RETRIES", 3))
    if provider == "openai":
        return OpenAIProvider(
            model or os.getenv("OPENAI_VISION_MODEL", DEFAULT_MODEL["openai"]), client, timeout, retries
        )
    if provider == "anthropic":
        return AnthropicProvider(
            model or os.getenv("ANTHROPIC_VISION_MODEL", DEFAULT_MODEL["anthropic"]), client, timeout, retries
        )
    raise ValueError(f"VISION_PROVIDER must be openai or anthropic, not {provider!r}")


# --- identifier ---


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return lo if math.isnan(v) else min(max(v, lo), hi)


def clamp_bbox(b: dict) -> BBox | None:
    x, y = _clamp(b.get("x")), _clamp(b.get("y"))
    w, h = min(_clamp(b.get("w")), 1 - x), min(_clamp(b.get("h")), 1 - y)
    return BBox(x=x, y=y, w=w, h=h) if w > 0 and h > 0 else None


@dataclass
class Found:
    brand_id: str
    confidence: float
    bbox: BBox
    surface_type: SurfaceType
    side: Side


@dataclass
class Call:
    """One provider call, as logged."""

    mode: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    error: str | None = None
    off_list: int = 0


@dataclass
class Identifier:
    provider: object
    prompts: dict[str, str] = field(default_factory=load_prompts)
    cost_log: pathlib.Path | None = COST_LOG
    calls: list[Call] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def reader(self) -> Reader:
        return READER_OF[self.provider.name]

    def expected_cost_usd(self) -> float:
        pin, pout = price_of(self.model)
        return round((EXPECTED_TOKENS_IN * pin + EXPECTED_TOKENS_OUT * pout) / 1e6, 6)

    def _call(
        self, mode: str, text: str, images: Sequence[Image], schema: dict, context: str = ""
    ) -> tuple[dict | None, Call]:
        pin, pout = price_of(self.model)
        try:
            r = self.provider.call(self.prompts["system"], text, images, schema, f"sponsor_{mode}")
            call = Call(
                mode,
                r.model,
                r.tokens_in,
                r.tokens_out,
                round((r.tokens_in * pin + r.tokens_out * pout) / 1e6, 6),
                round(r.latency_ms, 1),
            )
            data = r.data
        except Exception as e:  # noqa: BLE001  (reported, never guessed: caller marks the frame unmeasured)
            if type(e).__name__ in FATAL_ERRORS:  # wrong key / no access / unknown model: stop, do not loop
                raise
            call, data = Call(mode, self.model, 0, 0, 0.0, 0.0, error=f"{type(e).__name__}: {e}"[:300]), None
        self.calls.append(call)
        self._log(call, context)
        return data, call

    def _log(self, c: Call, context: str) -> None:
        log.info(
            "%s %s %s in=%d out=%d $%.5f %.0fms%s",
            c.mode,
            context,
            c.model,
            c.tokens_in,
            c.tokens_out,
            c.cost_usd,
            c.latency_ms,
            f" ERROR {c.error}" if c.error else "",
        )
        if self.cost_log is None:
            return
        try:
            self.cost_log.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_LOCK, self.cost_log.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"ts": datetime.now(UTC).isoformat(), "context": context, **c.__dict__}) + "\n"
                )
        except OSError:
            log.warning("cost log not writable: %s", self.cost_log)

    @property
    def spent_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.calls), 6)

    def find(self, jpeg: bytes, sponsors: Sequence[Brand], context: str = "") -> tuple[list[Found], Call]:
        ids = [b.brand_id for b in sponsors]
        text = self.prompts["find"].replace("{sponsors}", sponsor_lines(sponsors))
        data, call = self._call("find", text, [("", jpeg)], schema_find(ids), context)
        out: list[Found] = []
        for it in (data or {}).get("logos", []):
            bid = it.get("brand_id")
            if bid is None:
                continue
            if bid not in ids:
                call.off_list += 1
                continue
            bbox = clamp_bbox(it.get("bbox") or {})
            if bbox is None:
                continue
            st = it.get("surface_type") if it.get("surface_type") in SURFACES else "other"
            sd = it.get("side") if it.get("side") in SIDES else "unknown"
            out.append(Found(bid, _clamp(it.get("confidence")), bbox, SurfaceType(st), Side(sd)))
        return out, call

    def read_crops(
        self, crops: Sequence[tuple[SurfaceBox, bytes]], sponsors: Sequence[Brand], context: str = ""
    ) -> tuple[list[BoxRead], Call]:
        ids = [b.brand_id for b in sponsors]
        text = self.prompts["read"].replace("{sponsors}", sponsor_lines(sponsors))
        images = [
            (f"box_id={b.box_id} surface={SurfaceType(b.surface_type).value} side={Side(b.side).value}", j)
            for b, j in crops
        ]
        data, call = self._call("read", text, images, schema_read(ids), context)
        got = {a.get("box_id"): a for a in (data or {}).get("boxes", [])}
        reads = []
        for b, _ in crops:
            a = got.get(b.box_id)
            if data is None or a is None:
                reads.append(BoxRead(b.box_id, None, 0.0, margin=0.0, unsure=True))
                continue
            bid, ru = a.get("brand_id"), a.get("runner_up")
            if bid is not None and bid not in ids:
                call.off_list += 1
                bid = None
            ru = ru if ru in ids and ru != bid else None
            conf = _clamp(a.get("confidence")) if bid else 0.0
            margin = max(conf - (_clamp(a.get("runner_up_confidence")) if ru else 0.0), 0.0)
            reads.append(
                BoxRead(
                    b.box_id,
                    bid,
                    conf,
                    margin=round(margin, 4),
                    runner_up=ru,
                    unsure=not a.get("readable", True),
                )
            )
        return reads, call

    def discover(self, jpeg: bytes, context: str = "") -> tuple[list[tuple[str, str]], Call]:
        data, call = self._call(
            "discover", self.prompts["discover"], [("", jpeg)], schema_discover(), context
        )
        return [
            (b["name"].strip(), b.get("surface_type", "other"))
            for b in (data or {}).get("brands", [])
            if b.get("name", "").strip()
        ], call


# --- router reader ---


def crop_jpeg(jpeg: bytes, bbox: BBox, pad: float = 0.15, min_h: int = 96, max_side: int = 2048) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = im.size
    px, py = bbox.w * pad, max(bbox.h * pad, bbox.h * 0.5 if bbox.h < 0.06 else 0)
    box = (
        max(int((bbox.x - px) * W), 0),
        max(int((bbox.y - py) * H), 0),
        min(int(math.ceil((bbox.x + bbox.w + px) * W)), W),
        min(int(math.ceil((bbox.y + bbox.h + py) * H)), H),
    )
    c = im.crop(box)
    s = max(min_h / max(c.height, 1), 1.0)
    s = min(s, max_side / max(c.width * s, c.height * s, 1) * s)
    if s != 1.0:
        c = c.resize((max(int(c.width * s), 1), max(int(c.height * s), 1)))
    buf = io.BytesIO()
    c.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def frame_jpeg_loader(pg_conn_factory: Callable | None = None) -> Callable[[str, str], bytes]:
    """frame_id -> JPEG: data/frames/<match_id>/<frame_no>.jpg (full-res, masked), else Postgres evidence."""

    def load(match_id: str, frame_id: str) -> bytes:
        p = ROOT / "data" / "frames" / match_id / f"{frame_id.rsplit(':', 1)[-1]}.jpg"
        if p.exists():
            return p.read_bytes()
        if pg_conn_factory is None:
            raise FileNotFoundError(p)
        with pg_conn_factory() as conn:
            row = conn.execute("SELECT jpeg FROM evidence WHERE frame_id = %s", (frame_id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"no evidence for {frame_id}")
        return bytes(row[0])

    return load


@dataclass
class VisionReader:
    """BrandReader for the router (vision/readers.py protocol): one call per group, all boxes as crops."""

    identifier: Identifier
    load_jpeg: Callable[[str, str], bytes]

    @property
    def reader(self) -> Reader:
        return self.identifier.reader

    @property
    def model(self) -> str:
        return self.identifier.model

    @property
    def expected_cost_usd(self) -> float:
        return self.identifier.expected_cost_usd()

    def read(self, group: FrameGroup, boxes: list[SurfaceBox], sponsors: list[Brand]) -> ReaderResult:
        rep = group.representative
        jpeg = self.load_jpeg(group.match_id, rep.frame_id)
        reads, call = self.identifier.read_crops(
            [(b, crop_jpeg(jpeg, b.bbox)) for b in boxes], sponsors, context=group.group_id
        )
        return ReaderResult(
            self.reader,
            call.model,
            reads,
            cost_usd=call.cost_usd,
            latency_ms=call.latency_ms,
            tokens_in=call.tokens_in,
            tokens_out=call.tokens_out,
            error=call.error,
        )


# --- frame-level pipeline (no surface detector, no Kafka) ---


def found_to_detections(
    found: Sequence[Found],
    match_id: str,
    frame_id: str,
    shot_id: str,
    t: float,
    reader: Reader,
    min_area_pct: float,
) -> list[Detection]:
    out = []
    for i, f in enumerate(found):
        b = f.bbox
        area = round(b.w * b.h * 100, 4)
        cx, cy = b.x + b.w / 2, b.y + b.h / 2
        center = min(math.hypot(cx - 0.5, cy - 0.5) / math.hypot(0.5, 0.5), 1.0)
        out.append(
            Detection(
                detection_id=f"{frame_id}:v{i}",
                match_id=match_id,
                frame_id=frame_id,
                shot_id=shot_id,
                t=t,
                bbox=b,
                surface_type=f.surface_type,
                side=f.side,
                brand_id=f.brand_id,
                confidence=round(f.confidence, 3),
                reader=reader,
                area_pct=area,
                center_dist=round(center, 4),
                below_threshold=area < min_area_pct,
            )
        )
    return out


def sample_video(path: str, fps: float, max_side: int = 1280, overlay: str = ""):
    """Yield (index, t, shot_no, jpeg) at `fps`, with shot cuts from vision.preprocess histograms."""
    import cv2

    from vision import preprocess as pp

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(int(round(src_fps / fps)), 1)
    rects = pp.parse_rects(overlay) if overlay else []
    prev, shot, k = None, 0, 0
    for fno in range(0, n if n else 10**9, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fno)
        ok, frame = cap.read()
        if not ok:
            break
        h = pp.frame_hist(frame)
        if prev is not None and pp.hist_distance(prev, h) > pp.CUT_THRESHOLD:
            shot += 1
        prev = h
        if rects:
            frame = pp.apply_mask(frame, pp.rects_to_mask(frame.shape[:2], rects))
        yield k, round(fno / src_fps, 3), shot, pp.to_jpeg(frame, max_side, 85)
        k += 1
    cap.release()


def decision_for(
    match_id: str,
    frame_id: str,
    shot_id: str,
    t: float,
    reader: Reader,
    call: Call,
    found: Sequence[Found],
    budget_left: float,
) -> DecisionLog:
    if call.error:
        reason = f"reader_error: {call.error}"
    elif found:
        names = ", ".join(sorted({f.brand_id for f in found}))
        reason = f"{call.model} read {len(found)} sponsor surface(s) on the whole frame: {names}"
    else:
        reason = f"{call.model} saw no sponsor from the list in this frame"
    if call.off_list:
        reason += f" ({call.off_list} answer(s) off the sponsor list dropped)"
    conf = max((f.confidence for f in found), default=0.0 if call.error else 1.0)
    return DecisionLog(
        decision_id=f"{frame_id}:vision",
        match_id=match_id,
        shot_id=shot_id,
        group_id=frame_id,
        t=t,
        readers_tried=[reader],
        chosen_reader=reader,
        confidence=round(conf, 3),
        cost_usd=call.cost_usd,
        latency_ms=call.latency_ms,
        reason=reason,
        budget_left_usd=round(budget_left, 6),
    )


def run_video(
    ident: Identifier,
    video: str,
    sponsors: list[Brand],
    match_id: str,
    fps: float,
    max_usd: float,
    out_dir: pathlib.Path,
    overlay: str = "",
    limit: int | None = None,
    workers: int = 1,
) -> dict:
    """Whole-frame identification of a video -> out_dir/{detections,decisions,pred}.json + summary.json."""
    from app.config import settings
    from app.contracts import Shot
    from workers.aggregate_logic import detections_to_intervals, union_seconds

    s = settings()
    dets: list[Detection] = []
    decs: list[DecisionLog] = []
    shots: dict[str, list[float]] = {}
    stopped = None
    exp = ident.expected_cost_usd()
    frames_dir = out_dir / "frames"   # sampled JPEGs, for scripts/replay_run.py (evidence)
    frames_dir.mkdir(parents=True, exist_ok=True)

    def flush(batch: list) -> None:
        for k, _t, _s, jpeg in batch:
            (frames_dir / f"{k}.jpg").write_bytes(jpeg)
        # one thread per frame of the batch; results are kept in frame order
        with ThreadPoolExecutor(max_workers=max(len(batch), 1)) as pool:
            res = list(pool.map(lambda b: ident.find(b[3], sponsors, context=f"{match_id}:{b[0]}"), batch))
        for (k, t, shot_no, _), (found, call) in zip(batch, res, strict=True):
            fid, sid = f"{match_id}:{k}", f"{match_id}:s{shot_no}"
            shots.setdefault(sid, [t, t])[1] = t
            dets.extend(found_to_detections(found, match_id, fid, sid, t, ident.reader, s.min_area_pct))
            decs.append(
                decision_for(match_id, fid, sid, t, ident.reader, call, found, max_usd - ident.spent_usd)
            )

    batch: list = []
    for k, t, shot_no, jpeg in sample_video(video, fps, overlay=overlay):
        if limit is not None and k >= limit:
            break
        if ident.spent_usd + exp * (len(batch) + 1) > max_usd:
            flush(batch)
            batch = []
            if ident.spent_usd + exp > max_usd:
                stopped = f"budget_cap at t={t}s (spent ${ident.spent_usd:.4f} of ${max_usd})"
                break
        batch.append((k, t, shot_no, jpeg))
        if len(batch) >= workers:
            flush(batch)
            batch = []
    flush(batch)
    step = 1.0 / fps
    shot_objs = [
        Shot(shot_id=sid, match_id=match_id, t_start=a, t_end=b + step, camera="main")
        for sid, (a, b) in shots.items()
    ]
    intervals = detections_to_intervals(dets, shot_objs, fps, s)
    out_dir.mkdir(parents=True, exist_ok=True)
    dump = lambda name, xs: (out_dir / name).write_text(  # noqa: E731
        json.dumps([x.model_dump(mode="json") for x in xs], indent=1), encoding="utf-8"
    )
    dump("detections.json", dets)
    dump("decisions.json", decs)
    dump("pred.json", intervals)
    minutes = round((max((d.t for d in decs), default=0.0) + step) / 60, 3)
    summary = {
        "video": str(video),
        "match_id": match_id,
        "provider": ident.provider.name,
        "model": ident.model,
        "fps": fps,
        "frames": len(decs),
        "minutes": minutes,
        "detections": len(dets),
        "intervals": len(intervals),
        "errors": sum(1 for c in ident.calls if c.error),
        "off_list_dropped": sum(c.off_list for c in ident.calls),
        "tokens_in": sum(c.tokens_in for c in ident.calls),
        "tokens_out": sum(c.tokens_out for c in ident.calls),
        "cost_usd": ident.spent_usd,
        "stopped": stopped,
        # seconds a brand was visible on any surface (union), counted only from credited detections
        "brand_seconds": {
            b: union_seconds((i.t_start, i.t_end) for i in intervals if i.brand_id == b)
            for b in sorted({i.brand_id for i in intervals})
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def check_frames(
    ident: Identifier, frames: Sequence[pathlib.Path], sponsors: list[Brand], match_id: str
) -> dict:
    """Acceptance check: whole-frame answers on N frames must validate as contracts `Detection`."""
    from app.config import settings

    ok = empty = invalid = errors = 0
    for p in frames:
        found, call = ident.find(p.read_bytes(), sponsors, context=p.name)
        if call.error:
            errors += 1
            continue
        try:
            d = found_to_detections(
                found,
                match_id,
                f"{match_id}:{p.stem}",
                f"{match_id}:s0",
                0.0,
                ident.reader,
                settings().min_area_pct,
            )
            [Detection.model_validate(x.model_dump()) for x in d]
            ok += 1
            empty += not d
        except Exception:  # noqa: BLE001
            invalid += 1
    return {
        "frames": len(frames),
        "valid": ok,
        "empty": empty,
        "invalid": invalid,
        "errors": errors,
        "off_list_dropped": sum(c.off_list for c in ident.calls),
        "cost_usd": ident.spent_usd,
        "tokens_in": sum(c.tokens_in for c in ident.calls),
        "tokens_out": sum(c.tokens_out for c in ident.calls),
    }


def write_draft(counts: dict[str, dict], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["brand_id", "name", "aliases", "frames_seen", "surfaces"])
        for name, c in sorted(counts.items(), key=lambda kv: -kv[1]["frames"]):
            w.writerow([slug(name), name, "", c["frames"], ";".join(sorted(c["surfaces"]))])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vision.identify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("cmd", choices=["run", "check", "discover"])
    p.add_argument("--video")
    p.add_argument("--frames", help="check: folder of JPEG frames (e.g. data/frames/<match_id>)")
    p.add_argument("--sponsors", help="sponsor CSV brand_id,name[,aliases]")
    p.add_argument("--match-id", default="m-club-5min")
    p.add_argument("--fps", type=float, default=1.0)
    p.add_argument("--n", type=int, default=20, help="check: frames sampled evenly from --frames")
    p.add_argument("--limit", type=int, help="run: stop after N frames")
    p.add_argument("--workers", type=int, default=8, help="run: parallel API calls")
    p.add_argument("--max-usd", type=float, default=1.0, help="hard spend cap for this command")
    p.add_argument("--overlay", default="", help="x,y,w,h[;...] normalised rects to mask (score bug)")
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--name", default="vision-run")
    p.add_argument("--out", help="output folder (default data/runs/<name>)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ident = Identifier(make_provider(a.provider, a.model))
    price_of(ident.model)  # fail fast without a price

    if a.cmd == "run":
        if not (a.video and a.sponsors):
            p.error("run needs --video and --sponsors")
        out = pathlib.Path(a.out or ROOT / "data" / "runs" / a.name)
        summary = run_video(
            ident,
            a.video,
            load_sponsors(a.sponsors),
            a.match_id,
            a.fps,
            a.max_usd,
            out,
            a.overlay,
            a.limit,
            a.workers,
        )
        print(json.dumps(summary, indent=1))
        print(
            f"\nNext: python -m eval.harness --pred {out / 'pred.json'} --decisions {out / 'decisions.json'} "
            f"--gold data/gold/reference_intervals.csv --minutes {summary['minutes']} --name {a.name} "
            f"--source RECORDED --sponsors {','.join(b.brand_id for b in load_sponsors(a.sponsors))}"
        )
        return 0
    if a.cmd == "check":
        if not (a.frames and a.sponsors):
            p.error("check needs --frames and --sponsors")
        files = sorted(pathlib.Path(a.frames).glob("*.jpg"), key=lambda q: (len(q.stem), q.stem))
        pick = [files[round(i * (len(files) - 1) / max(a.n - 1, 1))] for i in range(min(a.n, len(files)))]
        print(json.dumps(check_frames(ident, pick, load_sponsors(a.sponsors), a.match_id), indent=1))
        return 0
    if not a.video:
        p.error("discover needs --video")
    counts: dict[str, dict] = {}
    for _k, _t, _s, jpeg in sample_video(a.video, a.fps, overlay=a.overlay):
        if ident.spent_usd + ident.expected_cost_usd() > a.max_usd:
            break
        names, _ = ident.discover(jpeg)
        for name, st in {n.lower(): (n, st) for n, st in names}.values():
            c = counts.setdefault(name, {"frames": 0, "surfaces": set()})
            c["frames"] += 1
            c["surfaces"].add(st)
    out = ROOT / "data" / "sponsors.draft.csv"
    write_draft(counts, out)
    print(f"{len(counts)} names -> {out} (cost ${ident.spent_usd:.4f}); a human confirms it before use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
