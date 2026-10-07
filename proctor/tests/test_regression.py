import pytest
import yaml
from proctor.core.models import FaceResult, YoloResult, Box, HandResult
from proctor.core.rules import HoldRule, RuleEngine
from proctor.tools.replay_evaluator import match


@pytest.fixture
def engine():
    with open("proctor/config.yaml", encoding="utf-8") as f:
        return RuleEngine(yaml.safe_load(f))


def face(**kw):
    return FaceResult(
        n_faces=1, brightness=120, variance=2000, face_box=(200, 100, 400, 350), **kw
    )


@pytest.mark.parametrize(
    "yaw,ih,iv,head,gaze",
    [
        (0, 0.5, 0.5, "CENTER", "CENTER"),
        (0, 0.8, 0.5, "CENTER", "LEFT"),
        (0, 0.2, 0.5, "CENTER", "RIGHT"),
        (0, 0.5, 0.9, "CENTER", "DOWN"),
        (0, 0.5, 0.1, "CENTER", "UP"),
        (30, 0.5, 0.5, "RIGHT", "CENTER"),
        (-30, 0.5, 0.5, "LEFT", "CENTER"),
        (30, 0.8, 0.5, "RIGHT", "LEFT"),
    ],
)
def test_independent_states(engine, yaw, ih, iv, head, gaze):
    engine.on_face(face(yaw=yaw, iris_h=ih, iris_v=iv))
    engine.tick(0)
    engine.tick(0.2)
    assert engine.debug["head"] == head and engine.debug["gaze"] == gaze


def test_hysteresis_does_not_chatter(engine):
    engine.on_face(face(yaw=0))
    engine.tick(0)
    for i, yaw in enumerate((2, -3, 4, -5, 1, -2)):
        engine.on_face(face(yaw=yaw))
        engine.tick(0.2 + i * 0.1)
        assert engine.debug["head"] == "CENTER"


def test_single_frame_noise_no_warning(engine):
    engine.on_face(face(iris_h=0.8))
    assert not engine.tick(0)
    engine.on_face(face())
    for t in (0.05, 0.2, 0.5, 1, 3):
        assert "GAZE_LEFT" not in engine.tick(t)


def test_long_gaze_timer_then_center_reset(engine):
    engine.on_face(face(iris_h=0.8))
    engine.tick(0)
    assert "GAZE_LEFT" in engine.tick(2.1)
    assert engine.rules["GAZE_LEFT"].active_duration(2.1) == 2.1
    engine.on_face(face())
    for t in (2.2, 2.4, 2.8):
        engine.tick(t)
    assert engine.rules["GAZE_LEFT"].active_duration(2.8) == 0


def test_gap_reset_on_return_and_zero_origin():
    r = HoldRule(1, gap_tolerance=0.3)
    r.update(True, 0)
    assert r.active_duration(0.2) == 0.2
    r.update(False, 0.4)
    assert not r.update(True, 1.1)
    assert r.active_duration(1.1) == 0


def test_phone_aimed_no_double_hold(engine):
    phone = Box(0.9, 240, 110, 290, 230, track_id=1, confirmed=True)
    engine.on_face(face())
    engine.on_yolo(YoloResult(phones=[phone], phone_voted=True))
    engine.tick(0)
    assert "PHONE_AIMED" in engine.tick(2.1)


def test_no_face_not_raised(engine):
    engine.on_face(FaceResult(n_faces=0, brightness=120, variance=2000))
    engine.on_yolo(
        YoloResult(phones=[Box(0.9, 0, 0, 40, 80, confirmed=True)], phone_voted=True)
    )
    engine.tick(0)
    assert "PHONE_RAISED" not in engine.tick(3)


def test_person_count_not_face_count(engine):
    engine.on_face(face())
    engine.on_yolo(YoloResult(n_persons=3))
    engine.tick(0)
    assert "MULTI_FACE" not in engine.tick(3)


def test_second_face_temporal(engine):
    engine.on_face(FaceResult(n_faces=2, brightness=120, variance=2000))
    assert "MULTI_FACE" not in engine.tick(0)
    assert "MULTI_FACE" in engine.tick(1.2)


def test_stale_results_no_warning(engine):
    engine.on_face(face(iris_h=0.8, captured_at=100.0))
    engine.on_yolo(YoloResult(phone_voted=True, captured_at=100.0))
    engine.tick(100.0)
    for t in (101.0, 102.0, 105.0):
        out = engine.tick(t)
        assert (
            "GAZE_LEFT" not in out
            and "NO_FACE" not in out
            and "PHONE_DETECTED" not in out
        )
    assert engine.debug["gaze"] == "UNKNOWN"


def test_error_not_student_absence(engine):
    engine.on_face(FaceResult(error="FaceMesh failed"))
    engine.tick(0)
    assert "NO_FACE" not in engine.tick(5)


def test_phone_in_hand_gated(engine):
    engine.on_face(face())
    engine.on_yolo(
        YoloResult(
            phones=[Box(0.9, 230, 250, 300, 330, confirmed=True)], phone_voted=True
        )
    )
    engine.on_hands(HandResult(boxes=[(220, 270, 340, 380)]))
    engine.tick(0)
    assert "PHONE_IN_HAND" in engine.tick(0.6)


def test_replay_reports_unexpected_event_types():
    m = match([{"type": "PHONE_DETECTED", "t": 1.0}], [])
    assert m["PHONE_DETECTED"]["false_positives"] == 1


def test_direction_invalid_not_center(engine):
    engine.on_face(face(pose_valid=False, gaze_valid=False))
    engine.tick(0)
    assert engine.debug["head"] == "UNKNOWN" and engine.debug["gaze"] == "UNKNOWN"


def test_phone_lift_motion_not_single_frame(engine):
    engine.on_face(face())
    engine.on_yolo(
        YoloResult(
            phones=[
                Box(
                    0.9, 220, 260, 270, 340, confirmed=True, velocity=(0, -200, 0, -200)
                )
            ],
            phone_voted=True,
        )
    )
    assert "PHONE_LIFTED" not in engine.tick(0)
    assert "PHONE_LIFTED" in engine.tick(0.2)


def test_calibration_noise_does_not_shrink_configured_head_deadzone(engine):
    engine.calib = {"yaw": 0, "pitch": 0, "head_x_threshold": 10, "head_y_threshold": 8}
    engine.on_face(face(yaw=14, pitch=10))
    engine.tick(0)
    assert engine.debug["head"] == "CENTER"


def test_noncalibrated_gaze_uses_configured_entry_threshold(engine):
    engine.on_face(
        face(iris_h=0.5 + engine.cfg["rules"]["gaze_side"]["iris_thresh"] * 0.6)
    )
    engine.tick(0)
    assert engine.debug["gaze"] == "CENTER"
