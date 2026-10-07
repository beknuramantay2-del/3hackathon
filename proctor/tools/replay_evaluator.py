import argparse
import json
import os
import sys
import time

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from proctor.core.config import load_config, ConfigError
from proctor.core.rules import RuleEngine
from proctor.core.models import FaceResult, YoloResult, Box
from proctor.core.calibration import Calibration

TOL = 0.5


def run(video, cfg, calibration_mode="five"):
    import cv2
    from proctor.core.pipeline import FramePacket
    from proctor.core.face_mesh import FaceMeshThread
    from proctor.core.detector_yolo import DetectorYolo
    from proctor.core.hands import HandsThread

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise ValueError(f"Нет видеофайла: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    yc, fc = cfg["yolo"], cfg["face"]
    yolo = DetectorYolo(
        yc["model"],
        yc["imgsz"],
        yc["conf_phone"],
        yc["conf_person"],
        offline=True,
        adaptive=False,
        phone_class=yc["phone_class"],
        person_class=yc["person_class"],
    )
    face = FaceMeshThread(
        fc["max_faces"],
        max_width=fc.get("max_width", 640),
        min_detection_confidence=fc["min_detection_confidence"],
        min_tracking_confidence=fc["min_tracking_confidence"],
    )
    latest = {"yolo": None}
    hands = (
        HandsThread(lambda: latest["yolo"]) if fc.get("hands_enabled", True) else None
    )
    workers = [yolo, face] + ([hands] if hands else [])
    eng = RuleEngine(cfg, {})
    seconds = (
        cfg["calibration"]["duration_sec"]
        if calibration_mode == "five"
        else (5.0 if calibration_mode == "center" else 0.0)
    )
    calib = Calibration(seconds or 20.0)
    base = time.monotonic()
    calib.start(base)
    next_yolo = next_face = next_hands = 0.0
    calibrated = calibration_mode == "none"
    fired, i = [], 0
    try:
        for w in workers:
            w.setup()
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            t = i / fps
            packet = FramePacket(i, base + t, fr, t)
            i += 1
            if t >= next_yolo:
                latest["yolo"] = yolo.process(packet)
                eng.on_yolo(latest["yolo"])
                next_yolo = t + 1 / yc["target_fps"]
            if t >= next_face:
                f = face.process(packet)
                eng.on_face(f)
                next_face = t + 1 / fc.get("target_fps", 15)
                if (
                    not calibrated
                    and t < seconds
                    and f.n_faces == 1
                    and f.pose_valid
                    and f.gaze_valid
                ):
                    if calibration_mode == "five":
                        calib.add(f.yaw, f.pitch, f.iris_h, f.iris_v, now=base + t)
                    elif t >= 0.7:
                        calib.add(f.yaw, f.pitch, f.iris_h, f.iris_v, pose="CENTER")
            if hands and t >= next_hands:
                eng.on_hands(hands.process(packet))
                next_hands = t + 1 / fc.get("hands_fps", 5)
            if not calibrated and t >= seconds:
                eng.calib = calib.finish(center_only=calibration_mode == "center")
                if calibration_mode == "five" and eng.calib["unresolved_targets"]:
                    raise ValueError(
                        "Не измерены позы калибровки: "
                        + str(eng.calib["unresolved_targets"])
                    )
                eng.reset()
                calibrated = True
            if calibrated:
                for kind in eng.tick(base + t):
                    fired.append({"type": kind, "t": round(t, 2)})
        if not calibrated:
            raise ValueError("Видео короче калибровки или калибровка не пройдена")
    finally:
        cap.release()
        for w in workers:
            w.teardown()
    return fired


def match(fired, gt):
    by_type = {}
    for g in gt:
        by_type.setdefault(g["type"], []).append(g)
    det_by_type = {}
    for d in fired:
        det_by_type.setdefault(d["type"], []).append(d)
    out = {}
    tp_total = 0
    f1w = 0.0
    for t in sorted(set(by_type) | set(det_by_type)):
        items = by_type.get(t, [])
        dets = sorted(det_by_type.get(t, []), key=lambda d: d["t"])
        matched = set()
        lat = []
        tp = 0
        for d in dets:
            for gi, g in enumerate(items):
                if (
                    gi not in matched
                    and g["t_start"] - TOL <= d["t"] <= g["t_end"] + TOL
                ):
                    matched.add(gi)
                    tp += 1
                    lat.append(round(d["t"] - g["t_start"], 2))
                    break
        fp = len(dets) - tp
        fn = len(items) - len(matched)
        prec = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
        rec = tp / len(items) if items else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        f1w += f1 * len(items)
        out[t] = {
            "precision": round(prec, 2),
            "recall": round(rec, 2),
            "avg_latency_sec": round(sum(lat) / len(lat), 2) if lat else None,
            "false_positives": fp,
            "fn": fn,
        }
        tp_total += tp
    out["overall"] = {
        "weighted_f1": round(f1w / len(gt), 2) if gt else 0.0,
        "total_events": len(gt),
        "detected": tp_total,
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--profile", default="dev", choices=("dev", "exam"))
    ap.add_argument("--calibration", choices=("five", "center", "none"), default="five")
    a = ap.parse_args()
    try:
        cfg = load_config(a.config, a.profile)
    except ConfigError as e:
        raise SystemExit(str(e))
    gt = json.load(open(a.gt, encoding="utf-8"))
    fired = run(a.video, cfg, a.calibration)
    metrics = match(fired, gt)
    json.dump(
        {"detections": fired, "metrics": metrics, "calibration": a.calibration},
        open(a.output, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
