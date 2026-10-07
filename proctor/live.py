import argparse, json, os, sys, time
from pathlib import Path
from PyQt6.QtCore import QTimer, QLockFile, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit
from proctor.core.camera import CameraThread
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.face_mesh import FaceMeshThread
from proctor.core.hands import HandsThread
from proctor.core.rules import RuleEngine
from proctor.core.calibration import CalibrationError
from proctor.core.mandatory_calibration import (
    MandatoryCalibration,
    calibration_complete,
)
from proctor.core.episodes import EpisodePolicy
from proctor.core.evidence import EvidenceRecorder
from proctor.ui.clip_viewer import ClipViewer
from proctor.core.config import load_config, ensure_hash_salt
from proctor.core.pipeline import hardware_profile, Timings
from proctor.core.overlay import render_overlay
from proctor.core.store import Store, EventWriter
from proctor.core.events import make_violation, SEVERITY
from proctor.ui.monitor_window import MonitorWindow, DIRECTIONS
from proctor.core.presentation import event_label
from proctor.guard.hotkeys import HotkeyGuard
from proctor.guard.focus import FocusWatch
from proctor.guard.processes import ProcWatch
from proctor.guard.clipboard import ClipboardGuard
from proctor.guard.security import FULL_GUARD, verify_password, guard_health


class Signals(QObject):
    event = pyqtSignal(str, object)
    exit = pyqtSignal()


