"""The Lovable mock must validate against the contracts, so the UI and the backend agree."""
import json
import pathlib

from pydantic import TypeAdapter

from app import contracts as c

MOCK = json.loads((pathlib.Path(__file__).parents[1] / "docs" / "api-mock.json").read_text(encoding="utf-8"))


def test_report_and_lists_validate():
    c.MatchReport.model_validate(MOCK["GET /matches/m-demo-1/report"])
    TypeAdapter(list[c.Match]).validate_python(MOCK["GET /matches"])
    TypeAdapter(list[c.DecisionLog]).validate_python(MOCK["GET /matches/m-demo-1/decisions?limit=3"])
    TypeAdapter(list[c.CoverageSecond]).validate_python(MOCK["GET /matches/m-demo-1/coverage"])
    c.BrandDetail.model_validate(MOCK["GET /matches/m-demo-1/brands/b-bravo"])


def test_sse_and_chat_validate():
    adapter = TypeAdapter(c.LiveUpdate)
    sse = MOCK["SSE /matches/m-demo-1/stream (examples of each event)"]
    events = [adapter.validate_python(e) for e in sse]
    assert {e.event for e in events} == {"decision", "alert"}
    chat = MOCK["POST /matches/m-demo-1/chat"]
    c.ChatRequest.model_validate(chat["request"])
    c.ChatAnswer.model_validate(chat["response"])


def test_bbox_rejects_out_of_range():
    import pytest
    with pytest.raises(ValueError):
        c.BBox(x=1.2, y=0, w=0.1, h=0.1)
