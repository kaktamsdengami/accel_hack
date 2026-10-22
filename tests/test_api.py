"""API tests. Mock mode needs no services (CI); the live smoke test needs Aiven (run with -m live)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.contracts import (
    BrandDetail,
    ChatAnswer,
    CoverageSecond,
    DecisionLog,
    Match,
    MatchReport,
)
from app.main import app

M = "m-demo-1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_MOCK", "1")
    monkeypatch.setenv("API_MOCK_TICK_S", "0.01")
    return TestClient(app)


def test_matches(client):
    r = client.get("/matches")
    assert r.status_code == 200
    assert [Match.model_validate(m).match_id for m in r.json()] == [M]


def test_report(client):
    r = client.get(f"/matches/{M}/report")
    assert r.status_code == 200
    rep = MatchReport.model_validate(r.json())
    assert rep.match.match_id == M and rep.brands


def test_brand_detail(client):
    r = client.get(f"/matches/{M}/brands/b-bravo")
    assert r.status_code == 200
    d = BrandDetail.model_validate(r.json())
    assert d.brand.brand_id == "b-bravo"
    assert all(s.brand_id == "b-bravo" for s in d.surfaces)
    assert d.intervals


def test_decisions_and_limit(client):
    r = client.get(f"/matches/{M}/decisions?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2
    [DecisionLog.model_validate(d) for d in r.json()]
    assert client.get(f"/matches/{M}/decisions?limit=0").status_code == 422


def test_coverage(client):
    r = client.get(f"/matches/{M}/coverage")
    assert r.status_code == 200
    assert r.json() and all(CoverageSecond.model_validate(c) for c in r.json())


def test_evidence(client):
    r = client.get(f"/evidence/{M}:000204.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    assert client.get("/evidence/nope.jpg").status_code == 404


def test_chat_stub(client):
    r = client.post(f"/matches/{M}/chat", json={"question": "How long was Bravo visible?"})
    assert r.status_code == 200
    assert "T17" in ChatAnswer.model_validate(r.json()).answer
    assert client.post(f"/matches/{M}/chat", json={"question": ""}).status_code == 422


def test_replay_stub(client):
    r = client.post(f"/matches/{M}/replay")
    assert r.status_code == 202
    assert r.json()["started"] is False


@pytest.mark.parametrize(
    "path", ["/report", "/brands/b-bravo", "/decisions", "/coverage", "/stream"]
)
def test_unknown_match_404(client, path):
    r = client.get(f"/matches/nope{path}")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_unknown_brand_404(client):
    assert client.get(f"/matches/{M}/brands/b-zzz").status_code == 404


def test_stream_delivers_events(client):
    events = []
    with client.stream("GET", f"/matches/{M}/stream?max_events=5") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        name = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
    kinds = [k for k, _ in events]
    assert kinds.count("stats") >= 2 and "decision" in kinds
    stats = [MatchReport.model_validate(d) for k, d in events if k == "stats"]
    assert stats[1].clock_t > stats[0].clock_t
    [DecisionLog.model_validate(d) for k, d in events if k == "decision"]


def test_cors_header(client):
    r = client.get("/matches", headers={"Origin": "https://preview.lovable.app"})
    assert r.headers["access-control-allow-origin"] in ("*", "https://preview.lovable.app")


@pytest.mark.live
def test_live_smoke(monkeypatch):
    """Real Valkey/ClickHouse/Postgres; tolerates empty data."""
    monkeypatch.delenv("API_MOCK", raising=False)
    c = TestClient(app)
    r = c.get("/matches")
    assert r.status_code == 200
    [Match.model_validate(m) for m in r.json()]
    assert c.get("/matches/__none__/report").status_code == 404
    assert c.get("/matches/__none__/decisions").json() == []
    assert c.get("/matches/__none__/coverage").json() == []
    assert c.get("/evidence/__none__.jpg").status_code == 404
    for m in r.json()[:1]:
        rep = c.get(f"/matches/{m['match_id']}/report")
        assert rep.status_code in (200, 404)
        if rep.status_code == 200:
            MatchReport.model_validate(rep.json())