def main():
    ap = argparse.ArgumentParser(description="Живой CV-прокторинг без страницы теста")
    ap.add_argument("--video")
    ap.add_argument("--config")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--perf", choices=("auto", "weak", "balanced"))
    ap.add_argument("--profile", choices=("dev", "exam"))
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--run-seconds", type=float, help=argparse.SUPPRESS)
    args = ap.parse_args()
    os.chdir(Path(__file__).resolve().parent.parent)
    cfg = load_config(args.config, args.profile, require_test_url=False)
    if cfg["profile"] == "exam":
        import ctypes

        if (
            args.no_guard
            or args.demo
            or args.video
            or args.run_seconds is not None
            or not FULL_GUARD
            or not ctypes.windll.shell32.IsUserAnAdmin()
        ):
            raise SystemExit(
                "Защищённый режим: Windows/admin, камера и включённая защита. Для CV без lock — dev."
            )
    if cfg["hash_chain"]["enabled"] and not cfg["hash_chain"]["salt_hex"]:
        ensure_hash_salt(cfg)
    import cv2

    cv2.setNumThreads(1)
    app = QApplication(sys.argv)
    app.setStyleSheet(Path("proctor/ui/style.qss").read_text())
    Path("data").mkdir(exist_ok=True)
    lock = QLockFile(str(Path("data/proctor.lock").resolve()))
    if not lock.tryLock(100):
        raise SystemExit("Прокторинг уже запущен")
    profile = hardware_profile(
        args.perf or cfg.get("performance", {}).get("mode", "auto")
    )
    adaptive = cfg.get("performance", {}).get("adaptive", True)
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
        min(yc["imgsz"], profile["imgsz"]) if adaptive else yc["imgsz"],
        yc["conf_phone"],
        yc["conf_person"],
        target_fps=min(yc["target_fps"], profile["yolo_fps"]),
        device=yc.get("device", "auto"),
        threads=profile["threads"],
        offline=yc.get("offline", True),
        adaptive=adaptive,
        detail_search=yc.get("detail_search", True),
        desk_search=yc.get("desk_search", True),
        phone_class=yc["phone_class"],
        person_class=yc["person_class"],
    )
    face = FaceMeshThread(
        fc["max_faces"],
        target_fps=min(fc.get("target_fps", 15), profile["face_fps"]),
        max_width=fc.get("max_width", 640),
        min_detection_confidence=fc["min_detection_confidence"],
        min_tracking_confidence=fc["min_tracking_confidence"],
        adaptive=adaptive,
    )
    hands = (
        HandsThread(yolo.output.peek, fc.get("hands_fps", 5))
        if fc.get("hands_enabled", True)
        else None
    )
    yolo.hand_provider = (lambda: hands.output.peek()) if hands else None
    workers = [yolo, face] + ([hands] if hands else [])
    for worker in workers:
        cam.subscribe(worker.push)
    engine = RuleEngine(cfg)
    timings = Timings()
    run = Path("data/sessions") / (
        time.strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid())
    )
    store = Store(
        str(run / "session.db"),
        str(run / "shots"),
        cfg["hash_chain"]["salt_hex"] if cfg["hash_chain"]["enabled"] else "",
    )
    writer = EventWriter(store)
    window = MonitorWindow()
    policy_cfg = cfg.get("policy", {})
    policy = EpisodePolicy(
        yellow=policy_cfg.get("yellow_sec", 3.0),
        red=policy_cfg.get("red_sec", 5.0),
        phone_conf=policy_cfg.get("immediate_phone_conf", 0.55),
    )
    record_cfg = cfg.get("evidence", {})
    recorder = EvidenceRecorder(
        store,
        run / "clips",
        source="video replay" if args.video else "camera",
        fps=record_cfg.get("fps", 5),
        pre=record_cfg.get("pre_sec", 2.0),
        post=record_cfg.get("post_sec", 2.0),
        segment=record_cfg.get("segment_sec", 15.0),
    )
    cam.subscribe(recorder.push)
    signals = Signals()
    started = time.monotonic()
    last_events = {}
    finished = False
    calibration = None
    calibration_mode = None
    calibration_error = ""
    ever_calibrated = False
    auto_attempted = False
    next_phase = None
    poll_versions = {w: -1 for w in workers}
    last_frame = -1
    last_ui = 0.0
    metrics_at = 0.0
    stage_text = "—"
    import psutil

    process = psutil.Process()
    process.cpu_percent(None)
    resource_text = "—"

    def event(kind, details=None, level=None, episode_id=""):
        if finished or kind not in SEVERITY:
            return
        if kind.startswith(("HEAD_", "GAZE_")) and (
            not calibration_complete(engine.calib) or calibration is not None
        ):
            return
        now = time.monotonic()
        if not episode_id and now - last_events.get(kind, -1e6) < 5:
            return
        last_events[kind] = now
        if not episode_id:
            pulse = policy.pulse(kind, now, min(2, SEVERITY[kind]))
            recorder.submit([pulse])
            episode_id = pulse["ident"]
            level = pulse["level"]
            details = dict(details or engine.debug, started=now)
        duration = (details or {}).get(
            "duration", engine.debug.get("durations", {}).get(kind, 0.0)
        )

        data = dict(details or engine.debug)
        data["calibrated"] = calibration_complete(engine.calib)
        data["episode_id"] = episode_id
        data["source"] = "video replay" if args.video else "camera"
        violation = make_violation(kind, duration, "", data)
        if level is not None:
            violation.severity = level
        if episode_id:
            violation.t_start = float(data["started"]) + recorder.wall_offset
        packet = cam.output.peek()
        data["frame_age_ms"] = (now - packet.captured_at) * 1000 if packet else None
        frame = (
            packet.frame
            if cfg["store"]["save_screenshots"]
            and packet
            and now - packet.captured_at < 0.7
            else None
        )
        writer.submit(violation, frame)
        window.log(
            f'{time.strftime("%H:%M:%S")} · '
            + ("КРАСНЫЙ · " if level == 2 else "ЖЁЛТЫЙ · " if level == 1 else "")
            + f"{event_label(kind)} · {duration:.1f} с",
            level,
            episode_id,
        )
        if level == 2:
            QApplication.beep()

    signals.event.connect(event)
    guard_on = not args.no_guard and not args.demo and cfg["profile"] == "exam"
    hk = HotkeyGuard(
        cfg["guard"]["hotkeys"],
        cfg["guard"]["exit_combo"],
        on_block=lambda key: signals.event.emit("HOTKEY_BLOCKED", {"key": key}),
        on_exit=signals.exit.emit,
        enabled=guard_on,
    )
    fw = FocusWatch(lambda: int(window.winId()), enabled=guard_on and os.name == "nt")

    native_id = int(window.winId())
    fw.get_hwnd = lambda: native_id
    pw = ProcWatch(
        cfg["guard"]["forbidden_processes"],
        cfg["guard"]["process_check_sec"],
        cfg["guard"]["process_mode"],
    )
    clipboard = ClipboardGuard(
        app, cfg["guard"]["clipboard_clear_sec"], lambda kind: event(kind)
    )
    fw.lost.connect(lambda: event("FOCUS_LOST"))
    pw.found.connect(lambda name: event("FORBIDDEN_PROCESS", {"process": name}))
    if guard_on:
        if not hk.start():
            hk.stop()
            recorder.stop()
            writer.stop()
            store.close()
            lock.unlock()
            raise SystemExit(
                "Не удалось активировать защиту клавиш: " + ", ".join(hk.errors)
            )
        fw.start()
        pw.start()
        clipboard.start()
        window.locked = True

    def exit_requested():
        if window.locked:
            fw.enabled = False
            password, ok = QInputDialog.getText(
                window, "Выход экзаменатора", "Пароль:", QLineEdit.EchoMode.Password
            )
            ex = cfg["guard"]["examiner"]
            fw.enabled = True
            if not ok or not verify_password(
                password, ex["salt_hex"], ex["hash_hex"], ex["iterations"]
            ):
                return
        window.locked = False
        app.quit()

    signals.exit.connect(exit_requested)
    window.finish.connect(exit_requested)
    window.phone_threshold.setValue(yc["conf_phone"])
    window.phone_threshold.valueChanged.connect(
        lambda value: (
            setattr(yolo, "requested_confidence", float(value)),
            yc.update(conf_phone=float(value)),
        )
    )
    window.gaze_hold.setValue(policy.yellow)
    window.down_hold.setValue(policy.red)

    def update_policy():
        yellow, red = window.gaze_hold.value(), window.down_hold.value()
        if yellow >= red:
            window.banner.setText(
                "Жёлтый порог должен быть меньше красного. Изменение не применено."
            )
            return
        policy.yellow, policy.red = yellow, red

    window.gaze_hold.valueChanged.connect(update_policy)
    window.down_hold.valueChanged.connect(update_policy)
    window.yaw_threshold.setValue(cfg["rules"]["gaze_side"]["yaw_thresh"])
    window.pitch_threshold.setValue(cfg["rules"]["gaze_down"]["pitch_thresh"])
    window.yaw_threshold.valueChanged.connect(
        lambda value: (
            cfg.update(head_threshold_override=True),
            cfg["rules"]["gaze_side"].update(yaw_thresh=value),
        )
    )
    window.pitch_threshold.valueChanged.connect(
        lambda value: (
            cfg.update(head_threshold_override=True),
            cfg["rules"]["gaze_down"].update(pitch_thresh=value),
        )
    )

    def start_calibration(mode):
        nonlocal calibration, calibration_mode, next_phase, auto_attempted
        if window.locked and ever_calibrated:
            fw.enabled = False
            password, ok = QInputDialog.getText(
                window,
                "Повторная настройка",
                "Пароль экзаменатора:",
                QLineEdit.EchoMode.Password,
            )
            fw.enabled = True
            ex = cfg["guard"]["examiner"]
            if not ok or not verify_password(
                password, ex["salt_hex"], ex["hash_hex"], ex["iterations"]
            ):
                return
        if face.status != "ready":
            window.banner.setText(
                "FaceMesh ещё не готов: " + (face.error or face.status)
            )
            return
        calibration = MandatoryCalibration()
        auto_attempted = True
        engine.calib = {}
        engine.preview_base = {}
        calibration.start(time.monotonic())
        calibration_mode = mode
        next_phase = None
        engine.reset()
        recorder.submit(policy.close())

    window.calibrate.connect(start_calibration)
    if guard_on:
        for control in (
            window.phone_threshold,
            window.gaze_hold,
            window.down_hold,
            window.yaw_threshold,
            window.pitch_threshold,
        ):
            control.setEnabled(False)

    def poll():
        nonlocal last_frame, last_ui, calibration, calibration_mode, next_phase, metrics_at, stage_text, resource_text, ever_calibrated, auto_attempted, calibration_error
        now = time.monotonic()
        for worker in workers:
            result = worker.output.peek()
            if result is None or result.seq == poll_versions[worker]:
                continue
            poll_versions[worker] = result.seq
            for name, value in getattr(result, "timings", {}).items():
                timings.add(name, value)
            if worker is face:
                engine.on_face(result)
                if calibration and now - result.captured_at < 0.5:
                    calibration.feed(result)
                    engine.preview_base = calibration.preview()
            elif worker is yolo:
                engine.on_yolo(result)
            else:
                engine.on_hands(result)
        if (
            not auto_attempted
            and face.status == "ready"
            and engine.face.n_faces == 1
            and engine.face.pose_valid
        ):
            start_calibration("five")
        if calibration:
            pose = calibration.phase(now)
            phase = (calibration.stage, pose)
            if phase != next_phase:
                next_phase = phase
                window.show_target(pose)
                QApplication.beep()
            window.banner.setText(
                "Обязательная настройка: "
                + (
                    "пять поворотов головы"
                    if calibration.stage == "head"
                    else "пять направлений глаз; голова прямо"
                )
            )
            window.calibration_running(
                pose,
                calibration.remaining(now),
                calibration.duration,
                calibration.stage,
            )
            try:
                if calibration.advance(now):
                    engine.calib = calibration.base
                    engine.reset()
                    ever_calibrated = True
                    calibration_error = ""
                    message = "Готово: голова и глаза различают все направления. Требуется проверка реальных сценариев."
                    window.log(message)
                    window.calibration_finished(message)
                    calibration = None
                    window.target.hide()
                    QApplication.beep()
            except CalibrationError as exc:
                engine.calib = {}
                engine.reset()
                calibration = None
                calibration_error = str(exc)
                message = (
                    "Экзамен не готов: " + calibration_error + ". Повторите настройку."
                )
                window.log(message)
                window.calibration_finished(message)
                window.target.hide()
        if face.status != "ready" or face.output.peek() is None:
            engine.face.error = face.error or "FaceMesh loading/unavailable"
        if yolo.status != "ready" or yolo.output.peek() is None:
            engine.yolo.error = yolo.error or "YOLO loading/unavailable"
        s = time.perf_counter()
        ready = calibration_complete(engine.calib) and calibration is None
        engine.tick(now, directions_enabled=ready)
        engine.debug["calibrated"] = ready
        engine.debug["immediate_phone_conf"] = max(policy.phone_conf, yolo.conf_phone)
        engine.debug.update(
            head=engine.debug["head_display"], gaze=engine.debug["gaze_display"]
        )
        timings.add("logic", time.perf_counter() - s)
        conditions = policy.observations(engine, now, ready)

        for key in ("NO_FACE", "MULTI_FACE", "CAMERA_COVERED"):
            conditions[key] = (
                conditions.get(key, False) and ever_calibrated and calibration is None
            )
        changes, notices = policy.update(conditions, now, policy.ages(engine, now))
        recorder.submit(changes)
        for row in changes:
            if not row["closed"] and row["level"] == 0 and row["duration"] == 0:
                window.log(
                    time.strftime("%H:%M:%S")
                    + " · Запись: "
                    + event_label(row["kind"]),
                    0,
                    row["ident"],
                )
        for row in notices:
            event(
                row["kind"],
                dict(row, measurements=engine.debug),
                row["level"],
                row["ident"],
            )
        engine.debug["durations"] = {k: e.duration for k, e in policy.active.items()}
        engine.debug["warnings_active"] = [
            k for k, e in policy.active.items() if e.level > 0
        ]
        if (
            not args.video
            and now - started > 3
            and (cam.output.peek() is None or now - cam.output.peek().captured_at > 1)
        ):
            event("CAMERA_LOST", {"status": cam.status, "error": cam.error})
        packet = cam.output.peek()
        if packet and packet.seq != last_frame:
            last_frame = packet.seq
            s = time.perf_counter()
            vis = render_overlay(
                packet,
                engine.face,
                engine.yolo,
                engine.hands,
                cfg["camera"]["mirror_preview"],
                states=engine.debug,
            )
            window.show_frame(vis)
            timings.add("ui_render", time.perf_counter() - s)
            timings.add("preview_age", time.monotonic() - packet.captured_at)
        if now - last_ui < 0.15:
            return
        last_ui = now
        d = engine.debug
        f = engine.face
        y = engine.yolo
        ff = face.status == "ready" and f.seq >= 0 and engine.fresh(f, now)
        yf = yolo.status == "ready" and y.seq >= 0 and engine.fresh(y, now)
        face_text = str(f.n_faces) if ff else "UNKNOWN"
        phone_text = (
            "CONFIRMED"
            if yf and conditions["PHONE_DETECTED"]
            else (
                "CANDIDATE"
                if yf and y.candidates
                else ("НЕ НАБЛЮДАЕТСЯ" if yf else "UNKNOWN")
            )
        )
        phone_display = {
            "CONFIRMED": "подтверждён",
            "CANDIDATE": "кандидат, проверяем",
            "НЕ НАБЛЮДАЕТСЯ": "не обнаружен",
            "UNKNOWN": "нет свежих данных",
        }[phone_text]
        window.set_observation(
            (
                (
                    "не обнаружено"
                    if f.n_faces == 0
                    else (
                        "ученик в кадре"
                        if f.n_faces == 1
                        else f"{f.n_faces} · возможен второй человек"
                    )
                )
                if ff
                else "нет свежих данных"
            ),
            phone_display,
        )
        window.show_directions(
            d.get("head"),
            d.get("gaze"),
            ready,
            preview=d.get("preview_ready", False),
            head_calibrated=ready and d.get("head_display_calibrated", False),
            gaze_calibrated=ready and d.get("gaze_display_calibrated", False),
        )
        labels = (
            "ЗЕЛЁНЫЙ · активных предупреждений нет",
            "ЖЁЛТЫЙ · требуется наблюдение",
            "КРАСНЫЙ · требуется проверка экзаменатором",
        )
        window.signal_badge.setText(
            labels[policy.level] + (" · калибровка не завершена" if not ready else "")
            if policy.level
            else labels[0] if ready else "Калибровка обязательна · экзамен не готов"
        )
        window.signal_badge.setStyleSheet(
            "color: "
            + (
                ("#91cdaa", "#f4c36f", "#ff8c8c")[policy.level]
                if ready or policy.level
                else "#a5b3c9"
            )
        )
        window.highlight_gaze(
            bool(engine.calib)
            and calibration is None
            and any(k.startswith("GAZE_") for k in d.get("warnings_active", []))
        )
        if d.get("gaze") == "UNKNOWN":
            window.gaze_card.note.setText(f.gaze_reason or "Нет свежего измерения глаз")
            window.gaze_card.note.show()
        seconds = int(now - started)
        window.session_clock.setText(f"{seconds//60:02}:{seconds%60:02}")
        protection_ready, protection_errors = guard_health(
            hk, fw, pw, clipboard, now, required=guard_on
        )
        guard_workers = guard_on and protection_ready
        if guard_on and not protection_ready:
            event("GUARD_LOST", {"errors": protection_errors})
        window.mode_label.setText(
            "Режим защиты"
            if guard_workers
            else (
                "Защита неполная / запускается"
                if guard_on
                else "Просмотр CV · защита выключена"
            )
        )
        errors = [
            type(w).__name__ + ": " + w.error for w in workers if w.status == "error"
        ]
        if not calibration:
            source = "Видео · не физическая камера" if args.video else "Камера"
            healthy = (
                face.status == "ready"
                and yolo.status == "ready"
                and packet is not None
                and now - packet.captured_at < 1
            )
            window.banner.setText(
                " · ".join(errors)
                if errors
                else source
                + " · "
                + (
                    "Наблюдение активно"
                    if healthy
                    else "Подготовка / нет свежего кадра"
                )
                + (
                    " · голова и глаза настроены"
                    if ready
                    else " · обязательная настройка головы и глаз не завершена"
                )
            )
        t = d.get("thresholds", {})
        left = "—" if f.left_eye is None else f"{f.left_eye[0]:.3f}/{f.left_eye[1]:.3f}"
        right = (
            "—" if f.right_eye is None else f"{f.right_eye[0]:.3f}/{f.right_eye[1]:.3f}"
        )
        pmax = max((p.conf for p in y.candidates), default=None)
        head_numbers = (
            f'{d.get("dyaw",0):+.1f}° / {d.get("dpitch",0):+.1f}°; roll {f.roll:+.1f}°'
            if ff and f.pose_valid
            else "UNKNOWN — нет валидной геометрии"
        )
        gaze_numbers = (
            f'{d.get("gaze_dx",0):+.3f} / {d.get("gaze_dy",0):+.3f}'
            if ff and f.gaze_valid
            else "UNKNOWN — зрачки не читаются"
        )
        gates = d.get("gaze_entry_gates", {})
        gate_text = " / ".join(
            f"{gates[k]:.3f}" if k in gates else "—"
            for k in ("LEFT", "RIGHT", "UP", "DOWN")
        )
        confidence = (
            f"{pmax:.2f}" if pmax is not None and yf else "нет свежего кандидата"
        )
        recovery = any(
            p.confirmed
            and p.observed
            and p.conf < yolo.conf_phone
            and p.strong_at is not None
            and now - p.strong_at <= 0.8
            for p in y.phones
        )
        support = ", ".join(
            sorted(
                {
                    p.support_kind
                    for p in y.phones
                    if p.observed and p.confirmed and p.support_kind
                }
            )
        )
        window.measurements.setText(
            f"HEAD Δ yaw / pitch: {head_numbers}; PnP + измеренный центр (не метрическая точность)\n"
            f"GAZE Δ H / V: {gaze_numbers}; глаз L {left}, R {right}\n"
            f'HEAD вход: yaw {t.get("yaw")}° / pitch {t.get("pitch")}°; выход 70% порога\n'
            f"GAZE вход L/R/U/D: {gate_text}\n"
            f"Phone P={confidence}; новый track ≥ {yolo.conf_phone:.2f}; imgsz {yolo.budget.imgsz}"
            + (" · weak recovery: strong ≤0.8с" if recovery else "")
            + (" · подтверждение: " + support if support else "")
            + "\n"
            f'ROI: {y.detail_region or "не запускалась в этом цикле"}; размер {y.detail_inference_size or "—"}; всего запусков {yolo.detail_calls}\n'
            f'HEAD quality: {"valid" if f.pose_valid and ff else f.pose_reason or "нет свежего измерения"}; GAZE: {f.gaze_reason or "нет измерения"}'
        )
        active = list(policy.active.values())
        direction_active = [e for e in active if e.kind.startswith(("HEAD_", "GAZE_"))]
        if calibration is None and active:
            ep = max(direction_active or active, key=lambda e: e.duration)
            phone_now = ep.kind == "PHONE_DETECTED"
            window.progress.setValue(
                100 if phone_now else min(100, int(100 * ep.duration / policy.red))
            )
            window.progress.setFormat(
                "Телефон: немедленный сигнал"
                if phone_now
                else f"{event_label(ep.kind)}: {ep.duration:.1f} с · жёлтый {policy.yellow:.1f} / красный {policy.red:.1f} с"
            )
        elif calibration is None:
            window.progress.setValue(0)
            window.progress.setFormat("Нет активного отвода / предупреждения")

        def state_for(key):
            ep = policy.active.get(key)
            return (
                ("АКТИВНО" if ep else "не наблюдается")
                + f" · {ep.duration if ep else 0.:.1f} с"
                + (
                    " · сразу"
                    if key == "PHONE_DETECTED"
                    else (
                        " · футаж, без отдельного таймера тревоги"
                        if key.startswith("PHONE_")
                        else f" · {policy.yellow:.0f}/{policy.red:.0f} с"
                    )
                )
            )

        window.set_case("PHONE", phone_text)
        for item, kind in (
            ("HAND", "PHONE_IN_HAND"),
            ("LIFT", "PHONE_LIFTED"),
            ("RAISED", "PHONE_RAISED"),
            ("AIM", "PHONE_AIMED"),
        ):
            available = yf and (
                item != "HAND"
                or hands is not None
                and hands.status == "ready"
                and engine.fresh(engine.hands, now, 0.5)
            )
            if item in ("RAISED", "AIM"):
                available = available and ff and f.face_box is not None
            window.set_case(
                item,
                (state_for(kind) if available else "UNKNOWN: модуль/данные недоступны")
                + (" · эвристика" if item == "AIM" else ""),
            )
        window.set_case(
            "HEAD",
            DIRECTIONS[d["head"]]
            + (
                " · независимо от глаз"
                if ready
                else " · предварительно, калибровка не принята"
            ),
        )
        window.set_case(
            "GAZE",
            DIRECTIONS[d["gaze"]]
            + (
                " · независимые глаза"
                if ready and d.get("gaze_display_calibrated")
                else " · предварительно, без gaze-тревог"
            ),
        )
        window.set_case(
            "DOWN",
            (
                state_for("GAZE_DOWN")
                if ready and f.gaze_valid and ff
                else "UNKNOWN: требуется центр / читаемые глаза"
            ),
        )
        window.set_case(
            "SIDE",
            (
                state_for("GAZE_LEFT") + " / " + state_for("GAZE_RIGHT")
                if ready and f.gaze_valid and ff
                else "UNKNOWN: требуется центр / читаемые глаза"
            ),
        )
        window.set_case(
            "PRESENCE",
            "Лиц: "
            + face_text
            + (" · landmarks потеряны" if ff and not f.pose_valid else ""),
        )
        window.set_case(
            "MULTI", state_for("MULTI_FACE") if ff else "UNKNOWN: лицо не измерено"
        )
        guard_status = (
            "hook активен" if hk.status == "active" else "НЕ АКТИВНО: " + hk.status
        )
        for key in ("ALT", "WIN", "SHOT"):
            window.set_case(
                key,
                guard_status
                + (" · не все способы screenshot" if key == "SHOT" else ""),
            )
        window.set_case(
            "COPY",
            guard_status
            + (" · clipboard active" if guard_on else " · буфер не блокируется в dev"),
        )
        window.set_case(
            "WINDOWS",
            (
                (
                    "Потоки фокуса/процессов работают; только новые процессы, не полный kiosk"
                    if fw.isRunning() and pw.isRunning()
                    else "НЕ АКТИВНО: worker защиты остановлен / запускается"
                )
                if guard_on
                else "НЕ АКТИВНО в CV/dev"
            ),
        )
        window.set_case(
            "TABS", "Нет браузера в этом режиме. Защита вкладок — --embedded-test"
        )
        age = lambda r: f"{(now-r.captured_at)*1000:.0f}ms" if r.seq >= 0 else "—"
        camera_rate = cam.camera_fps if packet and now - packet.captured_at < 1 else 0.0
        if now - metrics_at >= 1:
            metrics_at = now
            resource_text = f"CPU процесса: {process.cpu_percent(None):.0f}% (может быть >100% на нескольких ядрах); RAM: {process.memory_info().rss/1024**2:.0f} MiB"
            stats = timings.snapshot()
            stage_text = " · ".join(
                name + ": " + f'{stats[name]["mean_ms"]:.1f}ms'
                for name in (
                    "preview_age",
                    "yolo",
                    "tracker",
                    "face_scan",
                    "mediapipe",
                    "head_pose",
                    "eye_gaze",
                    "hands",
                    "logic",
                    "ui_render",
                )
                if name in stats
            )
        window.performance.setText(
            f"Реально: Camera {camera_rate:.1f}fps · захват {cam.read_ms:.1f}ms, YOLO {yolo.actual_fps if yf else 0.:.1f}Hz, Face {face.actual_fps if ff else 0.:.1f}Hz\n"
            f"Возраст данных: YOLO {age(y)}, Face {age(f)}. Пропущено кадров YOLO {yolo.skipped_frames} / Face {face.skipped_frames}\n"
            f"Среднее время этапов: {stage_text}\n{resource_text}\n"
            f"Футаж: {recorder.clips} фрагментов · ошибок {recorder.failures}"
            + (" · " + recorder.error if recorder.error else "")
            + "\n"
            + f"Сессия {(now-started):.0f}с · очередь записи: {writer.queue.unfinished_tasks} · "
            + ("Ошибка записи: " + writer.error if writer.error else "БД локальная")
        )

    def open_evidence(item):
        from PyQt6.QtCore import Qt

        ident = item.data(Qt.ItemDataRole.UserRole)
        if not ident:
            return
        fw.enabled = False
        try:
            if window.locked:
                password, ok = QInputDialog.getText(
                    window, "Футаж экзаменатора", "Пароль:", QLineEdit.EchoMode.Password
                )
                ex = cfg["guard"]["examiner"]
                if not ok or not verify_password(
                    password, ex["salt_hex"], ex["hash_hex"], ex["iterations"]
                ):
                    return
            clips = store.clips_for(ident)
            if not clips:
                window.banner.setText(
                    "Футаж ещё записывается или недоступен: " + recorder.error
                )
                return
            ClipViewer(clips, run / "clips", window).exec()
        finally:
            fw.enabled = guard_on

    window.feed.itemDoubleClicked.connect(open_evidence)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(33)
    for w in workers:
        w.start()
    cam.start()
    window.show()
    if window.locked:
        window.showFullScreen()
    if args.run_seconds:
        QTimer.singleShot(int(args.run_seconds * 1000), app.quit)
    cleaned = False

    def cleanup():
        nonlocal cleaned, finished
        if cleaned:
            return
        cleaned = True
        finished = True
        timer.stop()
        hk.stop()
        fw.stop()
        pw.stop()
        clipboard.stop()
        for w in [cam] + workers:
            if not w.stop():
                w.wait()
        recorder.submit(policy.close())
        recorder.stop()
        writer.stop()
        (run / "timings.json").write_text(json.dumps(timings.snapshot(), indent=2))
        (run / "calibration.json").write_text(json.dumps(engine.calib, indent=2))
        (run / "runtime.json").write_text(
            json.dumps(
                dict(
                    source="video" if args.video else "camera",
                    phone_confidence=yolo.conf_phone,
                    imgsz=yolo.budget.imgsz,
                    frames_camera=cam.seq,
                    frames_yolo=yolo.processed_count,
                    frames_face=face.processed_count,
                    head=engine.debug.get("head"),
                    gaze=engine.debug.get("gaze"),
                    episodes=len(store.episode_rows()),
                    clips=recorder.clips,
                    evidence_errors=recorder.failures,
                    policy=dict(yellow_sec=policy.yellow, red_sec=policy.red),
                    case_status={
                        key: window.checklist.item(row, 1).text()
                        for key, row in window.rows.items()
                    },
                    calibration=engine.calib.get("mode"),
                    preview_ready=engine.debug.get("preview_ready", False),
                    pose_method=engine.face.pose_method,
                    calibration_error=calibration_error,
                    guard=hk.status,
                    immediate_phone_conf=max(policy.phone_conf, yolo.conf_phone),
                    gaze_entry_gates=engine.debug.get("gaze_entry_gates"),
                    detail_calls=yolo.detail_calls,
                    preview=dict(
                        image_size=[
                            window.camera.image_rect.width(),
                            window.camera.image_rect.height(),
                        ],
                        window_size=[window.width(), window.height()],
                    ),
                    phone_evidence=[
                        dict(
                            conf=p.conf,
                            support=p.support_kind,
                            observed=p.observed,
                            confirmed=p.confirmed,
                        )
                        for p in engine.yolo.phones
                    ],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        store.close()
        lock.unlock()

    app.aboutToQuit.connect(cleanup)
    try:
        return app.exec()
    finally:
        cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
