import os, time, sys, types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PyQt6.QtCore import Qt
from proctor.guard import hotkeys, processes
from proctor.guard.focus import FocusWatch
from proctor.guard.security import guard_health
from proctor.tools.replay_evaluator import run, direction_metrics


def test_required_shortcuts_cannot_be_removed_by_empty_config(monkeypatch):
    registered = []
    monkeypatch.setitem(
        sys.modules,
        "keyboard",
        types.SimpleNamespace(
            add_hotkey=lambda name, *a, **k: registered.append(name),
            unhook_all=lambda: None,
        ),
    )
    monkeypatch.setattr(hotkeys, "FULL_GUARD", True)
    guard = hotkeys.HotkeyGuard([])
    assert guard.start() and set(hotkeys.REQUIRED_KEYS) <= set(registered)
    guard.stop()
    assert guard.status == "disabled"


def test_existing_forbidden_program_not_ignored_or_killed_during_preflight(monkeypatch):
    killed = []
    process = types.SimpleNamespace(
        info={"pid": 900001, "name": "CHROME.EXE", "create_time": 1},
        kill=lambda: killed.append(1),
    )
    monkeypatch.setattr(processes.psutil, "process_iter", lambda attrs: [process])
    watch = processes.ProcWatch(["chrome"], mode="close", enforcing=False)
    watch.scan()
    assert watch.blocking and watch.status == "active" and not killed
    watch.enforcing = True
    watch.scan()
    assert killed == [1]


def test_failed_process_enforcement_not_claimed_active(monkeypatch):
    def denied():
        raise processes.psutil.AccessDenied(99)

    process = types.SimpleNamespace(
        info={"pid": 99, "name": "chrome", "create_time": 1}, kill=denied
    )
    monkeypatch.setattr(processes, "forbidden_processes", lambda names: [process])
    watch = processes.ProcWatch(["chrome"], mode="close")
    watch.scan()
    assert watch.status == "partial" and watch.error


def test_focus_failure_closes_answer_gate():
    focus = FocusWatch(lambda: 10)
    native = types.SimpleNamespace(
        IsWindow=lambda hwnd: True,
        GetForegroundWindow=lambda: 20,
        GetAncestor=lambda *a: 20,
        SetForegroundWindow=lambda hwnd: None,
    )
    focus.check(native)
    assert focus.status == "active" and not focus.focus_ok
    now = time.monotonic()
    keys = types.SimpleNamespace(status="active")
    process = types.SimpleNamespace(
        status="active", checked_at=now, interval=2, blocking=[]
    )
    clip = types.SimpleNamespace(_active=True)
    ready, errors = guard_health(keys, focus, process, clip, now)
    assert not ready and errors
    native.GetForegroundWindow = lambda: 10
    focus.check(native)
    assert guard_health(keys, focus, process, clip, time.monotonic())[0]
    assert not guard_health(keys, focus, process, clip, time.monotonic() + 10)[0]


def test_no_shortened_replay_calibration():
    for mode in ("none", "center"):
        with pytest.raises(ValueError, match="раздельную"):
            run("unused", {}, mode)


def test_direction_metrics_count_unknown_as_failure_and_keep_channels_separate():
    timeline = [
        dict(t=31, head="CENTER", gaze="RIGHT"),
        dict(t=32, head="CENTER", gaze="UNKNOWN"),
    ]
    out = direction_metrics(
        timeline, [dict(t_start=30, t_end=33, head="CENTER", gaze="RIGHT")]
    )
    assert (
        out["head"]["correct_fraction"] == 1 and out["gaze"]["correct_fraction"] == 0.5
    )
    assert out["gaze"]["segments"][0]["unknown"] == 1


def test_weak_global_floor_retains_detail_resolution():
    from proctor.core.detector_yolo import DetectorYolo

    detector = DetectorYolo(imgsz=416)
    for _ in range(90):
        detector.budget.observe(0.4)
    assert detector.budget.imgsz == 320 and detector.detail_imgsz == 544
    for _ in range(300):
        detector.budget.observe(0.01)
    assert detector.budget.imgsz == 416


def test_dead_camera_or_cv_worker_disables_answer_readiness():
    from proctor.core.session_health import cv_health

    now = time.monotonic()
    camera = types.SimpleNamespace(status="ready")
    packet = types.SimpleNamespace(captured_at=now)
    result = types.SimpleNamespace(seq=1, captured_at=now, error="")
    worker = types.SimpleNamespace(
        status="ready", output=types.SimpleNamespace(peek=lambda: result)
    )
    assert cv_health(camera, [("Face", worker)], packet, now)[0]
    assert not cv_health(camera, [("Face", worker)], packet, now + 2)[0]
    worker.status = "error"
    assert not cv_health(camera, [("Face", worker)], packet, now)[0]


def test_adaptive_budget_recovers_resolution_without_exceeding_initial_budget():
    from proctor.core.pipeline import AdaptiveBudget

    budget = AdaptiveBudget(6, imgsz=416, min_imgsz=320)
    for _ in range(90):
        budget.observe(0.4)
    assert budget.imgsz == 320
    for _ in range(300):
        budget.observe(0.01)
    assert budget.imgsz == 416 and budget.target_fps == 6


def test_wrong_model_labels_cannot_silently_report_no_phone():
    from proctor.core.detector_yolo import DetectorYolo

    detector = DetectorYolo()
    detector.validate_classes({0: "person", 67: "cell phone"})
    with pytest.raises(ValueError, match="класс телефона"):
        detector.validate_classes({0: "face"})
    with pytest.raises(ValueError, match="person"):
        detector.validate_classes({0: "face", 67: "cell phone"})


@pytest.mark.skipif(
    os.getenv("PROCTOR_MODEL_SMOKE") != "1", reason="native two-face composition"
)
def test_native_full_frame_scan_sees_two_portrait_faces():
    import cv2, numpy as np
    from proctor.core.face_mesh import FaceMeshThread
    from proctor.core.pipeline import FramePacket

    image = cv2.imread(os.environ["PROCTOR_FACE_FIXTURE"])
    assert image is not None
    frame = np.concatenate([image, image], axis=1)
    face = FaceMeshThread(max_faces=2)
    try:
        face.setup()
        result = face.process(FramePacket(1, time.monotonic(), frame))
        assert result.n_faces == 2 and len(result.face_boxes) == 2
    finally:
        face.teardown()
