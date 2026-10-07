import time, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml
from proctor.core.rules import HoldRule, RuleEngine
from proctor.core.models import FaceResult, YoloResult


def test_hold_fires_once():
    r = HoldRule(hold=1.0, cooldown=5.0, gap_tolerance=0.3)
    t0 = 100.0
    assert not r.update(True, t0)
    assert r.update(True, t0 + 1.1)
    assert not r.update(True, t0 + 1.2)


def test_gap_tolerance():
    r = HoldRule(hold=1.0, cooldown=0.0, gap_tolerance=0.3)
    t0 = 200.0
    assert not r.update(True, t0)
    assert not r.update(False, t0 + 0.95)
    assert r.update(True, t0 + 1.1)


def test_gap_resets():
    r = HoldRule(hold=1.0, cooldown=0.0, gap_tolerance=0.3)
    t0 = 300.0
    r.update(True, t0)
    r.update(False, t0 + 0.5)
    r.update(False, t0 + 1.0)
    assert not r.update(True, t0 + 1.1)


def test_gaze_down_needs_hold():
    cfg = yaml.safe_load(open("proctor/config.yaml", encoding="utf-8"))
    eng = RuleEngine(
        cfg, dict(yaw=0, pitch=0, iris_h=0.5, iris_v=0.5, yaw_mad=2.0, pitch_mad=2.0)
    )
    t0 = time.monotonic()
    eng.on_face(
        FaceResult(
            n_faces=1,
            yaw=0,
            pitch=25,
            iris_h=0.5,
            iris_v=0.85,
            face_box=(0, 0, 10, 10),
            brightness=120,
            variance=2000,
        )
    )
    eng.on_yolo(YoloResult(phones=[], n_persons=1), False)
    assert eng.tick(t0) == []
    assert "GAZE_DOWN" in eng.tick(t0 + 4.0)


def test_no_false_multi():
    cfg = yaml.safe_load(open("proctor/config.yaml", encoding="utf-8"))
    eng = RuleEngine(cfg, dict(yaw=0, pitch=0, iris_h=0.5, iris_v=0.5))
    eng.on_face(FaceResult(n_faces=1, brightness=120, variance=2000))
    eng.on_yolo(YoloResult(phones=[], n_persons=1), False)
    assert "MULTI_FACE" not in eng.tick(time.monotonic() + 5.0)


if __name__ == "__main__":
    for fn in [
        test_hold_fires_once,
        test_gap_tolerance,
        test_gap_resets,
        test_gaze_down_needs_hold,
        test_no_false_multi,
    ]:
        fn()
        print(f"OK {fn.__name__}")
    print("Все тесты прошли (синтетика, без камеры).")
