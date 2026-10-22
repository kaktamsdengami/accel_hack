"""End to end on the real Aiven services (python -m pytest -q -m live): SYNTHETIC stream -> Kafka ->
aggregate worker -> Valkey snapshot + ClickHouse. Uses a unique match id and consumer group per run."""
import threading
import time
import uuid

import pytest

from app import stores
from app.contracts import MatchReport
from scripts import fake_stream
from workers import aggregate

pytestmark = pytest.mark.live

STREAM_S = 10


def test_synthetic_match_end_to_end():
    match_id = f"m-synthetic-{uuid.uuid4().hex[:6]}"
    r = stores.valkey()
    ch = stores.clickhouse()

    pubsub = r.pubsub()
    pubsub.subscribe(f"sp:live:{match_id}")

    stop = threading.Event()
    worker = threading.Thread(
        target=aggregate.run,
        kwargs=dict(stop=stop, group=f"aggregate-test-{uuid.uuid4().hex[:6]}", match_filter={match_id}),
        daemon=True)
    worker.start()
    time.sleep(8)  # let the consumer join the group before the first message
    counts = fake_stream.run(match_id, duration_s=STREAM_S, match_duration_s=60.0)
    time.sleep(4)  # a few ticks to drain and publish
    stop.set()
    worker.join(timeout=30)
    assert not worker.is_alive()

    assert counts["frames"] == STREAM_S * 2 and counts["detections"] > 0 and counts["decisions"] > 0

    snapshot = MatchReport.model_validate_json(r.get(f"sp:{match_id}:stats"))
    assert snapshot.match.match_id == match_id and snapshot.match.source == "RECORDED"
    assert "SYNTHETIC" in snapshot.match.title
    assert snapshot.clock_t >= STREAM_S - 1
    assert {b.brand_id for b in snapshot.brands} >= {"b-synth-alpha", "b-synth-beta"}
    assert all(b.brand_name.startswith("SYNTHETIC") for b in snapshot.brands)  # names came from Postgres
    assert any(s.surface_label == "LED perimeter · far" for s in snapshot.surfaces)
    assert snapshot.coverage.measured_seconds > 0
    assert snapshot.cost.budget_usd > 0

    n_int = ch.query("SELECT count() FROM intervals FINAL WHERE match_id = {m:String}",
                     parameters={"m": match_id}).result_rows[0][0]
    n_det = ch.query("SELECT count() FROM detections FINAL WHERE match_id = {m:String}",
                     parameters={"m": match_id}).result_rows[0][0]
    n_cov = ch.query("SELECT count() FROM coverage FINAL WHERE match_id = {m:String}",
                     parameters={"m": match_id}).result_rows[0][0]
    n_dec = ch.query("SELECT count() FROM decisions FINAL WHERE match_id = {m:String}",
                     parameters={"m": match_id}).result_rows[0][0]
    assert n_int > 0 and n_det == counts["detections"] and n_dec == counts["decisions"]
    assert n_cov == int(snapshot.coverage.measured_seconds + snapshot.coverage.unmeasured_seconds)

    events = []
    while (msg := pubsub.get_message(timeout=0.2)) is not None:
        if msg["type"] == "message":
            events.append(msg["data"])
    assert any(MatchReport.model_validate_json(e).match.match_id == match_id for e in events)
    assert r.sismember("sp:matches", match_id)
