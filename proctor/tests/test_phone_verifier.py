import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from proctor.core.detector_yolo import DetectorYolo
from proctor.core.models import Box, YoloResult
from proctor.core.pipeline import FramePacket
from proctor.tools.fetch_weights import fetch_models


def verifier(**kwargs):
    return DetectorYolo(detail_model="proctor/weights/yolov8s.pt", **kwargs)


def test_verifier_caps_rate_without_silently_lowering_confidence():
    d = verifier(target_fps=8, imgsz=416)
    assert d.budget.target_fps == d.budget.ceiling == 4
    assert d.detail_imgsz == 384 and d.conf_phone == 0.25
    assert verifier(target_fps=3).budget.target_fps == 3
    plain = verifier(detail_search=False, target_fps=8)
    assert plain.detail_model_name is None and plain.budget.target_fps == 8


def test_verifier_replaces_detail_pass_not_third_inference():
    d = verifier(adaptive=False)
    calls = []
    d._infer = lambda *args: (calls.append(args) or ([], []))
    for seq in range(12):
        before = len(calls)
        r = d.process(FramePacket(seq, 100 + seq * 0.25, np.zeros((480, 640, 3))))
        assert len(calls) - before == 2
        assert calls[-1][1:3] == (384, [67])
        assert not r.phone_voted and not r.phones
    assert d.detail_calls == 12


def test_clear_global_phone_skips_verifier():
    d = verifier(adaptive=False)
    calls = []
    d._infer = lambda *args: (calls.append(args) or [Box(0.9, 20, 20, 150, 200)], [])
    r = d.process(FramePacket(1, 100, np.zeros((480, 640, 3))))
    assert len(calls) == 1 and r.candidates[0].conf == 0.9
    assert not r.detail_region


def test_overlapping_search_keeps_wide_bottom_object_whole():
    d = verifier(adaptive=False)
    frame = np.zeros((480, 640, 3))
    rectangles = []
    for i in range(3):
        d.calls = i + 1
        d._detail_roi(frame, [], 100 + i * 0.25)
        rectangles.append(d.detail_rect)
    for left in range(0, 640 - 220, 10):
        assert any(x1 <= left and left + 220 <= x2 for x1, _, x2, _ in rectangles)
    assert all(y1 == 264 and y2 == 480 for _, y1, _, y2 in rectangles)


def test_retained_roi_is_not_reused_detection():
    d = verifier(adaptive=False)
    actual_calls = []

    def infer(frame, size, classes, offset=(0, 0)):
        actual_calls.append((size, classes))
        return (
            [Box(0.48, 150, 350, 290, 479, source="detail")] if classes == [67] else []
        ), []

    d._infer = infer
    frame = np.zeros((480, 640, 3))
    first = d.process(FramePacket(1, 100, frame))
    assert not first.phone_voted
    d.output.put(first)
    second = d.process(FramePacket(2, 100.25, frame))
    assert second.detail_rect == first.detail_rect
    assert second.detail_region == "candidate retained context"
    assert second.phone_voted and second.candidates[0].conf == 0.48
    assert second.seq == 2 and second.captured_at == 100.25
    assert len(actual_calls) == 4
    d.output.put(second)
    d._infer = lambda *args: ([], [])
    absent = d.process(FramePacket(3, 100.5, frame))
    assert not absent.phone_voted and not absent.candidates
    assert not any(b.observed for b in absent.phones)


def test_stale_roi_is_not_retained():
    d = verifier(adaptive=False)
    d.output.put(
        YoloResult(
            candidates=[Box(0.48, 150, 350, 290, 479, source="detail")],
            captured_at=100,
            detail_rect=(120, 264, 520, 480),
        )
    )
    d.calls = 2
    d._detail_roi(np.zeros((480, 640, 3)), [], 102)
    assert d.detail_region == "desk center"


