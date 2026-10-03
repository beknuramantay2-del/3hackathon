"""По 10 попыток каждого нарушения: сколько поймано. Таблицу попыток/найдено — в отчёт и слайды."""
import time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml
from proctor.core.rules import RuleEngine
from proctor.core.models import FaceResult, YoloResult, Box

cfg = yaml.safe_load(open("proctor/config.yaml", encoding="utf-8"))

def run(condition_fn, hold_needed, tries=10):
    hits = 0
    for _ in range(tries):
        eng = RuleEngine(cfg, dict(yaw=0, pitch=0, iris_h=0.5, iris_v=0.5,
                                    yaw_mad=2.0, pitch_mad=2.0))
        t0 = time.monotonic()
        fired = False
        while time.monotonic() - t0 < hold_needed + 2.0:
            f, y, voted = condition_fn(time.monotonic() - t0)
            eng.on_face(f)
            eng.on_yolo(y, voted)
            if eng.tick():
                fired = True
                break
            time.sleep(0.05)
        hits += fired
    return hits

def phone():
    return lambda dt: (FaceResult(n_faces=1, face_box=(200, 150, 400, 350), brightness=120, variance=2000),
                       YoloResult(phones=[Box(0.9, 250, 100, 330, 250)], n_persons=1), True)

def gaze_down():
    return lambda dt: (FaceResult(n_faces=1, yaw=0, pitch=25, iris_h=0.5, iris_v=0.85,
                                  face_box=(200, 150, 400, 350), brightness=120, variance=2000),
                       YoloResult(phones=[], n_persons=1), False)

def noface():
    return lambda dt: (FaceResult(n_faces=0, brightness=120, variance=2000),
                       YoloResult(phones=[], n_persons=0), False)

if __name__ == "__main__":
    for name, fn, hold in [("PHONE_DETECTED", phone(), 0.6), ("GAZE_DOWN", gaze_down(), 3.2),
                           ("NO_FACE", noface(), 2.2)]:
        print(f"{name}: поймано {run(fn, hold)}/10 (синтетика; живые цифры — с камеры человеком)")
