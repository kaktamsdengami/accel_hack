"""T08 vision identifier: fake SDK clients (no network, no keys). Fixture answers are invented."""

import io
import json
from types import SimpleNamespace as NS

import pytest

from app.contracts import BBox, Detection, Frame, Reader, Side, SurfaceBox, SurfaceType
from vision import identify as vi
from vision.readers import Brand, BrandReader, FrameGroup

SPONSORS = [Brand("b-alpha", "Alpha Air", ("AlphaAir",)), Brand("b-bravo", "Bravo Beer")]


class FakeOpenAI:
    """Mimics openai.OpenAI().chat.completions.create; answers pop from a list (dict or exception)."""

    def __init__(self, answers, tokens=(1200, 150)):
        self.answers, self.tokens, self.requests = list(answers), tokens, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        msg = NS(content=json.dumps(a), refusal=None)
        return NS(
            choices=[NS(message=msg, finish_reason="stop")],
            model=kw["model"],
            usage=NS(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1]),
        )


class FakeAnthropic:
    def __init__(self, answers, tokens=(1500, 120), stop="end_turn"):
        self.answers, self.tokens, self.stop, self.requests = list(answers), tokens, stop, []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        txt = json.dumps(self.answers.pop(0))
        return NS(
            content=[NS(type="text", text=txt)],
            stop_reason=self.stop,
            model=kw["model"],
            usage=NS(input_tokens=self.tokens[0], output_tokens=self.tokens[1]),
        )


def ident(client, provider="openai", model=None, tmp_path=None):
    p = vi.make_provider(provider, model, client)
    return vi.Identifier(p, cost_log=(tmp_path / "calls.jsonl") if tmp_path else None)


def jpeg(w=320, h=180):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


def logo(bid, conf=0.9, st="led_perimeter", side="far", bbox=(0.1, 0.05, 0.5, 0.05)):
    return {
        "brand_id": bid,
        "confidence": conf,
        "surface_type": st,
        "side": side,
        "bbox": dict(zip("xywh", bbox, strict=True)),
    }


def test_prompts_have_all_sections():
    p = vi.load_prompts()
    assert {"system", "read", "find", "discover"} <= set(p)
    assert "{sponsors}" in p["read"] and "{sponsors}" in p["find"]


def test_schema_enum_only_allows_listed_brands():
    s = vi.schema_find(["b-alpha", "b-bravo"])
    brand = s["properties"]["logos"]["items"]["properties"]["brand_id"]
    assert brand["enum"] == ["b-alpha", "b-bravo", None]
    assert vi.schema_find([])["properties"]["logos"]["items"]["properties"]["brand_id"] == {"type": "null"}