def test_weak_roi_alone_never_becomes_confirmed_phone():
    d = verifier(adaptive=False)
    d._infer = lambda frame, size, classes, *args: (
        [Box(0.18, 150, 350, 290, 479, source="detail")] if classes == [67] else [],
        [],
    )
    for seq in range(12):
        result = d.process(FramePacket(seq, 100 + seq * 0.25, np.zeros((480, 640, 3))))
        d.output.put(result)
        assert not result.phone_voted


def test_missing_verifier_weights_are_explicit(tmp_path):
    base = tmp_path / "nano.pt"
    base.write_bytes(b"test")
    d = DetectorYolo(str(base), detail_model=str(tmp_path / "missing.pt"))
    with pytest.raises(FileNotFoundError, match="fetch_weights"):
        d.setup()


def test_prediction_dispatches_to_selected_model():
    calls = []

    class FakeModel:
        def __init__(self, name):
            self.name = name

        def predict(self, image, **kwargs):
            calls.append((self.name, kwargs))
            return [SimpleNamespace(boxes=None)]

    d = verifier()
    d.model = FakeModel("nano")
    d.detail_model = FakeModel("verifier")
    d._infer(np.zeros((480, 640, 3)), 416, [67, 0])
    d._infer(np.zeros((216, 396, 3)), 384, [67])
    assert [name for name, _ in calls] == ["nano", "verifier"]
    assert all(args["conf"] == 0.10 for _, args in calls)


def test_verifier_class_mismatch_fails_setup(tmp_path, monkeypatch):
    import sys

    base, detail = tmp_path / "nano.pt", tmp_path / "small.pt"
    base.touch()
    detail.touch()
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(
            YOLO=lambda path: SimpleNamespace(
                names={0: "person", 67: "cell phone" if path == str(base) else "car"}
            )
        ),
    )
    d = DetectorYolo(str(base), detail_model=str(detail), device="cpu")
    with pytest.raises(ValueError, match="класс телефона"):
        d.setup()


def test_download_prepares_both_models_and_reuses_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def factory(path):
        calls.append(path)
        p = Path(path)
        if not p.is_file():
            p.write_bytes(b"weights")
        return SimpleNamespace(ckpt_path=str(p))

    assert len(fetch_models(factory=factory)) == 2
    assert calls == ["yolov8n.pt", "yolov8s.pt"]
    calls.clear()
    fetch_models(factory=factory)
    assert calls == ["proctor/weights/yolov8n.pt", "proctor/weights/yolov8s.pt"]


@pytest.mark.skipif(
    os.getenv("PROCTOR_MODEL_SMOKE") != "1",
    reason="opt-in: native models with local weights",
)
def test_native_verifier_blank_scene_and_external_phone_fixture():
    import cv2

    cv2.setNumThreads(1)
    d = verifier(
        model_name="proctor/weights/yolov8n.pt", imgsz=416, threads=1, adaptive=False
    )
    d.setup()
    frame = np.full((480, 640, 3), 110, dtype=np.uint8)
    for seq in range(8):
        r = d.process(FramePacket(seq, 100 + seq * 0.25, frame))
        d.output.put(r)
        assert not r.phone_voted and not r.candidates
    face_path = os.getenv("PROCTOR_FACE_FIXTURE")
    if face_path:
        portrait = cv2.imread(face_path)
        assert portrait is not None
        for seq in range(8, 16):
            r = d.process(FramePacket(seq, 100 + seq * 0.25, portrait))
            d.output.put(r)
            assert not r.phone_voted and not r.candidates
    path = os.getenv("PROCTOR_PHONE_FIXTURE")
    if not path:
        return
    frame = cv2.imread(path)
    assert frame is not None
    d.output.put(None)
    detected = []
    for seq in range(12):
        r = d.process(FramePacket(seq + 10, time.monotonic(), frame))
        d.output.put(r)
        detected.append(r.phone_voted)
        for box in r.candidates:
            assert 0 <= box.x1 < box.x2 <= frame.shape[1]
            assert 0 <= box.y1 < box.y2 <= frame.shape[0]
        time.sleep(0.25)
    assert sum(detected) >= 8
