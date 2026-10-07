import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest, numpy as np
from PyQt6.QtWidgets import QApplication, QPushButton
from proctor.ui.monitor_window import MonitorWindow
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.models import Box, YoloResult
from proctor.core.pipeline import FramePacket


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("source,minimum", [((480, 640), 0.45), ((720, 1280), 0.38)])
def test_visible_image_area_not_label(app, source, minimum):
    w = MonitorWindow()
    w.resize(1280, 800)
    w.show()
    app.processEvents()
    w.show_frame(np.zeros((*source, 3), np.uint8))
    app.processEvents()
    r = w.camera.image_rect
    assert r.width() * r.height() / (w.width() * w.height()) >= minimum
    assert abs(r.width() / r.height() - source[1] / source[0]) < 0.02
    assert not hasattr(w.camera, "hud")
    assert not w.neutral.isVisible()
    assert len([b for b in w.findChildren(QPushButton) if b.isVisible()]) == 2
    assert w.gaze_card.chips == {} and w.head_card.chips == {}
    old = r.width() * r.height()
    w.resize(1440, 900)
    app.processEvents()
    assert w.camera.image_rect.width() * w.camera.image_rect.height() > old
    w.close()


def test_bottom_search_overlaps_and_checks_other_regions():
    d = DetectorYolo(adaptive=False)
    f = np.zeros((480, 640, 3), np.uint8)
    crops = []
    for n in range(4):
        d.calls = 2 * (n + 1)
        roi, origin = d._detail_roi(f, [], 100 + n * 0.2)
        assert roi.size and min(origin) >= 0
        crops.append((d.detail_region, d.detail_rect))
    bottom = [r for name, r in crops if name.startswith("desk")]
    assert len(bottom) == 3 and any(name == "full-frame quadrant" for name, _ in crops)
    assert all(r[3] == 480 for r in bottom)
    intervals = sorted((r[0], r[2]) for r in bottom)
    assert intervals[0][0] == 0 and intervals[-1][1] >= 638
    assert intervals[0][1] > intervals[1][0] and intervals[1][1] > intervals[2][0]


def test_at_most_one_detail_inference_and_empty_desk_not_phone():
    d = DetectorYolo(adaptive=False)
    calls = []
    d._infer = lambda *a: (calls.append(a) or ([], []))
    for n in range(8):
        before = len(calls)
        r = d.process(FramePacket(n, 100 + n * 0.2, np.zeros((480, 640, 3), np.uint8)))
        assert len(calls) - before in (1, 2) and not r.phone_voted and not r.phones
    assert d.detail_calls == 4


def test_expired_candidate_not_followed():
    d = DetectorYolo(adaptive=False)
    d.output.put(YoloResult(candidates=[Box(0.2, 550, 10, 620, 50)], captured_at=90))
    d.calls = 2
    d._detail_roi(np.zeros((480, 640, 3), np.uint8), [], 100)
    assert d.detail_region == "desk center"


def test_fragment_zoom_clipped_to_frame():
    d = DetectorYolo(adaptive=False)
    d.calls = 2
    roi, origin = d._detail_roi(
        np.zeros((480, 640, 3), np.uint8), [Box(0.13, 1, 466, 20, 479)], 100
    )
    assert d.detail_region == "candidate zoom" and roi.size
    assert d.detail_rect[0] == 0 and d.detail_rect[3] == 480
