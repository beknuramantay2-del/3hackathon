"""Default entry: useful live CV independent of a test page or a preflight gate."""
import argparse,json,os,sys,time
from pathlib import Path
from PyQt6.QtCore import QTimer,QLockFile,QObject,pyqtSignal
from PyQt6.QtWidgets import QApplication,QInputDialog,QLineEdit
from proctor.core.camera import CameraThread
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.face_mesh import FaceMeshThread
from proctor.core.hands import HandsThread
from proctor.core.rules import RuleEngine
from proctor.core.calibration import Calibration,CalibrationError
from proctor.core.config import load_config,ensure_hash_salt
from proctor.core.pipeline import hardware_profile,Timings
from proctor.core.overlay import render_overlay
from proctor.core.store import Store,EventWriter
from proctor.core.events import make_violation,SEVERITY
from proctor.ui.monitor_window import MonitorWindow
from proctor.guard.hotkeys import HotkeyGuard
from proctor.guard.focus import FocusWatch
from proctor.guard.processes import ProcWatch
from proctor.guard.clipboard import ClipboardGuard
from proctor.guard.security import FULL_GUARD,verify_password

class Signals(QObject):
    event=pyqtSignal(str,object)
    exit=pyqtSignal()


def main():
    ap=argparse.ArgumentParser(description='Живой CV-прокторинг без страницы теста')
    ap.add_argument('--video');ap.add_argument('--config');ap.add_argument('--debug',action='store_true')
    ap.add_argument('--perf',choices=('auto','weak','balanced'))
    ap.add_argument('--profile',choices=('dev','exam'))
    ap.add_argument('--no-guard',action='store_true');ap.add_argument('--demo',action='store_true')
    ap.add_argument('--run-seconds',type=float,help=argparse.SUPPRESS)
    args=ap.parse_args()
    os.chdir(Path(__file__).resolve().parent.parent)
    cfg=load_config(args.config,args.profile,require_test_url=False)
    if cfg['profile']=='exam':
        import ctypes
        if args.no_guard or args.demo or args.video or args.run_seconds is not None or not FULL_GUARD or not ctypes.windll.shell32.IsUserAnAdmin():
            raise SystemExit('Защищённый режим: Windows/admin, камера и включённая защита. Для CV без lock — dev.')
    if cfg['hash_chain']['enabled'] and not cfg['hash_chain']['salt_hex']:
        ensure_hash_salt(cfg)
    import cv2
    cv2.setNumThreads(1)
    app=QApplication(sys.argv)
    app.setStyleSheet(Path('proctor/ui/style.qss').read_text())
    Path('data').mkdir(exist_ok=True)
    lock=QLockFile(str(Path('data/proctor.lock').resolve()))
    if not lock.tryLock(100):
        raise SystemExit('Прокторинг уже запущен')
    profile=hardware_profile(args.perf or cfg.get('performance',{}).get('mode','auto'))
    adaptive=cfg.get('performance',{}).get('adaptive',True)
    cam=CameraThread(cfg['camera']['width'],cfg['camera']['height'],args.video,cfg['camera']['index'],fps=cfg['camera']['fps'])
    yc,fc=cfg['yolo'],cfg['face']
    yolo=DetectorYolo(yc['model'],min(yc['imgsz'],profile['imgsz']) if adaptive else yc['imgsz'],
        yc['conf_phone'],yc['conf_person'],target_fps=min(yc['target_fps'],profile['yolo_fps']),
        device=yc.get('device','auto'),threads=profile['threads'],offline=yc.get('offline',True),adaptive=adaptive,
        detail_search=yc.get('detail_search',True),phone_class=yc['phone_class'],person_class=yc['person_class'])
    face=FaceMeshThread(fc['max_faces'],target_fps=min(fc.get('target_fps',15),profile['face_fps']),
        max_width=fc.get('max_width',640),min_detection_confidence=fc['min_detection_confidence'],
        min_tracking_confidence=fc['min_tracking_confidence'],adaptive=adaptive)
    hands=HandsThread(yolo.output.peek,fc.get('hands_fps',5)) if fc.get('hands_enabled',True) else None
    workers=[yolo,face]+([hands] if hands else [])
    for worker in workers:
        cam.subscribe(worker.push)
    engine=RuleEngine(cfg)
    timings=Timings()
    run=Path('data/sessions')/(time.strftime('%Y%m%d_%H%M%S')+'_'+str(os.getpid()))
    store=Store(str(run/'session.db'),str(run/'shots'),cfg['hash_chain']['salt_hex'] if cfg['hash_chain']['enabled'] else '')
    writer=EventWriter(store)
    window=MonitorWindow()
    signals=Signals()
    started=time.monotonic()
    last_events={}
    finished=False
    calibration=None
    calibration_mode=None
    next_phase=None
    poll_versions={w:-1 for w in workers}
    last_frame=-1
    last_ui=0.
    auto_center_attempted=False

    def event(kind,details=None):
        if finished or calibration is not None or kind not in SEVERITY:
            return
        if kind.startswith(("HEAD_","GAZE_")) and not engine.calib:
            return
        now=time.monotonic()
        if now-last_events.get(kind,-1e6)<5:
            return
        last_events[kind]=now
        duration=engine.debug.get('durations',{}).get(kind,0.)
        # No meaningless trust/accuracy number; every event stores the actual measurement and calibration state.
        data=dict(details or engine.debug)
        data['calibrated']=bool(engine.calib)
        data['source']='video replay' if args.video else 'camera'
        violation=make_violation(kind,duration,'',data)
        packet=cam.output.peek()
        data["frame_age_ms"]=(now-packet.captured_at)*1000 if packet else None
        frame=packet.frame if cfg['store']['save_screenshots'] and packet and now-packet.captured_at<.7 else None
        writer.submit(violation,frame)
        window.log(f'{time.strftime("%H:%M:%S")} · {kind} · удержание {duration:.1f}с')
    signals.event.connect(event)
    guard_on=not args.no_guard and not args.demo and cfg['profile']=='exam'
    hk=HotkeyGuard(cfg['guard']['hotkeys'],cfg['guard']['exit_combo'],
        on_block=lambda key:signals.event.emit('HOTKEY_BLOCKED',{'key':key}),on_exit=signals.exit.emit,enabled=guard_on)
    fw=FocusWatch(lambda:int(window.winId()),enabled=guard_on and os.name=='nt')
    # Cache native ID before starting a guard worker; never call QWidget from that worker.
    native_id=int(window.winId());fw.get_hwnd=lambda:native_id
    pw=ProcWatch(cfg['guard']['forbidden_processes'],cfg['guard']['process_check_sec'],cfg['guard']['process_mode'])
    clipboard=ClipboardGuard(app,cfg['guard']['clipboard_clear_sec'],lambda kind:event(kind))
    fw.lost.connect(lambda:event('FOCUS_LOST'))
    pw.found.connect(lambda name:event('FORBIDDEN_PROCESS',{'process':name}))
    if guard_on:
        if not hk.start():
            hk.stop();writer.stop();store.close();lock.unlock()
            raise SystemExit('Не удалось активировать защиту клавиш: '+', '.join(hk.errors))
        fw.start();pw.start();clipboard.start();window.locked=True
    def exit_requested():
        if window.locked:
            fw.enabled=False
            password,ok=QInputDialog.getText(window,'Выход экзаменатора','Пароль:',QLineEdit.EchoMode.Password)
            ex=cfg['guard']['examiner']
            fw.enabled=True
            if not ok or not verify_password(password,ex['salt_hex'],ex['hash_hex'],ex['iterations']):
                return
        window.locked=False
        app.quit()
    signals.exit.connect(exit_requested)
    window.finish.connect(exit_requested)
    window.phone_threshold.setValue(yc['conf_phone'])
    window.phone_threshold.valueChanged.connect(lambda value:setattr(yolo,'requested_confidence',float(value)))
    def set_hold(value):
        for key,rule in engine.rules.items():
            if key.startswith('GAZE_'):
                rule.hold=value
    window.gaze_hold.valueChanged.connect(set_hold)
    window.yaw_threshold.setValue(cfg['rules']['gaze_side']['yaw_thresh'])
    window.pitch_threshold.setValue(cfg['rules']['gaze_down']['pitch_thresh'])
    window.yaw_threshold.valueChanged.connect(lambda value:cfg['rules']['gaze_side'].update(yaw_thresh=value))
    window.pitch_threshold.valueChanged.connect(lambda value:cfg['rules']['gaze_down'].update(pitch_thresh=value))
    def start_calibration(mode):
        nonlocal calibration,calibration_mode,next_phase
        if face.status!='ready':
            window.banner.setText('FaceMesh ещё не готов: '+(face.error or face.status));return
        calibration=Calibration(20 if mode=='five' else 2)
        calibration.start(time.monotonic())
        calibration_mode=mode;next_phase=None
        engine.reset()
    window.calibrate.connect(start_calibration)

    def poll():
        nonlocal last_frame,last_ui,calibration,calibration_mode,next_phase,auto_center_attempted
        now=time.monotonic()
        for worker in workers:
            result=worker.output.peek()
            if result is None or result.seq==poll_versions[worker]:
                continue
            poll_versions[worker]=result.seq
            for name,value in getattr(result,'timings',{}).items():
                timings.add(name,value)
            if worker is face:
                engine.on_face(result)
                if calibration and result.n_faces==1 and result.pose_valid and result.gaze_valid:
                    kw=dict(left_eye=result.left_eye,right_eye=result.right_eye)
                    if calibration_mode=='center':
                        if .3<result.captured_at-calibration.started_at<2:
                            calibration.add(result.yaw,result.pitch,result.iris_h,result.iris_v,pose='CENTER',**kw)
                    else:
                        calibration.add(result.yaw,result.pitch,result.iris_h,result.iris_v,now=result.captured_at,**kw)
            elif worker is yolo:
                engine.on_yolo(result)
            else:
                engine.on_hands(result)
        if not auto_center_attempted and face.output.peek() is not None and engine.face.n_faces==1 and engine.face.pose_valid and engine.face.gaze_valid:
            auto_center_attempted=True
            start_calibration('center')
        if calibration:
            elapsed=now-calibration.started_at
            pose='CENTER' if calibration_mode=='center' else calibration.phase(elapsed)
            if pose!=next_phase:
                next_phase=pose;window.show_target(pose);QApplication.beep()
            window.banner.setText(f'КАЛИБРОВКА {pose}: глаза на точку, голова прямо · {max(0,calibration.duration-elapsed):.1f}с')
            if elapsed>=calibration.duration:
                try:
                    base=calibration.finish(center_only=calibration_mode=='center')
                    base["mode"]=calibration_mode
                    engine.calib=base
                    engine.reset()
                    window.log('Центр головы/каждого глаза обновлён. '+('Центр измерен; 5 поз — отдельной кнопкой' if calibration_mode=='center' else ('Все позы измерены' if not base['unresolved_targets'] else 'Не различены: '+', '.join(base['unresolved_targets']))))
                except CalibrationError as exc:
                    window.log('Калибровка не принята: '+str(exc))
                calibration=None;window.target.hide();QApplication.beep()
        if face.status!="ready" or face.output.peek() is None:
            engine.face.error=face.error or "FaceMesh loading/unavailable"
        if yolo.status!="ready" or yolo.output.peek() is None:
            engine.yolo.error=yolo.error or "YOLO loading/unavailable"
        s=time.perf_counter()
        types=engine.tick(now)
        timings.add('logic',time.perf_counter()-s)
        for kind in types:
            event(kind)
        if not args.video and now-started>3 and (cam.output.peek() is None or now-cam.output.peek().captured_at>1):
            event("CAMERA_LOST",{"status":cam.status,"error":cam.error})
        packet=cam.output.peek()
        if packet and packet.seq!=last_frame:
            last_frame=packet.seq
            s=time.perf_counter()
            vis=render_overlay(packet,engine.face,engine.yolo,engine.hands,cfg['camera']['mirror_preview'])
            window.show_frame(vis)
            timings.add('ui_render',time.perf_counter()-s)
        if now-last_ui<.15:
            return
        last_ui=now
        d=engine.debug;f=engine.face;y=engine.yolo
        ff=face.status=='ready' and f.seq>=0 and engine.fresh(f,now)
        yf=yolo.status=='ready' and y.seq>=0 and engine.fresh(y,now)
        face_text=str(f.n_faces) if ff else 'UNKNOWN'
        phone_text='CONFIRMED' if yf and y.phone_voted else ('CANDIDATE' if yf and y.candidates else ('НЕ НАБЛЮДАЕТСЯ' if yf else 'UNKNOWN'))
        window.tiles.setText(f'HEAD: {d.get("head")}\nGAZE: {d.get("gaze")}\nFACE: {face_text}\nPHONE: {phone_text}')
        window.show_directions(d.get('head'),d.get('gaze'))
        errors=[type(w).__name__+': '+w.error for w in workers if w.status=='error']
        if not calibration:
            window.banner.setText(' · '.join(errors) if errors else ('Детекция активна. Калибровка: '+(str(engine.calib.get('mode','center')) if engine.calib else 'НЕТ — нажмите «Центр 2с»; HEAD/GAZE предупреждения отключены')+' · '+('VIDEO REPLAY' if args.video else 'CAMERA')+' · '+cam.status))
        t=d.get('thresholds',{})
        left='—' if f.left_eye is None else f'{f.left_eye[0]:.3f}/{f.left_eye[1]:.3f}'
        right='—' if f.right_eye is None else f'{f.right_eye[0]:.3f}/{f.right_eye[1]:.3f}'
        pmax=max((p.conf for p in y.candidates),default=None)
        head_numbers=f'{d.get("dyaw",0):+.1f}° / {d.get("dpitch",0):+.1f}°; roll {f.roll:+.1f}°' if ff and f.pose_valid else 'UNKNOWN — нет валидной геометрии'
        gaze_numbers=f'{d.get("gaze_dx",0):+.3f} / {d.get("gaze_dy",0):+.3f}' if ff and f.gaze_valid else 'UNKNOWN — зрачки не читаются'
        gates=d.get('gaze_entry_gates',{})
        gate_text=' / '.join(f'{gates[k]:.3f}' if k in gates else '—' for k in ('LEFT','RIGHT','UP','DOWN'))
        confidence=f'{pmax:.2f}' if pmax is not None and yf else 'нет свежего кандидата'
        recovery=any(p.confirmed and p.observed and p.conf<yolo.conf_phone for p in y.phones)
        window.measurements.setText(f'HEAD Δ yaw / pitch: {head_numbers}\n'
            f'GAZE Δ H / V: {gaze_numbers}; глаз L {left}, R {right}\n'
            f'HEAD вход: yaw {t.get("yaw")}° / pitch {t.get("pitch")}°; выход 70% порога\n'
            f'GAZE вход L/R/U/D: {gate_text}\n'
            f'Phone P={confidence}; новый track ≥ {yolo.conf_phone:.2f}; imgsz {yolo.budget.imgsz}'+(' · weak recovery: strong ≤0.8с' if recovery else '')+'\n'
            f'HEAD quality: {"valid" if f.pose_valid and ff else f.pose_reason or "нет свежего измерения"}; GAZE: {f.gaze_reason or "нет измерения"}')
        active=[(rule.active_duration(now)/max(.01,rule.hold),key,rule) for key,rule in engine.rules.items() if d['conds'].get(key)]
        if active:
            fraction,key,rule=max(active)
            window.progress.setValue(min(100,int(100*fraction)))
            window.progress.setFormat(f'{key}: {rule.active_duration(now):.1f}/{rule.hold:.1f}с')
        else:
            window.progress.setValue(0);window.progress.setFormat('Нет активного удержания')
        def state_for(key):
            return ('АКТИВНО · ' if d['conds'].get(key) else 'не наблюдается · ')+f'{d["durations"].get(key,0):.1f}/{engine.rules[key].hold:.1f}с'
        window.set_case('PHONE',phone_text)
        for item,kind in (('HAND','PHONE_IN_HAND'),('LIFT','PHONE_LIFTED'),('RAISED','PHONE_RAISED'),('AIM','PHONE_AIMED')):
            available=yf and (item!='HAND' or hands is not None and hands.status=='ready' and engine.fresh(engine.hands,now,.5))
            if item in ('RAISED','AIM'):
                available=available and ff and f.face_box is not None
            window.set_case(item,(state_for(kind) if available else 'UNKNOWN: модуль/данные недоступны')+(' · эвристика' if item=='AIM' else ''))
        window.set_case('HEAD',d['head']+' · независимо от глаз')
        window.set_case('GAZE',d['gaze']+(' · персональный центр' if engine.calib else ' · предварительно'))
        window.set_case('DOWN',state_for('GAZE_DOWN') if engine.calib and f.gaze_valid and ff else 'UNKNOWN: требуется центр / читаемые глаза')
        window.set_case('SIDE',state_for('GAZE_LEFT')+' / '+state_for('GAZE_RIGHT') if engine.calib and f.gaze_valid and ff else 'UNKNOWN: требуется центр / читаемые глаза')
        window.set_case('PRESENCE','Лиц: '+face_text+(' · landmarks потеряны' if ff and not f.pose_valid else ''))
        window.set_case('MULTI',state_for('MULTI_FACE') if ff else 'UNKNOWN: лицо не измерено')
        guard_status='hook активен' if hk.status=='active' else 'НЕ АКТИВНО: '+hk.status
        for key in ('ALT','WIN','SHOT'):
            window.set_case(key,guard_status+(' · не все способы screenshot' if key=='SHOT' else ''))
        window.set_case('COPY',guard_status+(' · clipboard active' if guard_on else ' · буфер не блокируется в dev'))
        window.set_case('WINDOWS',('watchdog/focus активны; только новые процессы' if guard_on else 'НЕ АКТИВНО в CV/dev'))
        window.set_case('TABS','Нет браузера в этом режиме. Защита вкладок — --embedded-test')
        age=lambda r: f'{(now-r.captured_at)*1000:.0f}ms' if r.seq>=0 else '—'
        camera_rate=cam.camera_fps if packet and now-packet.captured_at<1 else 0.
        window.performance.setText(f'Реально: Camera {camera_rate:.1f}fps, YOLO {yolo.actual_fps if yf else 0.:.1f}Hz, Face {face.actual_fps if ff else 0.:.1f}Hz\n'
            f'Возраст данных: YOLO {age(y)}, Face {age(f)}. Пропущено кадров YOLO {yolo.skipped_frames} / Face {face.skipped_frames}\n'
            f'Сессия {(now-started):.0f}с · очередь записи: {writer.queue.unfinished_tasks} · '+('Ошибка записи: '+writer.error if writer.error else 'БД локальная'))
    timer=QTimer();timer.timeout.connect(poll);timer.start(33)
    for w in workers:
        w.start()
    cam.start()
    window.show()
    if window.locked:
        window.showFullScreen()
    if args.run_seconds:
        QTimer.singleShot(int(args.run_seconds*1000),app.quit)
    cleaned=False
    def cleanup():
        nonlocal cleaned,finished
        if cleaned:
            return
        cleaned=True;finished=True;timer.stop()
        hk.stop();fw.stop();pw.stop();clipboard.stop()
        for w in [cam]+workers:
            if not w.stop():
                w.wait()
        writer.stop()
        (run/'timings.json').write_text(json.dumps(timings.snapshot(),indent=2))
        (run/'calibration.json').write_text(json.dumps(engine.calib,indent=2))
        (run/'runtime.json').write_text(json.dumps(dict(source='video' if args.video else 'camera',
            phone_confidence=yolo.conf_phone,imgsz=yolo.budget.imgsz,frames_camera=cam.seq,
            frames_yolo=yolo.processed_count,frames_face=face.processed_count,
            head=engine.debug.get('head'),gaze=engine.debug.get('gaze'),
            case_status={key:window.checklist.item(row,1).text() for key,row in window.rows.items()},
            calibration=engine.calib.get('mode'),guard=hk.status),ensure_ascii=False,indent=2))
        store.close();lock.unlock()
    app.aboutToQuit.connect(cleanup)
    try:
        return app.exec()
    finally:
        cleanup()

if __name__=='__main__':
    raise SystemExit(main())
