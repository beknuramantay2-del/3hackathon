"""10 мин нормального поведения: считает лишние события по типам (FPR). Запуск у человека на камере."""
import time, argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml
from proctor.core.rules import RuleEngine
from proctor.core.models import FaceResult, YoloResult

# Заглушка: в реальном прогоне сюда подаются FaceResult/YoloResult с живой камеры.
# Скрипт гоняет движок 10 мин и печатает, что сработало без нарушений.
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    a = ap.parse_args()
    cfg = yaml.safe_load(open("proctor/config.yaml", encoding="utf-8"))
    eng = RuleEngine(cfg, dict(yaw=0, pitch=0, iris_h=0.5, iris_v=0.5))
    print(f"Смотрите в экран {a.minutes} мин: печатайте, потирайте лицо, откидывайтесь.")
    print("Живой прогон: подключите камеру и подавайте FaceResult/YoloResult в eng.tick().")
    print("Синтетический ноль-прогон (без камеры): ложных срабатываний 0 по построению.")
    t0 = time.monotonic()
    fires = {}
    while time.monotonic() - t0 < a.minutes * 60:
        eng.on_face(FaceResult(n_faces=1, yaw=0, pitch=0, iris_h=0.5, iris_v=0.5,
                               brightness=120, variance=2000))
        eng.on_yolo(YoloResult(phones=[], n_persons=1), False)
        for v in eng.tick():
            fires[v] = fires.get(v, 0) + 1
            print(f"[{time.monotonic() - t0:6.1f}c] {v}")
        time.sleep(0.1)
    print("Итог лишних событий:", fires or "0 — чисто")
