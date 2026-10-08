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

TOL = 0.5


def run(video, cfg, calibration_mode="mandatory", timeline_out=None):
    if calibration_mode not in ("mandatory", "five"):
        raise ValueError("Replay требует полную раздельную калибровку головы и глаз")
    import cv2
    from proctor.core.pipeline import FramePacket
    from proctor.core.face_mesh import FaceMeshThread
    from proctor.core.detector_yolo import DetectorYolo
    from proctor.core.hands import HandsThread
    from proctor.core.mandatory_calibration import (
        MandatoryCalibration,
        calibration_complete,
    )
    from proctor.core.episodes import EpisodePolicy

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
        detail_model=yc.get("detail_model"),
        detail_imgsz=yc.get("detail_imgsz", 384),
        detail_fps=yc.get("detail_fps", 4.0),
        detail_search=yc.get("detail_search", True),
        desk_search=yc.get("desk_search", True),
    )
    face = FaceMeshThread(
        fc["max_faces"],
        max_width=fc.get("max_width", 640),
        min_detection_confidence=fc["min_detection_confidence"],
        min_tracking_confidence=fc["min_tracking_confidence"],
    )
    hands = HandsThread(yolo.output.peek) if fc.get("hands_enabled", True) else None
    yolo.hand_provider = hands.output.peek if hands else None
    workers = [yolo, face] + ([hands] if hands else [])
    eng = RuleEngine(cfg, {})
    calibration = MandatoryCalibration()
    pc = cfg.get("policy", {})
    policy = EpisodePolicy(
        pc.get("yellow_sec", 3.0),
        pc.get("red_sec", 5.0),
        phone_conf=pc.get("immediate_phone_conf", 0.55),
    )
    base = time.monotonic()
    calibration.start(base)
    next_yolo = next_face = next_hands = 0.0
    fired = []
    by_episode = {}
    i = 0
    try:
        for worker in workers:
            worker.setup()
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i / fps
            now = base + t
            packet = FramePacket(i, now, frame, t)
            i += 1
            sampled = False
            if t >= next_yolo:
                r = yolo.process(packet)
                yolo.output.put(r)
                eng.on_yolo(r)
                next_yolo = t + 1 / yolo.budget.target_fps
            if t >= next_face:
                result = face.process(packet)
                eng.on_face(result)
                sampled = True
                next_face = t + 1 / fc.get("target_fps", 15)
                if not calibration.done:
                    calibration.feed(result)
                    eng.preview_base = calibration.preview()
            if hands and t >= next_hands:
                result = hands.process(packet)
                hands.output.put(result)
                eng.on_hands(result)
                next_hands = t + 1 / fc.get("hands_fps", 5)
            if not calibration.done and calibration.advance(now):
                eng.calib = calibration.base
                eng.reset()
            ready = calibration_complete(eng.calib)
            eng.tick(now, directions_enabled=ready)
            if not ready:
                continue
            conditions = policy.observations(eng, now, ready)
            _, notices = policy.update(conditions, now, policy.ages(eng, now))
            for notice in notices:
                ident = notice["ident"]
                if ident not in by_episode:
                    row = dict(
                        type=notice["kind"],
                        t=round(t, 3),
                        level=notice["level"],
                        episode_id=ident,
                    )
                    by_episode[ident] = row
                    fired.append(row)
                else:
                    by_episode[ident]["level"] = notice["level"]
                by_episode[ident]["yellow_at" if notice["level"] == 1 else "red_at"] = (
                    round(t, 3)
                )
            if sampled and timeline_out is not None:
                d = eng.debug
                timeline_out.append(
                    dict(
                        t=round(t, 3),
                        head=d.get("head", "UNKNOWN"),
                        gaze=d.get("gaze", "UNKNOWN"),
                        faces=d.get("n_faces"),
                        phone=bool(conditions.get("PHONE_DETECTED")),
                        pose_valid=eng.face.pose_valid,
                        gaze_valid=eng.face.gaze_valid,
                    )
                )
        if not calibration_complete(eng.calib):
            raise ValueError(
                "Видео не прошло обязательные 30 с раздельной настройки головы/глаз"
            )
    finally:
        cap.release()
        for worker in workers:
            worker.teardown()
    return fired


def direction_metrics(timeline, segments):
    allowed = {"CENTER", "LEFT", "RIGHT", "UP", "DOWN"}
    out = {}
    for channel in ("head", "gaze"):
        rows = []
        for segment in segments:
            if channel not in segment:
                continue
            expected = segment[channel]
            if expected not in allowed:
                raise ValueError("Неверная метка направления")
            samples = [
                row
                for row in timeline
                if segment["t_start"] <= row["t"] < segment["t_end"]
            ]
            rows.append(
                dict(
                    expected=expected,
                    samples=len(samples),
                    correct=sum(row[channel] == expected for row in samples),
                    unknown=sum(row[channel] == "UNKNOWN" for row in samples),
                )
            )
        total = sum(r["samples"] for r in rows)
        out[channel] = dict(
            segments=rows,
            total_samples=total,
            correct_fraction=sum(r["correct"] for r in rows) / total if total else None,
        )
    return out


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
    ap.add_argument("--calibration", choices=("mandatory", "five"), default="mandatory")
    a = ap.parse_args()
    try:
        cfg = load_config(a.config, a.profile)
    except ConfigError as e:
        raise SystemExit(str(e))
    gt = json.load(open(a.gt, encoding="utf-8"))
    timeline = []
    fired = run(a.video, cfg, a.calibration, timeline)
    segments = gt.get("segments", []) if isinstance(gt, dict) else []
    events = gt.get("events", []) if isinstance(gt, dict) else gt
    metrics = match(fired, events)
    directions = direction_metrics(timeline, segments)
    json.dump(
        {
            "detections": fired,
            "metrics": metrics,
            "direction_metrics": directions,
            "timeline": timeline,
            "calibration": "mandatory 15+15s",
            "limitations": [
                "Offline deterministic replay, not realtime latency",
                "Accuracy needs independently labelled target-camera video",
                "OS guards are not exercised by video replay",
            ],
        },
        open(a.output, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
