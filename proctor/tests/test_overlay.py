import numpy as np
from proctor.core.overlay import render_overlay
from proctor.core.pipeline import FramePacket
from proctor.core.models import FaceResult, YoloResult, HandResult, Box


def test_mirror_coordinates_and_input_immutable():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    p = FramePacket(1, 10.0, frame)
    f = FaceResult(
        face_box=(10, 30, 40, 70), face_boxes=[(10, 30, 40, 70)], captured_at=10.0
    )
    out = render_overlay(p, f, YoloResult(), HandResult(), mirror=True)
    assert out[30, 160].sum() > 0 and out[30, 10].sum() == 0
    assert frame.sum() == 0


def test_stale_boxes_not_rendered():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    p = FramePacket(1, 10.0, frame)
    f = FaceResult(face_boxes=[(10, 30, 40, 70)], captured_at=8.0)
    assert render_overlay(p, f, YoloResult(), HandResult(), mirror=False).sum() == 0


def test_prediction_preview_only():
    b = Box(0.9, 20, 40, 40, 80, confirmed=True, velocity=(100, 0, 100, 0))
    y = YoloResult(phones=[b], captured_at=10.0)
    p = FramePacket(1, 10.1, np.zeros((100, 200, 3), dtype=np.uint8))
    out = render_overlay(p, FaceResult(), y, HandResult(), mirror=False)
    assert out[40, 29:32].sum() > 0
    assert b.x1 == 20


def test_phone_overlay_stays_visible_between_bounded_four_hz_updates():
    b = Box(0.6, 20, 40, 60, 80, confirmed=True)
    y = YoloResult(phones=[b], captured_at=10.0)
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    f = FaceResult(face_boxes=[(100, 40, 150, 80)], captured_at=10.0)
    out = render_overlay(FramePacket(1, 10.45, frame), f, y, HandResult(), mirror=False)
    assert tuple(out[60, 20]) == (80, 80, 240)
    assert out[60, 100].sum() == 0
    expired = render_overlay(
        FramePacket(2, 10.7, frame), f, y, HandResult(), mirror=False
    )
    assert expired.sum() == 0