def test_find_validates_against_contracts_and_logs_cost(tmp_path):
    fake = FakeOpenAI(
        [{"logos": [logo("b-alpha"), logo("b-bravo", st="shirt", side="unknown", bbox=(0.4, 0.4, 0.1, 0.2))]}]
    )
    idf = ident(fake, tmp_path=tmp_path)
    found, call = idf.find(jpeg(), SPONSORS, context="m:1")
    dets = vi.found_to_detections(found, "m", "m:1", "m:s0", 0.5, idf.reader, 0.1)
    assert [d.brand_id for d in dets] == ["b-alpha", "b-bravo"]
    for d in dets:
        Detection.model_validate(d.model_dump())
        assert d.reader == Reader.gpt
    # gpt-5-mini: 1200 * 0.25 + 150 * 2.00 per 1M tokens
    assert call.cost_usd == pytest.approx((1200 * 0.25 + 150 * 2.0) / 1e6)
    row = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[0])
    assert row["tokens_in"] == 1200 and row["tokens_out"] == 150 and row["context"] == "m:1"
    req = fake.requests[0]
    assert req["response_format"]["json_schema"]["strict"] is True
    assert req["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_frame_without_sponsor_returns_empty_not_a_guess():
    idf = ident(FakeOpenAI([{"logos": []}]))
    found, call = idf.find(jpeg(), SPONSORS)
    assert found == [] and call.error is None
    d = vi.decision_for("m", "m:1", "m:s0", 0.0, idf.reader, call, found, 1.0)
    assert "no sponsor" in d.reason


def test_off_list_and_null_answers_are_dropped():
    answer = {"logos": [logo("b-zeta"), logo(None), logo("b-alpha", bbox=(0.9, 0.9, 0.5, 0.5))]}
    idf = ident(FakeOpenAI([answer]))
    found, call = idf.find(jpeg(), SPONSORS)
    assert [f.brand_id for f in found] == ["b-alpha"]
    assert call.off_list == 1
    b = found[0].bbox
    assert b.x + b.w <= 1 and b.y + b.h <= 1


def test_error_is_reported_not_raised():
    idf = ident(FakeOpenAI([TimeoutError("read timed out")]))
    found, call = idf.find(jpeg(), SPONSORS)
    assert found == [] and "TimeoutError" in call.error and call.cost_usd == 0
    d = vi.decision_for("m", "m:1", "m:s0", 0.0, idf.reader, call, found, 1.0)
    assert d.reason.startswith("reader_error") and d.confidence == 0


def test_unknown_model_price_is_refused(monkeypatch):
    monkeypatch.delenv("VISION_PRICE_IN_PER_MTOK", raising=False)
    with pytest.raises(ValueError):
        vi.price_of("some-new-model")
    assert vi.price_of("gpt-5-mini-2025-08-07") == vi.PRICES["gpt-5-mini"]
    monkeypatch.setenv("VISION_PRICE_IN_PER_MTOK", "1")
    monkeypatch.setenv("VISION_PRICE_OUT_PER_MTOK", "2")
    assert vi.price_of("some-new-model") == (1.0, 2.0)


def _group():
    f = Frame(frame_id="m:7", match_id="m", shot_id="m:s0", group_id="g7", t=3.5, width=320, height=180)
    return FrameGroup("m", "g7", "m:s0", [f])


def _box(bid, st=SurfaceType.led_perimeter):
    return SurfaceBox(
        box_id=bid,
        frame_id="m:7",
        bbox=BBox(x=0.1, y=0.05, w=0.5, h=0.04),
        surface_type=st,
        side=Side.far,
        score=0.9,
    )


def test_vision_reader_follows_router_protocol():
    fake = FakeAnthropic(
        [
            {
                "boxes": [
                    {
                        "box_id": "x1",
                        "brand_id": "b-alpha",
                        "confidence": 0.9,
                        "runner_up": "b-bravo",
                        "runner_up_confidence": 0.2,
                        "readable": True,
                    },
                    {
                        "box_id": "x2",
                        "brand_id": "b-zeta",
                        "confidence": 0.8,
                        "runner_up": None,
                        "runner_up_confidence": 0,
                        "readable": True,
                    },
                ]
            }
        ]
    )
    r = vi.VisionReader(ident(fake, "anthropic"), lambda m, f: jpeg(1280, 720))
    assert isinstance(r, BrandReader) and r.reader == Reader.claude and r.expected_cost_usd > 0
    res = r.read(_group(), [_box("x1"), _box("x2"), _box("x3")], SPONSORS)
    by = res.by_box()
    assert by["x1"].brand_id == "b-alpha" and by["x1"].margin == pytest.approx(0.7)
    assert by["x2"].brand_id is None  # off list
    assert by["x3"].brand_id is None and by["x3"].unsure  # model skipped it
    assert res.tokens_in == 1500 and res.cost_usd == pytest.approx((1500 * 4 + 120 * 20) / 1e6)
    req = fake.requests[0]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert sum(1 for c in req["messages"][0]["content"] if c["type"] == "image") == 3


def test_vision_reader_marks_error_on_failure():
    r = vi.VisionReader(ident(FakeOpenAI([RuntimeError("boom")])), lambda m, f: jpeg())
    res = r.read(_group(), [_box("x1")], SPONSORS)
    assert res.error and res.by_box()["x1"].unsure


def test_crop_upscales_thin_led_strip():
    from PIL import Image

    c = Image.open(io.BytesIO(vi.crop_jpeg(jpeg(1280, 720), BBox(x=0.1, y=0.05, w=0.5, h=0.03))))
    assert c.height >= 96 and c.width > c.height


def test_run_video_writes_harness_inputs(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    video = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 90))
    for i in range(40):
        w.write(np.full((90, 160, 3), 40 if i < 20 else 200, np.uint8))
    w.release()
    answers = [{"logos": [logo("b-alpha")]}] * 3 + [{"logos": []}]
    idf = ident(FakeOpenAI(answers))
    s = vi.run_video(idf, str(video), SPONSORS, "m-t", fps=1, max_usd=1.0, out_dir=tmp_path / "out")
    assert s["frames"] == 4 and s["intervals"] >= 1 and s["cost_usd"] > 0
    assert s["brand_seconds"]["b-alpha"] == pytest.approx(3.0)
    pred = json.loads((tmp_path / "out" / "pred.json").read_text())
    assert pred[0]["brand_id"] == "b-alpha" and pred[0]["t_start"] == 0
    assert len(json.loads((tmp_path / "out" / "decisions.json").read_text())) == 4


def test_run_video_stops_at_budget(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    video = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 90))
    for _ in range(50):
        w.write(np.zeros((90, 160, 3), np.uint8))
    w.release()
    idf = ident(FakeOpenAI([{"logos": []}] * 5))
    s = vi.run_video(
        idf,
        str(video),
        SPONSORS,
        "m-t",
        fps=1,
        max_usd=idf.expected_cost_usd() * 1.2,
        out_dir=tmp_path / "out",
    )
    assert s["stopped"].startswith("budget_cap") and s["frames"] < 5


def test_sponsors_csv_and_draft(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("brand_id,name,aliases\nb-alpha,Alpha Air,AlphaAir; AA\n,skip,\n", encoding="utf-8")
    assert vi.load_sponsors(p) == [Brand("b-alpha", "Alpha Air", ("AlphaAir", "AA"))]
    out = tmp_path / "draft.csv"
    vi.write_draft({"Alpha Air": {"frames": 3, "surfaces": {"shirt"}}}, out)
    assert "b-alpha-air,Alpha Air,,3,shirt" in out.read_text()


def test_wrong_key_stops_instead_of_looping():
    class AuthenticationError(Exception):
        pass

    idf = ident(FakeOpenAI([AuthenticationError("401 Incorrect API key")]))
    with pytest.raises(AuthenticationError):
        idf.find(jpeg(), SPONSORS)
