"""Shared contracts v1. Spec and rules: docs/CONTRACTS.md. Sample payloads: docs/api-mock.json."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field

CONTRACTS_VERSION = "1.0"


class Model(BaseModel):
    # Consumers ignore unknown fields so producers can add fields without breaking them.
    model_config = ConfigDict(extra="ignore", use_enum_values=True)


# --- enums -------------------------------------------------------------------

class Source(str, Enum):
    LIVE = "LIVE"
    RECORDED = "RECORDED"


class Camera(str, Enum):
    main = "main"
    close = "close"
    other = "other"
    replay = "replay"


class SurfaceType(str, Enum):
    led_perimeter = "led_perimeter"
    static_board = "static_board"
    shirt = "shirt"
    banner = "banner"
    backdrop = "backdrop"
    other = "other"


class Side(str, Enum):
    near = "near"
    far = "far"
    left = "left"
    right = "right"
    unknown = "unknown"


class Reader(str, Enum):
    memory = "memory"
    claude = "claude"
    gpt = "gpt"
    ocr = "ocr"
    own_detector = "own_detector"


class CoverageStatus(str, Enum):
    measured = "measured"
    unmeasured = "unmeasured"


class UnmeasuredReason(str, Enum):
    no_frame = "no_frame"
    budget_cap = "budget_cap"
    low_confidence = "low_confidence"
    replay_excluded = "replay_excluded"
    reader_error = "reader_error"


class PaceStatus(str, Enum):
    ahead = "ahead"
    on_track = "on_track"
    behind = "behind"
    no_target = "no_target"


class AlertKind(str, Enum):
    under_pace = "under_pace"
    budget = "budget"
    coverage_gap = "coverage_gap"
    conflict = "conflict"


Unit = Field(ge=0, le=1)


class BBox(Model):
    x: float = Unit
    y: float = Unit
    w: float = Unit
    h: float = Unit


# --- pipeline records ----------------------------------------------------------

class Match(Model):
    match_id: str
    club_id: str
    stadium_id: str
    title: str
    source: Source
    duration_s: float | None = None
    sampled_fps: float = 2.0
    started_at: datetime


class Shot(Model):
    shot_id: str
    match_id: str
    t_start: float
    t_end: float
    camera: Camera
    is_replay: bool = False


class Frame(Model):
    frame_id: str
    match_id: str
    shot_id: str
    group_id: str
    t: float
    width: int
    height: int
    is_representative: bool = True


class SurfaceBox(Model):
    box_id: str
    frame_id: str
    bbox: BBox
    surface_type: SurfaceType
    side: Side = Side.unknown
    board_id: str | None = None
    score: float = Unit


class Detection(Model):
    detection_id: str
    match_id: str
    frame_id: str
    shot_id: str
    t: float
    bbox: BBox
    surface_type: SurfaceType
    side: Side = Side.unknown
    board_id: str | None = None
    brand_id: str | None = None  # None = an ad surface whose brand is unknown
    confidence: float = Unit
    reader: Reader
    area_pct: float = Field(ge=0, le=100)
    center_dist: float = Unit
    sharpness: float = 0.0
    occluded: bool = False
    below_threshold: bool = False


class DecisionLog(Model):
    decision_id: str
    match_id: str
    shot_id: str
    group_id: str
    t: float
    readers_tried: list[Reader]
    chosen_reader: Reader
    confidence: float = Unit
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    reason: str
    budget_left_usd: float


class Interval(Model):
    match_id: str
    brand_id: str
    surface_type: SurfaceType
    side: Side = Side.unknown
    board_id: str | None = None
    t_start: float
    t_end: float
    duration_s: float
    avg_area_pct: float
    max_area_pct: float
    avg_center_dist: float = Unit
    is_closeup: bool = False
    is_replay: bool = False


class CoverageSecond(Model):
    match_id: str
    second: int
    status: CoverageStatus
    reason: UnmeasuredReason | None = None


# --- dashboard and report ------------------------------------------------------

class BrandStats(Model):
    brand_id: str
    brand_name: str
    seconds: float
    pct_of_match: float
    closeup_seconds: float = 0.0
    replay_seconds: float = 0.0
    avg_area_pct: float = 0.0
    surfaces: int = 0
    target_seconds: float | None = None
    projected_seconds: float | None = None
    pace: PaceStatus = PaceStatus.no_target


class SurfaceStats(Model):
    surface_label: str
    surface_type: SurfaceType
    side: Side = Side.unknown
    board_id: str | None = None
    brand_id: str
    brand_name: str
    seconds: float
    pct_of_match: float
    avg_area_pct: float = 0.0
    closeup_seconds: float = 0.0


class LedSlot(Model):
    surface_label: str
    brand_id: str
    brand_name: str
    t_start: float
    t_end: float


class CoverageSummary(Model):
    measured_pct: float
    measured_seconds: float
    unmeasured_seconds: float
    by_reason: dict[UnmeasuredReason, float] = {}


class CostSummary(Model):
    usd_total: float
    usd_per_match_minute: float
    claude_call_share: float = Unit
    calls_by_reader: dict[Reader, int] = {}
    budget_usd: float
    budget_left_usd: float


class Alert(Model):
    alert_id: str
    match_id: str
    t: float
    kind: AlertKind
    message: str
    brand_id: str | None = None


class MatchReport(Model):
    match: Match
    clock_t: float
    brands: list[BrandStats]
    surfaces: list[SurfaceStats]
    led_timeline: list[LedSlot] = []
    coverage: CoverageSummary
    cost: CostSummary
    alerts: list[Alert] = []


class StatsUpdate(Model):
    event: Literal["stats"] = "stats"
    data: MatchReport


class DecisionUpdate(Model):
    event: Literal["decision"] = "decision"
    data: DecisionLog


class AlertUpdate(Model):
    event: Literal["alert"] = "alert"
    data: Alert


LiveUpdate = Union[StatsUpdate, DecisionUpdate, AlertUpdate]


class BrandDetail(Model):
    brand: BrandStats
    surfaces: list[SurfaceStats]
    intervals: list[Interval]


class ChatRequest(Model):
    question: str = Field(min_length=1, max_length=1000)


class Citation(Model):
    brand_id: str | None = None
    surface_label: str | None = None
    t_start: float
    t_end: float
    frame_id: str | None = None


class ChatAnswer(Model):
    answer: str
    citations: list[Citation] = []
    sql: list[str] = []
    not_measured_note: str | None = None


# Kafka topic -> model (docs/CONTRACTS.md section 8)
TOPICS: dict[str, type[Model]] = {"frames": Frame, "detections": Detection, "decisions": DecisionLog}
