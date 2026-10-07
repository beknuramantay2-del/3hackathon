"""Case 3 MVP: camera -> latest-only workers -> fresh temporal rules -> Qt dashboard."""
import os
import sys
import time
import argparse
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer,QObject,pyqtSignal,QLockFile,Qt
from PyQt6.QtWidgets import (QApplication,QStackedWidget,QWidget,QVBoxLayout,QLabel,
                            QPushButton,QInputDialog,QLineEdit,QSplashScreen)
from PyQt6.QtGui import QPixmap
from proctor.core.camera import CameraThread
from proctor.core.overlay import render_overlay
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.face_mesh import FaceMeshThread
from proctor.core.hands import HandsThread
from proctor.core.calibration import CalibrationError
from proctor.core.mandatory_calibration import MandatoryCalibration,calibration_complete
from proctor.core.rules import RuleEngine
from proctor.core.episodes import EpisodePolicy
from proctor.core.evidence import EvidenceRecorder
from proctor.ui.clip_viewer import ClipViewer
from proctor.core.events import make_violation,SEVERITY
from proctor.core.store import Store,EventWriter
from proctor.core.pipeline import Timings,hardware_profile
from proctor.core.config import load_config,ensure_hash_salt,ConfigError
from proctor.core.strings import load_strings
from proctor.core.trust_score import TrustCalculator
from proctor.core.examiner_server import ExaminerServer
from proctor.core.preflight import check_camera
from proctor.guard.hotkeys import HotkeyGuard
from proctor.guard.focus import FocusWatch
from proctor.guard.processes import ProcWatch
from proctor.guard.clipboard import ClipboardGuard
from proctor.guard.displays import monitor_count
from proctor.guard.security import verify_password,FULL_GUARD
from proctor.ui.screens import PreflightScreen,CalibrationView,SidePanel
from proctor.ui.test_window import TestWindow
from proctor.report.generator import generate

class UiBus(QObject):
    violation = pyqtSignal(str,object)
    exit_requested = pyqtSignal()

