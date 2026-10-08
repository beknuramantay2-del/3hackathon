import argparse
import os
import sys
import time
import json
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--config")
    ap.add_argument("--perf", choices=("auto", "weak", "balanced"), default="auto")
    ap.add_argument("--output", default="data/benchmark.json")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.seconds <= 0:
        ap.error("seconds must be positive")
    if not args.preview:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = Path(__file__).resolve().parents[2]
    fingerprint = hashlib.sha256()
    for path in sorted(
        list((root / "proctor/core").glob("*.py"))
        + list((root / "proctor/ui").glob("*.py"))
        + [Path(__file__).resolve()]
    ):
        fingerprint.update(str(path.relative_to(root)).encode())
        fingerprint.update(path.read_bytes())
    import cv2
    import psutil
    from PyQt6.QtWidgets import QApplication
    from proctor.core.config import load_config
    from proctor.core.camera import CameraThread
    from proctor.core.overlay import render_overlay
    from proctor.core.detector_yolo import DetectorYolo
    from proctor.core.face_mesh import FaceMeshThread
    from proctor.core.hands import HandsThread
    from proctor.core.rules import RuleEngine
    from proctor.core.pipeline import Timings, hardware_profile
    from proctor.ui.screens import SidePanel

    cfg = load_config(args.config)
    profile = hardware_profile(args.perf)
    app = QApplication(["proctor-benchmark"])
    panel = SidePanel()
    if args.preview:
        panel.show()
    cv2.setNumThreads(1)
    cam = CameraThread(
        cfg["camera"]["width"],
        cfg["camera"]["height"],
        args.video,
        cfg["camera"]["index"],
        fps=cfg["camera"]["fps"],
    )
    yc, fc = cfg["yolo"], cfg["face"]
    yolo = DetectorYolo(
        yc["model"],
        min(yc["imgsz"], profile["imgsz"]),
        yc["conf_phone"],
        yc["conf_person"],
        target_fps=min(yc["target_fps"], profile["yolo_fps"]),
        threads=min(cfg.get("performance", {}).get("threads", 2), profile["threads"]),
        device=yc.get("device", "auto"),
        offline=True,
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
        target_fps=min(fc.get("target_fps", 15), profile["face_fps"]),
        max_width=fc.get("max_width", 640),
        min_detection_confidence=fc["min_detection_confidence"],
        min_tracking_confidence=fc["min_tracking_confidence"],
    )
    hands = (
        HandsThread(yolo.output.peek, min(fc.get("hands_fps", 5), profile["hands_fps"]))
        if fc.get("hands_enabled", True)
        else None
    )
    yolo.hand_provider = hands.output.peek if hands else None
    workers = [yolo, face] + ([hands] if hands else [])
    for w in workers:
        cam.subscribe(w.push)
        w.start()
    eng = RuleEngine(cfg)
    timings = Timings(10000)
    seen = {w: -1 for w in workers}
    frames, yolo_frames, face_frames, events = 0, 0, 0, {}
    process = psutil.Process()
    process.cpu_percent()
    cpu, rss = [], []
    started = time.monotonic()
    ready_at = None
    cam.start()
    seq = -1
    error = ""
    measurement_end = None
    camera_seq_start = 0
    try:
        while True:
            now = time.monotonic()
            app.processEvents()
            if any(w.status == "error" for w in workers):
                error = " · ".join(w.error for w in workers if w.status == "error")
                break
            if ready_at is None and all(w.status == "ready" for w in workers):
                ready_at = now
                process.cpu_percent()
                camera_seq_start = cam.seq
            if now - started > 120 and ready_at is None:
                error = "Model startup timeout"
                break
            if ready_at is not None and now - ready_at >= args.seconds:
                break
            if cam.status in ("error", "eof"):
                if frames == 0:
                    error = cam.error
                break
            for w in workers:
                r = w.output.peek()
                if (
                    r is None
                    or seen[w] == r.seq
                    or ready_at is None
                    or r.captured_at < ready_at
                ):
                    continue
                seen[w] = r.seq
                for name, elapsed in getattr(r, "timings", {}).items():
                    timings.add(name, elapsed)
                timings.add(
                    type(w).__name__ + "_frame_age", max(0, now - r.captured_at)
                )
                if w is yolo:
                    yolo_frames += 1
                    eng.on_yolo(r)
                elif w is face:
                    face_frames += 1
                    eng.on_face(r)
                else:
                    eng.on_hands(r)
            if ready_at is None:
                time.sleep(0.005)
                continue
            s = time.perf_counter()

            for kind in eng.tick(now, directions_enabled=False):
                events[kind] = events.get(kind, 0) + 1
            timings.add("logic", time.perf_counter() - s)
            ages = [
                now - r.captured_at
                for w in (face, yolo)
                if (r := w.output.peek()) is not None and r.captured_at >= ready_at
            ]
            if len(ages) == 2:
                timings.add("decision_data_age", max(ages))
            packet = cam.output.peek()
            if packet and packet.seq != seq and packet.captured_at >= ready_at:
                seq = packet.seq
                frames += 1
                timings.add("camera_read", cam.read_ms / 1000)
                s = time.perf_counter()
                panel.show_frame(
                    render_overlay(
                        packet,
                        eng.face,
                        eng.yolo,
                        eng.hands,
                        cfg["camera"]["mirror_preview"],
                    )
                )
                timings.add("ui_render", time.perf_counter() - s)
                timings.add("preview_latency", time.monotonic() - packet.captured_at)
                if frames % 30 == 0:
                    cpu.append(process.cpu_percent())
                    rss.append(process.memory_info().rss / 1024**2)
            time.sleep(0.005)
    finally:
        measurement_end = time.monotonic()
        for w in [cam] + workers:
            if not w.stop():
                w.wait()
    duration = max(0.001, measurement_end - (ready_at or started))
    summary = dict(
        code_fingerprint_sha256=fingerprint.hexdigest(),
        source=args.video or "camera",
        synthetic="synthetic" in str(args.video or "").lower(),
        profile=args.perf,
        startup_seconds=(ready_at - started) if ready_at else None,
        measured_seconds=duration,
        camera_frames=cam.seq - camera_seq_start,
        camera_fps=(cam.seq - camera_seq_start) / duration,
        rendered_frames=frames,
        render_fps=frames / duration,
        yolo_fps=yolo_frames / duration,
        face_fps=face_frames / duration,
        timings=timings.snapshot(),
        diagnostic_events_uncalibrated=events,
        cpu_process_percent=sum(cpu) / len(cpu) if cpu else None,
        rss_peak_mb=max(rss) if rss else process.memory_info().rss / 1024**2,
        effective_imgsz=yolo.budget.imgsz,
        effective_yolo_hz=yolo.budget.target_fps,
        effective_face_hz=face.budget.target_fps,
        error=error,
        host=dict(cpu_count=os.cpu_count(), platform=sys.platform),
        limitations=[
            "sensor exposure latency not measured",
            "no accuracy claim without labelled videos",
            "UI+overlay conversion measured in the standalone proctoring window",
        ],
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 2 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
