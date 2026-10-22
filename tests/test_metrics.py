"""Hand-checkable fixtures for eval/metrics.py. Synthetic data lives only here, never in eval/runs/."""
from __future__ import annotations

import json

import pytest

from eval import harness
from eval.gold import load_gold
from eval.metrics import (
    Pred,
    capture_pct,
    claude_call_share,
    cost_per_match_minute,
    false_credit_pct,
    latency_stats,
    mae_per_brand,
    mae_per_surface,
    overlap_length,
    percentile,
    union_length,
)

LED = "LED perimeter · far"

GOLD_INTERVALS = (
    "brand_id,surface_label,t_start,t_end\n"
    f"b-a,{LED},0,10\n"
    f"b-a,{LED},20,30\n"
    "b-b,,50,60\n"
)
GOLD_TOTALS = f"brand_id,surface_label,seconds\nb-a,{LED},20\nb-b,,10\n"


@pytest.fixture
def gold(tmp_path):
    f = tmp_path / "gold.csv"
    f.write_text(GOLD_INTERVALS, encoding="utf-8")
    return load_gold(f)


@pytest.fixture
def gold_totals(tmp_path):
    f = tmp_path / "gold_totals.csv"
    f.write_text(GOLD_TOTALS, encoding="utf-8")
    return load_gold(f)


def P(brand, s, e, label=LED):
    return Pred(brand, label, s, e)


def test_union_and_overlap():
    assert union_length([(0, 10), (5, 15), (20, 22)]) == 17  # overlap counted once
    assert overlap_length([(0, 10)], [(5, 15)]) == 5
    assert overlap_length([(0, 10)], [(20, 30)]) == 0


def test_capture_partial_and_total_overlap(gold):
    # [5,15] overlaps gold [0,10] by 5 (partial); [20,30] equals gold (total) -> 15 of 30 gold s
    pred = [P("b-a", 5, 15), P("b-a", 20, 30)]
    cap = capture_pct(pred, gold)
    assert cap["variant"] == "intervals"
    assert cap["value"] == pytest.approx(50.0)
    assert capture_pct([P("b-a", 0, 10), P("b-a", 20, 30), P("b-b", 50, 60)], gold)["value"] == 100.0


def test_pred_overlapping_intervals_counted_once(gold):
    pred = [P("b-a", 0, 10), P("b-a", 0, 10), P("b-a", 5, 10, "Board · near")]
    assert capture_pct(pred, gold)["value"] == pytest.approx(100 * 10 / 30)
    assert mae_per_brand(pred, gold)["per_brand"]["b-a"] == 10  # union 10 s vs gold 20 s


def test_false_credit_no_gold_for_brand(gold):
    # b-c has no gold: its 10 s are all false; b-a: 20 s pred, 15 overlap -> 5 false. 15 of 30.
    pred = [P("b-a", 5, 15), P("b-a", 20, 30), P("b-c", 0, 10)]
    fc = false_credit_pct(pred, gold)
    assert fc["value"] == pytest.approx(50.0)
    assert false_credit_pct([], gold)["value"] is None


def test_mae_missing_brand_is_zero_seconds(gold):
    pred = [P("b-a", 5, 15), P("b-a", 20, 30)]  # a: 20 s (gold 20), b missing (gold 10)
    mae = mae_per_brand(pred, gold)
    assert mae["value"] == pytest.approx(5.0)
    assert mae["per_brand"] == {"b-a": 0, "b-b": 10}
    # sponsor list can include a brand absent from gold: pred 10 vs gold 0
    mae3 = mae_per_brand([P("b-c", 0, 10)], gold, sponsors=["b-a", "b-b", "b-c"])
    assert mae3["value"] == pytest.approx(40 / 3)


def test_per_surface_mae(gold):
    pred = [P("b-a", 0, 15), P("b-a", 0, 4, "Board · near")]  # right brand, 15 s on LED far (gold 20)
    res = mae_per_surface(pred, gold)
    # gold pair (led far, b-a) = 20 vs 15 -> 5; unlabelled b-b is no pair; board label unused by gold
    assert res["per_pair"] == {f"{LED.lower()} | b-a": 5}
    assert res["value"] == 5