class LockedStack(QStackedWidget):
    locked = False
    def closeEvent(self,event):
        if self.locked:
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    if "--embedded-test" not in sys.argv:
        from proctor.live import main as live_main
        return live_main()
    sys.argv.remove("--embedded-test")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-guard",action="store_true")
    ap.add_argument("--demo",action="store_true",help="monitoring mode, never close processes")
    ap.add_argument("--video")
    ap.add_argument("--debug",action="store_true")
    ap.add_argument("--profile",choices=("dev","exam"))
    ap.add_argument("--config")
    ap.add_argument("--perf",choices=("auto","weak","balanced"))
    args = ap.parse_args()
    # Stable relative paths regardless of current working directory.
    os.chdir(Path(__file__).resolve().parent.parent)
    try:
        cfg = load_config(args.config,args.profile)
        if not cfg["_test_url"].strip():
            raise ConfigError("Для --embedded-test задайте реальную страницу test.test_url. Заглушка теста отключена")
        texts = load_strings()
        if cfg["hash_chain"]["enabled"] and not cfg["hash_chain"]["salt_hex"]:
            ensure_hash_salt(cfg)
        if cfg["profile"] == "exam":
            import ctypes
            if args.no_guard or args.demo or args.video or not FULL_GUARD or not ctypes.windll.shell32.IsUserAnAdmin():
                raise ConfigError("exam требует Windows/admin, живую камеру и включенную защиту; для демо используйте dev")
    except (ConfigError,RuntimeError) as e:
        print(e,file=sys.stderr)
        return 2
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # validate before creating QApplication
    except ImportError as e:
        print(f"Не загружается WebEngine: {e}",file=sys.stderr)
        return 2
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    os.makedirs("data",exist_ok=True)
    lock = QLockFile(os.path.abspath("data/proctor.lock"))
    if not lock.tryLock(100):
        print("Другой экземпляр прокторинга уже запущен",file=sys.stderr)
        return 2
    app.setStyleSheet(Path("proctor/ui/style.qss").read_text(encoding="utf-8"))
    splash_pix = QPixmap(500,160)
    splash_pix.fill(Qt.GlobalColor.black)
    splash = QSplashScreen(splash_pix)
    splash.showMessage("Локальный прокторинг · загрузка моделей…",Qt.AlignmentFlag.AlignCenter,Qt.GlobalColor.white)
    splash.show()
    app.processEvents()

    perf = cfg.get("performance",{})
    profile = hardware_profile(args.perf or perf.get("mode","auto"))
    adaptive = perf.get("adaptive",True)
    import cv2
    cv2.setNumThreads(1)  # avoid native thread-pool oversubscription
    cam = CameraThread(width=cfg["camera"]["width"],height=cfg["camera"]["height"],
                       fps=cfg["camera"]["fps"],video_file=args.video,index=cfg["camera"]["index"],parent=app)
    yc,fc = cfg["yolo"],cfg["face"]
    yolo = DetectorYolo(yc["model"],min(yc["imgsz"],profile["imgsz"]) if adaptive else yc["imgsz"],
                        yc["conf_phone"],yc["conf_person"],parent=app,
                        target_fps=min(yc["target_fps"],profile["yolo_fps"]) if adaptive else yc["target_fps"],
                        device=yc.get("device","auto"),threads=min(int(perf.get("threads",2)),profile["threads"]),
                        offline=yc.get("offline",True),adaptive=adaptive,
                        phone_class=yc["phone_class"],person_class=yc["person_class"])
    face = FaceMeshThread(fc["max_faces"],parent=app,
                          target_fps=min(fc.get("target_fps",15),profile["face_fps"]) if adaptive else fc.get("target_fps",15),
                          max_width=fc.get("max_width",640),min_detection_confidence=fc["min_detection_confidence"],
                          min_tracking_confidence=fc["min_tracking_confidence"],adaptive=adaptive)
    hands = HandsThread(yolo.output.peek,min(fc.get("hands_fps",5),profile["hands_fps"]),parent=app) if fc.get("hands_enabled",True) else None
    yolo.hand_provider=(lambda:hands.output.peek()) if hands else None
    workers = [yolo,face]+([hands] if hands else [])
    for worker in workers:
        cam.subscribe(worker.push)  # bounded pointer replacement, not queued Qt signals
    run_id = time.strftime("%Y%m%d_%H%M%S")+f"_{os.getpid()}"
    session_dir = Path(cfg["store"]["db"]).parent/"sessions"/run_id
    store = Store(str(session_dir/"session.db"),str(session_dir/"shots"),
                  cfg["hash_chain"]["salt_hex"] if cfg["hash_chain"]["enabled"] else "")
    writer = EventWriter(store)
    pc=cfg.get('policy',{});ec=cfg.get('evidence',{})
    policy=EpisodePolicy(pc.get('yellow_sec',3.),pc.get('red_sec',5.),phone_conf=pc.get('immediate_phone_conf',.55))
    recorder=EvidenceRecorder(store,session_dir/'clips',fps=ec.get('fps',5),pre=ec.get('pre_sec',2.),post=ec.get('post_sec',2.),segment=ec.get('segment_sec',15.),source='video replay' if args.video else 'camera')
    cam.subscribe(recorder.push)
    trust = TrustCalculator(cfg["trust_weights"])
    engine = RuleEngine(cfg)
    calibration = MandatoryCalibration()
    metrics = Timings()
    state = dict(active=False,calibrating=False,done=False,started=0.,fio="",status="Подготовка")
    window = {"test":None}
    stack = LockedStack()
    stack.setWindowTitle("Case 3 · Локальный прокторинг")
    stack.resize(1100,760)
    last_events = {}
    bridge = UiBus()

    def emit_violation(vtype,details=None):
        if not state["active"] or state["done"]:
            return
        vtype = vtype.split(":",1)[0]
        if vtype not in SEVERITY:
            return
        now = time.monotonic()
        event_gap = 5. if vtype in ("CAMERA_LOST","FOCUS_LOST","FORBIDDEN_PROCESS","SECOND_MONITOR") else 1.
        if not (details or {}).get("ident") and now-last_events.get(vtype,-1e9) < event_gap:
            return
        last_events[vtype] = now
        duration = (details or {}).get("duration",engine.debug.get("durations",{}).get(vtype,0.))
        v = make_violation(vtype,duration,"",dict(details or {}))
        if (details or {}).get('ident'):
            v.severity=details['level'];v.t_start=details['started']+recorder.wall_offset
        packet=cam.output.peek()
        shot = packet.frame if cfg["store"]["save_screenshots"] and packet and now-packet.captured_at<.7 else None
        if writer.submit(v,shot):
            trust.apply_violation(v)
            if window["test"]:
                window["test"].panel.log(f"{time.strftime('%H:%M:%S')} · {vtype} · {duration:.1f}с",(details or {}).get('level'),(details or {}).get('ident',''))
                if (details or {}).get('level')==2:QApplication.beep()
    bridge.violation.connect(emit_violation)

    def ask_password():
        was_enabled = fw.enabled
        fw.enabled = False
        try:
            text,ok = QInputDialog.getText(stack,"Выход экзаменатора","Пароль:",QLineEdit.EchoMode.Password)
            ex = cfg["guard"]["examiner"]
            return bool(ok and text and verify_password(text,ex["salt_hex"],ex["hash_hex"],ex["iterations"]))
        finally:
            fw.enabled = was_enabled

    def exit_requested():
        if cfg["profile"] != "exam" or ask_password():
            if state["active"]:
                finish()
            stack.locked = False
            app.quit()
    bridge.exit_requested.connect(exit_requested)
    guard_on = not args.no_guard
    hk = HotkeyGuard(cfg["guard"]["hotkeys"],cfg["guard"]["exit_combo"],
                     on_block=lambda h:bridge.violation.emit("HOTKEY_BLOCKED",{"key":h}),
                     on_exit=bridge.exit_requested.emit,enabled=guard_on)
    # Never call QWidget.winId() from a watchdog worker.
    hwnd = {"value":None}
    fw = FocusWatch(lambda:hwnd["value"],enabled=guard_on and os.name == "nt",parent=app)
    fw.lost.connect(lambda:emit_violation("FOCUS_LOST"))
    pw = ProcWatch(cfg["guard"]["forbidden_processes"],cfg["guard"]["process_check_sec"],
                   mode="log" if args.demo else cfg["guard"]["process_mode"],parent=app)
    pw.found.connect(lambda name:emit_violation("FORBIDDEN_PROCESS",{"process":name}))
    clipboard = ClipboardGuard(app,cfg["guard"]["clipboard_clear_sec"],emit_violation,parent=app)
    try:
        examiner = ExaminerServer(cfg["examiner"]["host"],cfg["examiner"]["port"],store.db,store.shots,
                                  cfg["trust_weights"],lambda:state["fio"],lambda:state["status"])
        print("Экзаменатор:",examiner.start())
    except Exception as e:
        examiner = None
        print("Панель экзаменатора недоступна:",e)
    camera_check = {"done":False,"name":"","blocked":False}
    unlocked = {"monitors":False}

    def checks():
        packet = cam.output.peek()
        fresh = packet is not None and time.monotonic()-packet.captured_at < 1
        if fresh and not camera_check["done"]:
            camera_check["done"] = True
            if not args.video:
                camera_check["name"],camera_check["blocked"] = check_camera(cfg["camera"]["index"],
                    cfg["preflight"]["virtual_keywords"],cfg["preflight"]["virtual_check_enabled"])
        now = time.monotonic()
        out = [("Камера: "+cam.status+(" · "+cam.error if cam.error else ""),fresh and cam.status == "ready")]
        for label,worker in (("YOLO",yolo),("FaceMesh",face)):
            r = worker.output.peek()
            out.append((label+": "+worker.status+(" · "+worker.error if worker.error else ""),
                        worker.status == "ready" and r is not None and engine.fresh(r,now)))
        out.extend([("В кадре одно лицо",engine.face.n_faces == 1 and engine.fresh(engine.face,now)),
                    ("Телефона нет",not any(b.observed for b in engine.yolo.phones)),
                    (f"Мониторов: {monitor_count()} (нужен 1)",monitor_count() == 1 or unlocked["monitors"]),
                    ("Камера не виртуальная (эвристика)",not camera_check["blocked"])])
        return out
    pre = PreflightScreen(checks)
    cal_view = CalibrationView(calibration.duration, external=True)
    stack.addWidget(pre); stack.addWidget(cal_view)

    def unlock_monitors():
        if ask_password():
            unlocked["monitors"] = True
            return True
        return False
    pre.on_unlock = unlock_monitors

    def start_calibration():
        nonlocal calibration
        calibration=MandatoryCalibration()
        engine.calib={}
        pre.poll.stop()
        state["fio"],state["calibrating"] = pre.fio_text,True
        stack.setCurrentWidget(cal_view)
        cal_view.start()
        calibration.start(cal_view.started_at)
        engine.reset()
    pre.ok.connect(start_calibration)
    cal_view.retry.clicked.connect(start_calibration)

    def cancel_calibration():
        state["calibrating"] = False
        stack.setCurrentWidget(pre)
        pre.poll.start(1000)
    cal_view.canceled.connect(cancel_calibration)

    def open_evidence(item):
        ident=item.data(Qt.ItemDataRole.UserRole)
        if not ident or cfg['profile']=='exam' and not ask_password():return
        was_enabled=fw.enabled;fw.enabled=False
        try:
            clips=store.clips_for(ident)
            if clips:ClipViewer(clips,session_dir/'clips',stack).exec()
            elif window['test']:window['test'].panel.set_status('Футаж ещё записывается / недоступен: '+recorder.error)
        finally:fw.enabled=was_enabled

    def calibration_done():
        state["calibrating"] = False
        try:
            if not calibration_complete(calibration.base):
                raise CalibrationError("Обязательная калибровка головы и глаз не завершена")
            engine.calib = calibration.base
            if not all(good for _,good in checks()):
                raise CalibrationError("Проверка устройств не пройдена: вернитесь на старт")
        except CalibrationError as e:
            cal_view.failed(str(e))
            return
        engine.reset()
        try:
            w = TestWindow(emit_violation,lambda ans:finish(),cfg["_test_url"],cfg["_domains"])
        except RuntimeError as e:
            cal_view.failed(str(e))
            return
        window["test"] = w
        w.panel.debug_on = args.debug
        w.panel.feed.itemDoubleClicked.connect(open_evidence)
        stack.addWidget(w); stack.setCurrentWidget(w)
        state.update(active=True,started=time.monotonic(),status="Тест активен")
        stack.locked = cfg["profile"] == "exam"
        if stack.locked:
            stack.showFullScreen()
        hwnd["value"] = int(stack.winId())
        if guard_on:
            guarded = hk.start()
            if cfg["profile"] == "exam" and not guarded:
                state.update(active=False,status="Ошибка защиты")
                stack.locked = False
                stack.setCurrentWidget(cal_view)
                stack.showNormal()
                stack.removeWidget(w)
                w.dispose(); w.deleteLater()
                window["test"] = None
                hk.stop()
                cal_view.failed("Защита клавиш не активирована: "+", ".join(hk.errors))
                return
            fw.start(); pw.start(); clipboard.start()
    cal_view.done.connect(calibration_done)
    seen = {w:-1 for w in workers}
    frame_seen = {"seq":-1}
    resource = {"at":0.,"text":""}
    import psutil
    process = psutil.Process()
    process.cpu_percent()
    last_monitor = {"at":0.}

    def poll():
        now = time.monotonic()
        if state["done"]:
            return
        for worker in workers:
            result = worker.output.peek()
            if result is None or seen[worker] == result.seq:
                continue
            seen[worker] = result.seq
            for name,elapsed in getattr(result,"timings",{}).items():
                metrics.add(name,elapsed)
            if worker is face:
                engine.on_face(result)
                if state["calibrating"] and now-result.captured_at < .5:
                    calibration.feed(result)
            elif worker is yolo:
                engine.on_yolo(result)
            else:
                engine.on_hands(result)
        if state["calibrating"]:
            phase=calibration.phase(now);stage=calibration.stage
            instruction='поверните голову' if stage=='head' else 'двигайте только глазами; голова прямо'
            label={'CENTER':'прямо','LEFT':'влево','RIGHT':'вправо','UP':'вверх','DOWN':'вниз'}[phase]
            cal_view.title.setText('Обязательная калибровка: '+('голова' if stage=='head' else 'глаза'))
            cal_view.prompt.setText(instruction+' · '+label)
            cal_view.counter.setText(f'Осталось {calibration.remaining(now):.1f} с')
            marker=(stage,phase)
            if cal_view.last_phase!=marker:
                cal_view.last_phase=marker;QApplication.beep()
            try:
                if calibration.advance(now):calibration_done()
            except CalibrationError as exc:
                state['calibrating']=False;engine.calib={};cal_view.failed(str(exc))
        if state["active"]:
            s = time.perf_counter()
            # Disable evidence from a dead/stalled worker; never keep a phone or gaze forever.
            if face.status != "ready":
                engine.face.error = face.error or face.status
            if yolo.status != "ready":
                engine.yolo.error = yolo.error or yolo.status
            ready=calibration_complete(engine.calib)
            engine.tick(now,directions_enabled=ready)
            engine.debug['calibrated']=ready
            engine.debug['immediate_phone_conf']=max(policy.phone_conf,yolo.conf_phone)
            if not ready:engine.debug.update(head='UNKNOWN',gaze='UNKNOWN')
            window['test'].view.setEnabled(ready)
            conditions=policy.observations(engine,now,ready)
            changes,notices=policy.update(conditions,now)
            recorder.submit(changes)
            for row in changes:
                if not row['closed'] and row['level']==0 and row['duration']==0:
                    window['test'].panel.log('Запись: '+row['kind'],0,row['ident'])
            for row in notices:emit_violation(row['kind'],dict(row,measurements=engine.debug))
            engine.debug['conds']=conditions
            engine.debug['durations']={k:e.duration for k,e in policy.active.items()}
            engine.debug['warnings_active']=[k for k,e in policy.active.items() if e.level>0]
            metrics.add("logic",time.perf_counter()-s)
            if cam.status != "ready" or cam.output.peek() is None or now-cam.output.peek().captured_at > 1:
                emit_violation("CAMERA_LOST",{"status":cam.status})
            if now-last_monitor["at"] >= 2:
                last_monitor["at"] = now
                if monitor_count() > 1 and not unlocked["monitors"]:
                    emit_violation("SECOND_MONITOR")
            panel = window["test"].panel
            elapsed = int(now-state["started"])
            panel.timer.setText(f"{elapsed//60:02d}:{elapsed%60:02d}")
            d = engine.debug
            n_faces,n_phones = d.get("n_faces"),d.get("phones")
            face_status = "UNKNOWN" if n_faces is None else (f"DETECTED ({n_faces})" if n_faces else "LOST")
            phone_status = "UNKNOWN" if n_phones is None else ("DETECTED" if d["conds"]["PHONE_DETECTED"] else ("CANDIDATE" if n_phones else "NOT DETECTED"))
            panel.states.setText(f"HEAD: {d.get('head','UNKNOWN')}\nGAZE: {d.get('gaze','UNKNOWN')}\nFACE: {face_status}\nPHONE: {phone_status}")
            active=[e for e in policy.active.values() if e.kind.startswith(('HEAD_','GAZE_'))]
            if active:
                ep=max(active,key=lambda e:e.duration)
                panel.set_hold(f'{ep.kind}: {ep.duration:.1f}с · жёлтый {policy.yellow:.0f}/красный {policy.red:.0f}с',ep.duration/policy.red)
            else:panel.set_hold('Нет активного отвода',0)
            errors = [w.error for w in workers if w.status == "error"]
            if guard_on and hk.status != "active":
                errors.append("Guard: "+hk.status)
            if writer.error:
                errors.append("Запись: "+writer.error)
            panel.set_status('Калибровка потеряна: ввод ответов заблокирован. Требуется новая сессия с экзаменатором.' if not ready else " · ".join(errors) if errors else ('ЗЕЛЁНЫЙ','ЖЁЛТЫЙ · проверка','КРАСНЫЙ · проверка экзаменатором')[policy.level]+' · PHONE_AIMED — эвристика')
        packet = cam.output.peek()
        if packet is not None and packet.seq != frame_seen["seq"]:
            frame_seen["seq"] = packet.seq
            if state["calibrating"] or state["active"]:
                s = time.perf_counter()
                vis = render_overlay(packet,engine.face,engine.yolo,engine.hands,cfg["camera"]["mirror_preview"],states=engine.debug)
                if state["calibrating"]:
                    cal_view.preview.setPixmap(SidePanel.pixmap(vis))
                else:
                    window["test"].panel.show_frame(vis)
                metrics.add("ui_render",time.perf_counter()-s)
                metrics.add("preview_latency",time.monotonic()-packet.captured_at)
        if state["active"] and now-resource["at"] > 1:
            resource["at"] = now
            snap = metrics.snapshot()
            averages = " · ".join(f"{k}: {v['mean_ms']:.1f}ms" for k,v in snap.items())
            result_ages = [now-r.captured_at for w in (face,yolo) if (r:=w.output.peek()) is not None]
            decision_latency = max(result_ages,default=0)*1000
            window["test"].panel.set_debug(f"Camera FPS: {cam.camera_fps:.1f} · capture {cam.read_ms:.1f}ms\n"
                f"Decision age: {decision_latency:.0f}ms · CPU process: {process.cpu_percent():.0f}%\n"
                f"RAM process: {process.memory_info().rss/1024**2:.0f}MB · imgsz {yolo.budget.imgsz}\n"
                f"YOLO {yolo.budget.target_fps:.1f}Hz · Face {face.budget.target_fps:.1f}Hz\n{averages}")
        if examiner and examiner.finish_requested.is_set():
            examiner.finish_requested.clear()
            # HTTP endpoint cannot bypass the exam exit password.
            if state["active"] and (cfg["profile"] != "exam" or ask_password()):
                finish()

    def finish():
        if state["done"]:
            return
        state.update(done=True,active=False,calibrating=False,status="Завершено")
        stack.locked = False
        hk.stop(); fw.stop(); pw.stop(); clipboard.stop()
        # Report generation occurs after exam end, never on a video hot path.
        for worker in [cam]+workers:
            if not worker.stop():
                worker.wait()
        recorder.submit(policy.close());recorder.stop()
        writer.flush()
        report,score,_ = generate(store,cfg,cfg["report"]["out"],cam.camera_fps,time.time()-store.t0,state["fio"],trust.get_score(texts.get("trust_labels") or {}))
        import json
        Path(session_dir/"timings.json").write_text(json.dumps(metrics.snapshot(),indent=2),encoding="utf-8")
        res = QWidget()
        layout = QVBoxLayout(res)
        layout.addWidget(QLabel(f"Тест завершён · индекс: {score}/100"))
        layout.addWidget(QLabel(f"Отчёт: {report}\nСессия: {session_dir}"+("\nОшибка записи: "+writer.error if writer.error else "")))
        button = QPushButton("Выйти")
        button.clicked.connect(app.quit)
        layout.addWidget(button)
        stack.addWidget(res); stack.setCurrentWidget(res)

    timer = QTimer(app)
    timer.timeout.connect(poll)
    timer.start(33)
    for worker in workers:
        worker.start()
    cam.start()
    stack.show()
    QTimer.singleShot(600,lambda:splash.finish(stack))
    cleaned = {"done":False}
    def cleanup():
        if cleaned["done"]:
            return
        cleaned["done"] = True
        timer.stop()
        hk.stop(); clipboard.stop(); fw.stop(); pw.stop()
        for worker in [cam]+workers:
            if not worker.stop():
                print(f"Ожидаю завершения {type(worker).__name__}; драйвер/инференс не отвечает")
                worker.wait()  # no unsafe QThread destruction
        if recorder.thread.is_alive():
            recorder.submit(policy.close());recorder.stop()
        writer.stop()
        if window["test"]:
            window["test"].dispose()
        if examiner:
            examiner.stop()
        store.close()
        lock.unlock()
    app.aboutToQuit.connect(cleanup)
    try:
        return app.exec()
    finally:
        cleanup()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
