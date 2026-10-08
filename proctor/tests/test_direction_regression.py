import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np, pytest, yaml, cv2
from proctor.core.models import FaceResult
from proctor.core.rules import RuleEngine
from proctor.core.mandatory_calibration import (
    MandatoryCalibration,
    calibration_complete,
)
from proctor.core.calibration import CalibrationError
from proctor.core.pipeline import FramePacket
from proctor.core.overlay import render_overlay
from proctor.tests.test_mandatory_episodes import feed_pass, complete


def engine(base=None):
    return RuleEngine(yaml.safe_load(open("proctor/config.yaml")), base)


def neutral_preview():
    c = MandatoryCalibration()
    c.start(100)
    for n in range(12):
        t = 100.7 + n * 0.1
        c.feed(
            FaceResult(
                n_faces=1,
                yaw=12,
                pitch=-8,
                iris_h=0.6,
                iris_v=0.55,
                left_eye=(0.58, 0.55),
                right_eye=(0.62, 0.55),
                captured_at=t,
            )
        )
    return c.preview()


@pytest.mark.parametrize(
    "axis,delta,wanted",
    [(0, -25, "LEFT"), (0, 25, "RIGHT"), (1, -20, "UP"), (1, 20, "DOWN")],
)
def test_uncalibrated_measured_head_visible_but_no_warning(axis, delta, wanted):
    e = engine()
    e.preview_base = neutral_preview()
    angles = [12, -8]
    angles[axis] += delta
    for i in range(70):
        now = 105 + i * 0.1
        e.on_face(
            FaceResult(
                n_faces=1,
                yaw=angles[0],
                pitch=angles[1],
                iris_h=0.6,
                iris_v=0.55,
                seq=i,
                captured_at=now,
            )
        )
        assert not any(
            k.startswith(("HEAD_", "GAZE_"))
            for k in e.tick(now, directions_enabled=False)
        )
        assert e.debug["head_display"] == wanted and e.debug["preview_ready"]
        assert not e.debug["head_display_calibrated"]
        assert not any(
            v for k, v in e.debug["conds"].items() if k.startswith(("HEAD_", "GAZE_"))
        )


@pytest.mark.parametrize(
    "h,v,wanted",
    [
        (0.78, 0.55, "LEFT"),
        (0.42, 0.55, "RIGHT"),
        (0.6, 0.37, "UP"),
        (0.6, 0.73, "DOWN"),
    ],
)
def test_uncalibrated_gaze_independent_and_relative_to_measured_eyes(h, v, wanted):
    e = engine()
    e.preview_base = neutral_preview()
    e.on_face(
        FaceResult(
            n_faces=1,
            yaw=12,
            pitch=-8,
            iris_h=h,
            iris_v=v,
            left_eye=(h - 0.02, v),
            right_eye=(h + 0.02, v),
            seq=1,
            captured_at=105,
        )
    )
    e.tick(105, directions_enabled=False)
    assert e.debug["head_display"] == "CENTER" and e.debug["gaze_display"] == wanted
    assert not e.debug["gaze_display_calibrated"]


def test_does_not_anchor_neutral_from_an_arbitrary_side_target():
    c = MandatoryCalibration()
    c.start(100)
    for n in range(12):
        c.feed(FaceResult(n_faces=1, yaw=30, captured_at=103.7 + n * 0.1))
    assert c.preview() == {}


def test_failed_eye_pass_keeps_successful_head_preview_not_exam_permission():
    c = MandatoryCalibration()
    c.start(100)
    feed_pass(c, 100, head=True)
    c.advance(115)
    before = c.preview()
    assert before["head_calibrated"] and len(before["head_targets"]) == 4
    feed_pass(c, 115, static=True)
    with pytest.raises(CalibrationError):
        c.advance(130)
    after = c.preview()
    assert after["head_targets"] == before["head_targets"]
    assert not calibration_complete(after) and not c.done


def test_stale_data_and_person_change_not_ghost_directions():
    e = engine()
    e.preview_base = neutral_preview()
    e.on_face(FaceResult(n_faces=1, yaw=37, pitch=-8, seq=1, captured_at=105))
    e.tick(106, directions_enabled=False)
    assert e.debug["head_display"] == e.debug["gaze_display"] == "UNKNOWN"
    e.on_face(FaceResult(n_faces=1, primary_changed=True))
    assert e.preview_base == {}
    e.tick(106, directions_enabled=False)
    assert not e.debug["preview_ready"]


def test_one_readable_eye_visible_but_never_creates_gaze_alert():
    base = complete()
    e = engine(base)
    for i in range(70):
        now = 200 + i * 0.1
        e.on_face(
            FaceResult(
                n_faces=1,
                yaw=base["yaw"],
                pitch=base["pitch"],
                iris_h=0.65,
                iris_v=0.5,
                left_eye=(0.63, 0.5),
                right_eye=None,
                gaze_valid=False,
                gaze_preview_valid=True,
                seq=i,
                captured_at=now,
            )
        )
        found = e.tick(now)
        assert e.debug["gaze"] == "UNKNOWN" and e.debug["gaze_display"] == "LEFT"
        assert not e.debug["gaze_display_calibrated"] and not any(
            k.startswith("GAZE_") for k in found
        )


def test_overlay_arrows_for_measured_preview_but_not_unanchored():
    p = FramePacket(1, 100, np.zeros((150, 180, 3), np.uint8))
    f = FaceResult(
        captured_at=100,
        face_box=(50, 50, 120, 120),
        eye_points=[(80, 80)],
        gaze_valid=False,
        gaze_preview_valid=True,
    )
    none = render_overlay(
        p, f, None, None, states={"head": "RIGHT", "gaze": "DOWN", "calibrated": False}
    )
    preview = render_overlay(
        p,
        f,
        None,
        None,
        states={
            "head": "RIGHT",
            "gaze": "DOWN",
            "calibrated": False,
            "preview_ready": True,
        },
    )
    assert not np.array_equal(none, preview)
    assert preview[90:100, 98:103].sum() > none[90:100, 98:103].sum()


def test_visible_preview_label_and_calibration_readouts():
    from PyQt6.QtWidgets import QApplication
    from proctor.ui.monitor_window import MonitorWindow

    app = QApplication.instance() or QApplication([])
    w = MonitorWindow()
    w.show()
    app.processEvents()
    w.show_directions("LEFT", "DOWN", False, preview=True)
    assert "Влево" in w.head_card.state.text() and "Вниз" in w.gaze_card.state.text()
    assert (
        "Предварительно" in w.head_card.note.text() and not w.head_card.note.isHidden()
    )
    assert w.head_card.layout().indexOf(w.head_card.note) >= 0
    w.close()


@pytest.mark.skipif(
    os.getenv("PROCTOR_MODEL_SMOKE") != "1",
    reason="opt-in native face with injected monocular ratio",
)
def test_native_face_worker_keeps_single_eye_measurement(monkeypatch):
    from proctor.core.face_mesh import FaceMeshThread

    image = cv2.imread(os.environ["PROCTOR_FACE_FIXTURE"])
    assert image is not None
    worker = FaceMeshThread()
    try:
        worker.setup()
        monkeypatch.setattr(
            "proctor.core.face_mesh.eye_gaze", lambda *a: (None, (0.7, 0.5), (0.7, 0.5))
        )
        f = worker.process(FramePacket(1, 100, image))
        assert f.pose_valid and f.pose_method == "PnP SQPnP+LM"
        assert (
            f.gaze_preview_valid and not f.gaze_valid and f.iris_h == pytest.approx(0.7)
        )
        assert "Один глаз" in f.gaze_reason
    finally:
        worker.teardown()