def test_totals_fallback(gold_totals):
    assert not gold_totals.has_intervals
    pred = [P("b-a", 0, 30), P("b-b", 0, 4)]  # a: min(30,20)=20 ; b: min(4,10)=4 -> 24/30
    cap = capture_pct(pred, gold_totals)
    assert cap["variant"] == "totals"
    assert cap["value"] == pytest.approx(80.0)
    assert false_credit_pct(pred, gold_totals)["value"] is None


def test_totals_without_surface_labels_has_no_per_surface(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("brand_id,seconds\nb-a,5\n", encoding="utf-8")
    g = load_gold(f)
    assert mae_per_surface([], g)["value"] is None
    assert mae_per_brand([], g)["value"] == 5


def test_cost_and_claude_share():
    dec = [
        {"group_id": "g1", "chosen_reader": "memory", "cost_usd": 0.0},
        {"group_id": "g2", "chosen_reader": "claude", "cost_usd": 0.03},
        {"group_id": "g3", "chosen_reader": "claude", "cost_usd": 0.01},
        {"group_id": "g4", "chosen_reader": "ocr", "cost_usd": 0.0},
    ]
    c = cost_per_match_minute(dec, minutes=2.0, extra_usd=0.02)
    assert c["value"] == pytest.approx(0.03)  # (0.04 + 0.02) / 2
    s = claude_call_share(dec)
    assert s["value"] == 0.5 and s["groups"] == 4
    assert claude_call_share([])["value"] is None


def test_percentile_and_latency():
    xs = list(range(1, 21))  # 1..20
    assert percentile(xs, 50) == pytest.approx(10.5)
    assert percentile(xs, 95) == pytest.approx(19.05)
    pairs = [(0, v) for v in xs]
    lat = latency_stats(pairs)
    assert lat["p95_s"] == pytest.approx(19.05) and lat["n"] == 20
    iso = latency_stats([("2026-10-22T10:00:00Z", "2026-10-22T10:00:02.5Z")])
    assert iso["p50_s"] == pytest.approx(2.5)
    assert percentile([], 95) is None


def test_gold_rejects_bad_files(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("brand_id,foo\nb-a,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_gold(f)


def test_harness_cli_end_to_end(tmp_path, capsys):
    (tmp_path / "gold.csv").write_text(GOLD_INTERVALS, encoding="utf-8")
    pred = [
        {"brand_id": "b-a", "surface_type": "led_perimeter", "side": "far", "t_start": 5, "t_end": 15},
        {"brand_id": "b-a", "surface_type": "led_perimeter", "side": "far", "t_start": 20, "t_end": 30},
        {"brand_id": "b-c", "surface_type": "shirt", "side": "unknown", "t_start": 0, "t_end": 10},
    ]
    dec = [{"group_id": "g1", "chosen_reader": "claude", "cost_usd": 0.06},
           {"group_id": "g2", "chosen_reader": "memory", "cost_usd": 0.0}]
    (tmp_path / "pred.json").write_text(json.dumps(pred), encoding="utf-8")
    (tmp_path / "dec.json").write_text(json.dumps(dec), encoding="utf-8")
    (tmp_path / "lat.json").write_text(json.dumps([[0, 1], [0, 3]]), encoding="utf-8")
    out = tmp_path / "runs"
    argv = ["--pred", str(tmp_path / "pred.json"), "--gold", str(tmp_path / "gold.csv"),
            "--decisions", str(tmp_path / "dec.json"), "--minutes", "3", "--name", "t",
            "--baseline", "router-memory", "--latency", str(tmp_path / "lat.json"),
            "--out-dir", str(out)]
    assert harness.main(argv) == 0
    (rec_path,) = out.glob("*-t.json")
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    assert rec["source"] == "RECORDED" and rec["baseline"] == "router-memory"
    assert rec["metrics"]["capture_pct"]["value"] == pytest.approx(50.0)
    assert rec["metrics"]["cost_per_match_minute"]["value"] == pytest.approx(0.02)
    assert rec["inputs"]["gold"]["sha256"]
    assert harness.main(["--reproduce", str(rec_path)]) == 0
    assert harness.main(["--compare", str(rec_path), str(rec_path)]) == 0
    assert "capture%" in capsys.readouterr().out
