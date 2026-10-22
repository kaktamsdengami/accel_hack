"""T06: preprocessing and ingest on a GENERATED video (scripts/make_test_video.py). Skipped without OpenCV."""
import os
import time
import uuid

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")

import cv2  # noqa: E402

from scripts import make_test_video as mtv  # noqa: E402
from vision import preprocess as pp  # noqa: E402

SECONDS = 20.0


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("vid") / "match.mp4"
    truth = mtv.make_video(path, SECONDS)
    return path, truth


def _read(path, step_s=0.0):
    """(times, frames) of the video; every frame if step_s == 0."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    times, frames, n = [], [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if step_s == 0 or n % round(step_s * fps) == 0:
            times.append(n / fps)
            frames.append(f)
        n += 1
    return times, frames


def test_cut_detection_finds_planted_cuts(video):
    path, truth = video
    times, frames = _read(path)
    cuts = pp.detect_cuts([pp.frame_hist(f) for f in frames], times)
    found = [times[i] for i in cuts]
    assert len(found) == len(truth["cuts_s"])
    for f, planted in zip(found, truth["cuts_s"], strict=True):
        assert abs(f - planted) <= 1 / truth["fps"] + 1e-6


def test_no_cut_inside_a_shot(video):
    path, truth = video
    times, frames = _read(path)
    hists = [pp.frame_hist(f) for f in frames]
    planted = {round(c * truth["fps"]) for c in truth["cuts_s"]}
    for i in range(1, len(hists)):
        if i not in planted:
            assert pp.hist_distance(hists[i - 1], hists[i]) < pp.CUT_THRESHOLD


def test_overlay_mask_covers_score_bug(video):
    path, truth = video
    # auto-detection: one frame per shot, static regions persisting across shots
    _, frames = _read(path, 1.0)
    learner = pp.StaticRegionLearner()
    times, all_frames = _read(path)
    hists = [pp.frame_hist(f) for f in all_frames]
    shot = 0
    cuts = set(pp.detect_cuts(hists, times))
    for i, f in enumerate(all_frames):
        shot += i in cuts
        if i % 25 == 0:
            learner.add(f, shot)
    h, w = frames[0].shape[:2]
    mask = learner.mask((h, w))
    assert pp.mask_coverage(mask, truth["score_bug"]) > 0.9
    assert pp.mask_coverage(mask, truth["watermark"]) > 0.6
    assert mask.mean() / 255 < 0.15  # not masking the whole picture
    # applying the mask flattens the score bug
    out = pp.apply_mask(frames[0], mask)
    x, y, bw, bh = truth["score_bug"]
    patch = out[int(y * h) + 4:int((y + bh) * h) - 4, int(x * w) + 4:int((x + bw) * w) - 4]
    assert patch.std() < 1.0


def test_configured_rects_mask():
    m = pp.rects_to_mask((100, 200), pp.parse_rects("0.1,0.2,0.3,0.1; 0.8,0.8,0.2,0.2"))
    assert pp.mask_coverage(m, (0.1, 0.2, 0.3, 0.1)) == 1.0
    assert pp.mask_coverage(m, (0.8, 0.8, 0.2, 0.2)) == 1.0
    assert pp.mask_coverage(m, (0.5, 0.5, 0.1, 0.1)) == 0.0


def test_dedup_groups_the_frozen_stretch(video):
    path, truth = video
    times, frames = _read(path, 0.5)  # 2 fps like SAMPLED_FPS
    hashes = [pp.phash(f) for f in frames]
    hists = [pp.frame_hist(f) for f in frames]
    cuts = set(pp.detect_cuts(hists, times))
    shots, s = [], 0
    for i in range(len(frames)):
        s += i in cuts
        shots.append(s)
    groups = pp.group_near_duplicates(hashes, shots)
    f0, f1 = truth["freeze_s"]
    frozen = [g for t, g in zip(times, groups, strict=True) if f0 + 0.5 <= t < f1]
    assert len(frozen) >= 3 and len(set(frozen)) == 1
    assert len(set(groups)) < len(groups)  # something was deduplicated
    # frames outside the frozen stretch are mostly distinct
    others = [g for t, g in zip(times, groups, strict=True) if not f0 <= t < f1]
    assert len(set(others)) >= len(others) * 0.7


def test_replay_hook_is_false(video):
    _, frames = _read(video[0], 5.0)
    assert pp.is_replay(frames[0]) is False


def test_ingest_dry_run_timestamps(video, tmp_path):
    from workers.ingest import DrySinks, ingest

    path, truth = video
    sinks = DrySinks()
    st = ingest(path, "m-t06test", speed=0, sinks=sinks, frames_dir=tmp_path)
    assert st.frames == len(sinks.frames) == int(SECONDS * 2)
    assert all(abs(f.t - i * 0.5) < 0.05 for i, f in enumerate(sinks.frames))
    assert st.shots == len(truth["cuts_s"]) + 1
    assert st.groups < st.frames and 0 < st.dedup_ratio < 1
    reps = [f for f in sinks.frames if f.is_representative]
    assert len(reps) == st.groups == len(sinks.evidence)
    assert len(list((tmp_path / "m-t06test").glob("*.jpg"))) == st.groups
    img = cv2.imdecode(__import__("numpy").frombuffer(next(iter(sinks.evidence.values())), "uint8"), 1)
    assert max(img.shape[:2]) <= 640
    assert 1 < st.mask_pct < 15


@pytest.mark.live
def test_ingest_live_10s_clip(tmp_path):
    """Real Kafka + Postgres + Valkey: ingest a 10 s generated clip at real time and read it back."""
    from app import stores
    from workers.ingest import ingest

    clip = tmp_path / "clip.mp4"
    mtv.make_video(clip, 10.0)
    mid = f"m-test-ingest-{uuid.uuid4().hex[:6]}"
    consumer = stores.kafka_consumer("frames", group_id=f"test-ingest-{mid}", auto_offset_reset="earliest",
                                     consumer_timeout_ms=1000)
    consumer.poll(timeout_ms=3000)
    st = ingest(clip, mid, speed=1.0, frames_dir=tmp_path / "frames")
    got = []
    deadline = time.monotonic() + 45
    while len(got) < st.frames and time.monotonic() < deadline:
        for batch in consumer.poll(timeout_ms=1000).values():
            got += [m.value for m in batch if m.value["match_id"] == mid]
    consumer.close()
    assert len(got) == st.frames == 20
    assert [round(g["t"], 1) for g in got] == [i * 0.5 for i in range(20)]
    assert 9.5 <= st.elapsed_s <= 30  # about real time (flush and network latency add some)
    rows = stores.postgres().fetch("SELECT count(*) FROM evidence WHERE match_id = %s", (mid,))
    assert rows[0][0] == st.groups
    assert stores.valkey().sismember("sp:matches", mid)
    stores.postgres().execute("DELETE FROM evidence WHERE match_id = %s", (mid,))
    stores.valkey().srem("sp:matches", mid)
    stores.valkey().delete(f"sp:match:{mid}")
    assert os.path.isdir(tmp_path / "frames" / mid)
