"""scripts/replay_run.py: ids move to the replay match id (no services touched)."""

import json

from app.contracts import BBox, DecisionLog, Detection, Reader
from scripts.replay_run import load


def test_load_renames_ids(tmp_path):
    det = Detection(
        detection_id="m-run:3:v0",
        match_id="m-run",
        frame_id="m-run:3",
        shot_id="m-run:s0",
        t=3.0,
        bbox=BBox(x=0.1, y=0.1, w=0.2, h=0.05),
        surface_type="led_perimeter",
        brand_id="b-a",
        confidence=0.9,
        reader=Reader.gpt,
        area_pct=1.0,
        center_dist=0.3,
    )
    dec = DecisionLog(
        decision_id="m-run:3:vision",
        match_id="m-run",
        shot_id="m-run:s0",
        group_id="m-run:3",
        t=3.0,
        readers_tried=[Reader.gpt],
        chosen_reader=Reader.gpt,
        confidence=0.9,
        reason="x",
        budget_left_usd=1.0,
    )
    (tmp_path / "summary.json").write_text(json.dumps({"match_id": "m-run", "fps": 2, "model": "m"}))
    (tmp_path / "detections.json").write_text(json.dumps([det.model_dump(mode="json")]))
    (tmp_path / "decisions.json").write_text(json.dumps([dec.model_dump(mode="json")]))
    _, old, dets, decs = load(tmp_path, "m-ucl-demo")
    assert old == "m-run"
    assert dets[0].frame_id == "m-ucl-demo:3" and dets[0].match_id == "m-ucl-demo"
    assert decs[0].group_id == "m-ucl-demo:3" and decs[0].decision_id == "m-ucl-demo:3:vision"
