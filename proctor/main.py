"""main.py — one process, Qt signals only for widgets.
Usage: python main.py [--no-guard] [--demo] [--video file.mp4]"""
import sys, os, time, argparse, traceback
import yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import QTimer

from proctor.core.camera import CameraThread
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.face_mesh import FaceMeshThread
from proctor.core.calibration import Calibration
from proctor.core.rules import RuleEngine
from proctor.core.events import EventBus, make_violation
from proctor.core.store import Store
from proctor.guard.hotkeys import HotkeyGuard
from proctor.guard.focus import FocusWatch
from proctor.guard.processes import ProcWatch
from proctor.guard.displays import monitor_count
from proctor.guard.clipboard import ClipboardGuard
from proctor.core.preflight import check_camera
from proctor.ui.screens import PreflightScreen, CalibrationView
from proctor.ui.test_window import TestWindow
from proctor.report.generator import generate

from proctor.core.config import load_config, ensure_hash_salt, ConfigError
from proctor.core.strings import load_strings
from proctor.core.trust_score import TrustCalculator
from proctor.core.examiner_server import ExaminerServer

def load_cfg(profile=None):
    return load_config(profile=profile)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-guard", action="store_true")
    ap.add_argument("--demo", action="store_true", help="don't kill processes")
    ap.add_argument("--video", default=None)
    ap.add_argument("--debug", action="store_true", help="debug overlay: FPS/yaw/pitch/faces/phone conf")
    ap.add_argument("--profile", default=None, choices=("dev", "exam"))
    a = ap.parse_args()
    try:
        cfg = load_cfg(a.profile)
        texts = load_strings()
    except (ConfigError, RuntimeError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(2)
    hc = cfg.get("hash_chain", {}) or {}
    if hc.get("enabled") and not hc.get("salt_hex"):
        try:
            ensure_hash_salt(cfg)
        except ConfigError as e:
            print(e, file=sys.stderr)
            raise SystemExit(2)
    app = QApplication(sys.argv)
    try:
        qss = os.path.join(os.path.dirname(__file__), "ui", "style.qss")
        if os.path.exists("proctor/ui/style.qss"):
            qss = "proctor/ui/style.qss"
        with open(qss, encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception as e:
        print("[ui] style.qss:", e)
    guard_on = not a.no_guard
    fio = {"name": ""}

    _hc = cfg.get("hash_chain", {}) or {}
    _salt = _hc.get("salt_hex", "") if _hc.get("enabled") else ""
    store = Store(cfg["store"]["db"], cfg["store"]["shots"], salt=_salt)
    bus = EventBus()
    trust_calc = TrustCalculator(cfg["trust_weights"])
    session = {"status": texts["examiner_active"], "done": False, "fio": fio["name"]}
    try:
        examiner = ExaminerServer(cfg["examiner"]["host"], cfg["examiner"]["port"],
                                  cfg["store"]["db"], cfg["store"]["shots"],
                                  cfg["trust_weights"],
                                  lambda: session["fio"] or fio.get("name", ""),
                                  lambda: session["status"])
        url = examiner.start()
        print(f"examiner panel: {url}")
    except Exception as e:
        examiner = None
        print(f"examiner panel unavailable: {e}")
    calib = Calibration(cfg["calibration"]["duration_sec"])
    engine = RuleEngine(cfg, {})
    calib_mode = {"on": False}

    # threads
    cam = CameraThread(width=cfg["camera"]["width"], height=cfg["camera"]["height"],
                       video_file=a.video, index=cfg["camera"]["index"], parent=app)
    yolo = DetectorYolo(cfg["yolo"]["model"], cfg["yolo"]["imgsz"],
                        cfg["yolo"]["conf_phone"], cfg["yolo"]["conf_person"],
                        cfg["yolo"]["vote_window"], cfg["yolo"]["vote_threshold"], parent=app)
    face = FaceMeshThread(cfg["face"]["max_faces"], parent=app)

    stack = QStackedWidget()
    fps_hist = {"n": 0, "t0": time.time()}

    # --- violation plumbing ---
    test_win = {"w": None}
    def emit_violation(vtype, details=None):
        frame = cam.latest
        shot = ""
        if cfg["store"]["save_screenshots"] and frame is not None:
            shot = store.save_frame(frame, vtype)
        v = make_violation(vtype, 0.0, shot, details or {})
        store.add(v)
        trust_calc.apply_violation(v)
        bus.emit(v)
        if test_win["w"]:
            test_win["w"].panel.set_status(f"{texts['watch_fixed']}: {vtype.split(':')[0]}")

    def on_rule_types(types):
        for t in types:
            emit_violation(t, engine.debug)

    # wire CV
    latest_yolo_voted = {"v": False}
    def on_frame(f):
        yolo.push(f); face.push(f)
        fps_hist["n"] += 1
    def on_face_res(r):
        engine.on_face(r)
        if calib_mode["on"]:
            calib.add(r.yaw, r.pitch, r.iris_h, r.iris_v)
        if test_win["w"]:
            # draw boxes
            import cv2
            fr = cam.latest
            if fr is not None:
                vis = fr.copy()
                if r.face_box:
                    x1, y1, x2, y2 = r.face_box
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
                for p in engine.yolo.phones:
                    cv2.rectangle(vis, (p.x1, p.y1), (p.x2, p.y2), (0, 0, 255), 2)
                    cv2.putText(vis, f"phone {p.conf:.2f}", (p.x1, p.y1 - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                test_win["w"].panel.show_frame(vis)
                test_win["w"].panel.set_debug(
                    f"yaw {r.yaw:+.0f} pitch {r.pitch:+.0f} faces {r.n_faces} "
                    f"phones {len(engine.yolo.phones)} bright {r.brightness:.0f}")
    def on_yolo_res(res):
        engine.on_yolo(res, yolo.phone_voted())

    cam.frame_ready.connect(on_frame)
    face.result_ready.connect(on_face_res)
    yolo.result_ready.connect(on_yolo_res)

    # rule tick 10 Hz
    tick = QTimer()
    tick.timeout.connect(lambda: on_rule_types(engine.tick()))
    tick.start(100)

    def poll_examiner():
        if examiner is not None and examiner.finish_requested.is_set():
            examiner.finish_requested.clear()
            finish()
    exam_tick = QTimer()
    exam_tick.timeout.connect(poll_examiner)
    exam_tick.start(500)

    # --- guard ---
    from PyQt6.QtCore import QObject as _QObject, pyqtSignal as _sig
    from PyQt6.QtWidgets import QInputDialog, QLineEdit
    from proctor.guard.security import verify_password

    def ask_password():
        text, ok = QInputDialog.getText(stack, "Пароль", "Введите пароль экзаменатора:",
                                        QLineEdit.EchoMode.Password)
        if not ok or not text:
            return False
        ex = cfg["guard"]["examiner"]
        return verify_password(text, ex["salt_hex"], ex["hash_hex"], ex["iterations"])

    class ExitAsk(_QObject):
        asked = _sig()
    exit_ask = ExitAsk()
    exit_ask.asked.connect(lambda: app.quit() if ask_password() else None)
    hk = HotkeyGuard(cfg["guard"]["hotkeys"], cfg["guard"]["emergency_exit"],
                     on_block=lambda h: emit_violation("HOTKEY_BLOCKED", {"key": h}),
                     on_exit=lambda: exit_ask.asked.emit(),
                     enabled=guard_on)
    hk.start()
    fw = FocusWatch(get_hwnd=lambda: int(test_win["w"].winId()) if test_win["w"] else None,
                    enabled=guard_on and os.name == "nt")
    fw.lost.connect(lambda: emit_violation("FOCUS_LOST"))
    if guard_on:
        try: fw.start()
        except Exception: pass
    pw = ProcWatch(cfg["guard"]["forbidden_processes"], cfg["guard"]["process_check_sec"],
                   mode=cfg["guard"]["process_mode"] if not a.demo else "log")
    if guard_on:
        pw.found.connect(lambda s: emit_violation("FORBIDDEN_PROCESS", {"proc": s}))
        pw.start()
    cg = ClipboardGuard(app, cfg["guard"]["clipboard_clear_sec"],
                        on_violation=lambda t: emit_violation(t), parent=app)
    if guard_on:
        cg.start()

    # --- screens ---
    _mon_warned = {"v": False}
    _unlocked = {"v": False}
    _vc = {"done": False, "name": "", "blocked": False}
    def preflight_checks():
        n_mon = monitor_count()
        out = [("Камера работает", cam.latest is not None),
               ("В кадре одно лицо", engine.face.n_faces == 1),
               ("Телефона нет", len(engine.yolo.phones) == 0),
               (f"Мониторов: {n_mon} (нужен 1)", n_mon == 1 or _unlocked["v"])]
        if not _vc["done"] and a.video is None:
            _vc["done"] = True
            name, blocked = check_camera(cfg["camera"]["index"],
                                         cfg["preflight"]["virtual_keywords"],
                                         cfg["preflight"]["virtual_check_enabled"])
            _vc.update(name=name, blocked=blocked)
        out.append((f"{texts['vc_label']}: {_vc['name'] or '?'}", not _vc["blocked"]))
        if n_mon > 1 and not _mon_warned["v"]:
            _mon_warned["v"] = True
            emit_violation("SECOND_MONITOR", {"monitors": n_mon})
        return out

    pre = PreflightScreen(preflight_checks)

    def unlock_monitors():
        if ask_password():
            _unlocked["v"] = True
            return True
        return False
    pre.on_unlock = unlock_monitors
    cal_view = CalibrationView(cfg["calibration"]["duration_sec"])
    stack.addWidget(pre); stack.addWidget(cal_view)

    def start_calib():
        try:
            fio["name"] = pre.fio_text
        except Exception:
            pass
        try:
            pre.poll.stop()
        except Exception:
            pass
        stack.setCurrentWidget(cal_view)
        calib_mode["on"] = True
        cal_view.start()
    pre.ok.connect(start_calib)

    def calib_done():
        calib_mode["on"] = False
        base = calib.finish()
        engine.calib = base
        # open test
        w = TestWindow(on_violation=lambda t: emit_violation(t),
                       on_finish=lambda ans: finish(),
                       test_url=cfg["_test_url"], allowed_domains=cfg["_domains"])
        test_win["w"] = w
        stack.addWidget(w)
        stack.setCurrentWidget(w)

        # second-monitor live check
        if monitor_count() > 1:
            emit_violation("SECOND_MONITOR", {"live": True})
    cal_view.done.connect(calib_done)

    def calib_canceled():
        calib_mode["on"] = False
        stack.setCurrentWidget(pre)
    cal_view.canceled.connect(calib_canceled)

    def finish():
        if session["done"]:
            return
        session["done"] = True
        session["status"] = texts["examiner_done"]
        t1 = time.time()
        dur = t1 - store.t0
        fps = fps_hist["n"] / max(dur, 1)
        trust = trust_calc.get_score(texts.get("trust_labels") or {})
        out, score, trust = generate(store, cfg, cfg["report"]["out"], fps, dur,
                                     fio.get("name", ""), trust)
        # result screen
        res = QWidget()
        res.setObjectName("root")
        l = QVBoxLayout(res)
        l.setContentsMargins(16, 16, 16, 16)
        head = QLabel(texts["result_title"])
        head.setObjectName("title")
        l.addWidget(head)
        l.addWidget(QLabel(f"{texts['violations']}: {len(store.all())}. {texts['trust_score']}: {score}/100."))
        if trust["breakdown_text"]:
            l.addWidget(QLabel(trust["breakdown_text"]))
        l.addWidget(QLabel(f"Отчёт: {out}"))
        b = QPushButton(texts["result_open"])

        def _open(path):
            try:
                if os.name == "nt":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", path])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", path])
            except Exception as e:
                print("[report] cannot open:", e)

        b.clicked.connect(lambda: _open(out))
        l.addWidget(b)
        q = QPushButton(texts["result_exit"]); q.clicked.connect(app.quit)
        l.addWidget(q)
        stack.addWidget(res); stack.setCurrentWidget(res)
        _open(out)

    bus.sub(lambda v: None)
    cam.start(); yolo.start(); face.start()
    stack.show()

    def cleanup():
        for th in (cam, yolo, face, fw, pw, cg):
            try: th.stop()
            except Exception: pass
        hk.stop()
        try:
            if examiner is not None:
                examiner.stop()
        except Exception: pass
    app.aboutToQuit.connect(cleanup)
    try:
        rc = app.exec()
    finally:
        cleanup()
    return rc

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
