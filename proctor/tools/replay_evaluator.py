"""Прогон видео через YOLO + FaceMesh + RuleEngine и сверка с разметкой GT.

Метрики: precision/recall/latency на тип, общий weighted_f1.
Калибровка: медиана первых 5 секунд видео. Шкала времени: метки видео.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proctor.core.config import load_config, ConfigError
from proctor.core.rules import RuleEngine
from proctor.core.models import FaceResult, YoloResult, Box
from proctor.core.calibration import Calibration

TOL = 0.5
PNP_IDX = [1, 152, 33, 263, 61, 291]

import numpy as np


def head_pose(pts, w, h):
    import cv2
    import math
    model = np.array([(0, 0, 0), (-165, -170, -135), (165, -170, -135),
                      (-150, -150, -125), (150, -150, -125), (0, -330, -65)], dtype=np.float64)
    img = np.array([(pts[i][0], pts[i][1]) for i in PNP_IDX], dtype=np.float64)
    cam = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], dtype=np.float64)
    try:
        _, rvec, _ = cv2.solvePnP(model, img, cam, np.zeros((4, 1)))
        rmat, _ = cv2.Rodrigues(rvec)
        yaw = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))
        pitch = math.degrees(math.atan2(-rmat[2, 0], (rmat[2, 1] ** 2 + rmat[2, 2] ** 2) ** 0.5))
        return yaw, pitch
    except Exception:
        return 0.0, 0.0


def iris(pts):
    try:
        center = pts[468:478].mean(axis=0)
        dl = abs(pts[133][0] - pts[33][0]) + 1e-6
        dr = abs(pts[263][0] - pts[362][0]) + 1e-6
        h = float(np.clip(((center[0] - pts[33][0]) / dl + (center[0] - pts[362][0]) / dr) / 2, 0, 1))
        top = (pts[159][1] + pts[386][1]) / 2
        bot = (pts[145][1] + pts[374][1]) / 2
        v = float(np.clip((center[1] - top) / (bot - top + 1e-6), 0, 1))
        return h, v
    except Exception:
        return 0.5, 0.5


def run(video, cfg):
    import cv2
    from ultralytics import YOLO
    import mediapipe as mp

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"нет видеофайла: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    yolo = YOLO(cfg["yolo"]["model"])
    mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=cfg["face"]["max_faces"], refine_landmarks=True,
        min_detection_confidence=0.5, min_tracking_confidence=0.5)
    eng = RuleEngine(cfg, {})
    calib = Calibration(5.0)
    votes = []
    vw, vt = cfg["yolo"]["vote_window"], cfg["yolo"]["vote_threshold"]
    stride = max(1, round(fps / cfg["yolo"]["target_fps"]))
    base = time.monotonic()
    calib_until = 5.0
    fired = []
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = i / fps
        i += 1
        h, w = fr.shape[:2]
        res = mesh.process(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        faces = res.multi_face_landmarks or []
        if faces:
            lm = faces[0]
            pts = np.array([(p.x * w, p.y * h) for p in lm.landmark])
            yaw, pitch = head_pose(pts, w, h)
            ih, iv = iris(pts)
            xs, ys = pts[:, 0], pts[:, 1]
            box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        else:
            yaw = pitch = 0.0
            ih, iv, box = 0.5, 0.5, None
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        f = FaceResult(n_faces=len(faces), yaw=yaw, pitch=pitch, iris_h=ih, iris_v=iv,
                       face_box=box, brightness=float(g.mean()), variance=float(g.var()))
        phones, persons = [], 0
        if i % stride == 0:
            r = yolo.predict(fr, imgsz=cfg["yolo"]["imgsz"], verbose=False)[0]
            for b in r.boxes:
                cls, conf = int(b.cls[0]), float(b.conf[0])
                if cls == cfg["yolo"]["phone_class"] and conf >= cfg["yolo"]["conf_phone"]:
                    x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                    phones.append(Box(conf, x1, y1, x2, y2))
                elif cls == cfg["yolo"]["person_class"] and conf >= cfg["yolo"]["conf_person"]:
                    persons += 1
            votes.append(1 if phones else 0)
            votes = votes[-vw:]
        eng.on_face(f)
        eng.on_yolo(YoloResult(phones=phones, n_persons=persons), sum(votes) >= vt)
        if t <= calib_until and faces:
            calib.add(yaw, pitch, ih, iv)
        elif t > calib_until and not eng.calib.get("yaw_mad"):
            eng.calib = calib.finish()
        for vtype in eng.tick(base + t):
            fired.append({"type": vtype, "t": round(t, 2)})
    cap.release()
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
    for t, items in by_type.items():
        dets = sorted(det_by_type.get(t, []), key=lambda d: d["t"])
        matched = set()
        lat = []
        tp = 0
        for d in dets:
            for gi, g in enumerate(items):
                if gi not in matched and g["t_start"] - TOL <= d["t"] <= g["t_end"] + TOL:
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
        out[t] = {"precision": round(prec, 2), "recall": round(rec, 2),
                  "avg_latency_sec": round(sum(lat) / len(lat), 2) if lat else None,
                  "false_positives": fp, "fn": fn}
        tp_total += tp
    out["overall"] = {"weighted_f1": round(f1w / len(gt), 2) if gt else 0.0,
                      "total_events": len(gt), "detected": tp_total}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", default=None)
    ap.add_argument("--profile", default="dev", choices=("dev", "exam"))
    a = ap.parse_args()
    try:
        cfg = load_config(a.config, a.profile)
    except ConfigError as e:
        raise SystemExit(str(e))
    gt = json.load(open(a.gt, encoding="utf-8"))
    fired = run(a.video, cfg)
    metrics = match(fired, gt)
    json.dump({"detections": fired, "metrics": metrics}, open(a.output, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(metrics, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
