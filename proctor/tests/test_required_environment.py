import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from proctor.guard import environment, hotkeys
from proctor.guard.security import guard_health
from proctor.guard.surface import SurfaceWatch
from proctor.guard.focus import FocusWatch


def args(**values):
    return SimpleNamespace(
        **dict(
            dict(
                preview=False,
                protected=False,
                profile=None,
                video=None,
                run_seconds=None,
            ),
            **values
        )
    )


def test_protection_is_default_and_preview_is_explicit():
    assert environment.protected_mode(args())
    assert environment.protected_mode(args(protected=True))
    assert not environment.protected_mode(args(preview=True))


@pytest.mark.parametrize(
    "values",
    [
        dict(video="scene.mp4"),
        dict(run_seconds=1),
        dict(profile="dev"),
        dict(preview=True, protected=True),
    ],
)
def test_unsafe_options_cannot_silently_disable_guard(values):
    with pytest.raises(ValueError):
        environment.protected_mode(args(**values))


@pytest.mark.parametrize(
    "system,capable,admin,build,monitors",
    [
        ("Linux", False, False, 0, 0),
        ("Windows", False, True, 22000, 1),
        ("Windows", True, False, 22000, 1),
        ("Windows", True, True, 18000, 1),
        ("Windows", True, True, 22000, 2),
    ],
)
def test_unsupported_runtime_fails_closed(system, capable, admin, build, monitors):
    assert environment.runtime_errors(system, capable, admin, build, monitors)


def test_supported_runtime_checks_can_pass():
    assert not environment.runtime_errors("Windows", True, True, 22000, 1)


def test_mandatory_processes_cannot_be_removed_from_config():
    assert set(environment.REQUIRED_PROCESSES) <= set(
        environment.required_processes([])
    )
    assert "snippingtool" in environment.required_processes([])


def test_preflight_does_not_kill_existing_programs(monkeypatch):
    monkeypatch.setattr(environment, "runtime_errors", lambda: [])
    process = SimpleNamespace(info={"name": "chrome.exe"})
    monkeypatch.setattr(environment, "forbidden_processes", lambda names: [process])
    cfg = dict(
        guard=dict(
            examiner=dict(salt_hex="ab" * 16, hash_hex="cd" * 32, iterations=100000),
            exit_combo="ctrl+shift+f12",
            forbidden_processes=[],
        )
    )
    assert any("chrome.exe" in e for e in environment.admission_errors(cfg))


def test_mandatory_hotkeys_use_suppression(monkeypatch):
    calls = []
    monkeypatch.setattr(hotkeys, "FULL_GUARD", True)
    monkeypatch.setitem(
        sys.modules,
        "keyboard",
        SimpleNamespace(
            add_hotkey=lambda key, callback, **kwargs: calls.append((key, kwargs)),
            unhook_all=lambda: None,
        ),
    )
    guard = hotkeys.HotkeyGuard([])
    assert guard.start()
    blocked = {key for key, kw in calls if kw.get("suppress")}
    assert set(hotkeys.REQUIRED_KEYS) <= blocked
    assert {
        "windows+shift+s",
        "ctrl+insert",
        "shift+insert",
        "print screen",
        "alt+tab",
    } <= blocked
    assert dict(calls)["ctrl+shift+f12"].get("suppress") is None
    guard.stop()


def test_exit_shortcut_cannot_replace_copy_block(monkeypatch):
    monkeypatch.setattr(hotkeys, "FULL_GUARD", True)
    guard = hotkeys.HotkeyGuard([], emergency="ctrl+c")
    assert not guard.start() and guard.status == "error"


class FakeNative:
    def __init__(self):
        self.values = {10: 0, 11: 1}
        self.foreign = []
        self.raised = []
        self.minimized = []
        self.restored = []
        self.reject = False
        self.ignore_minimize = False
        self.monitor_count = 1

    def IsWindow(self, hwnd):
        return True

    def affinity(self, hwnd):
        return self.values.get(hwnd, 0)

    def set_affinity(self, hwnd, value):
        if self.reject:
            raise OSError("capture exclusion refused")
        self.values[hwnd] = value

    def raise_window(self, hwnd):
        self.raised.append(hwnd)

    def owned_windows(self):
        return [10, 11]

    def overlays(self, hwnd):
        return list(self.foreign)

    def minimize(self, hwnd):
        self.minimized.append(hwnd)
        if not self.ignore_minimize:
            self.foreign.remove(hwnd)

    def restore(self, hwnd):
        self.restored.append(hwnd)

    def owner_pid(self, hwnd):
        return os.getpid() if hwnd in (10, 11) else 9999

    def monitors(self):
        return self.monitor_count

    def release_topmost(self, hwnd):
        pass

    def title(self, hwnd):
        return "Overlay"


def test_capture_exclusion_and_owned_dialog_are_verified_and_restored():
    native = FakeNative()
    surface = SurfaceWatch(10, native)
    assert surface.arm()
    surface.check()
    assert native.values == {10: 0x11, 11: 0x11}
    assert surface.status == "active" and surface.capture_ok
    assert surface.stop()
    assert native.values == {10: 0, 11: 1}


def test_capture_api_refusal_is_not_active_protection():
    native = FakeNative()
    native.reject = True
    surface = SurfaceWatch(10, native)
    assert not surface.arm()
    assert surface.status == "error" and not surface.capture_ok
    surface.stop()


def test_foreign_overlay_is_minimized_not_killed_and_restored():
    native = FakeNative()
    native.foreign = [55]
    surface = SurfaceWatch(10, native)
    seen = []
    surface.blocked.connect(seen.append)
    assert surface.arm()
    surface.check()
    assert seen == ["Overlay"] and native.minimized == [55]
    assert surface.overlays_ok and surface.status == "active"
    assert surface.stop()
    assert native.restored == [55]


def test_overlay_that_cannot_be_minimized_fails_health_gate():
    native = FakeNative()
    native.foreign = [55]
    native.ignore_minimize = True
    surface = SurfaceWatch(10, native)
    assert surface.arm()
    surface.check()
    assert surface.status == "partial" and not surface.overlays_ok
    surface.stop()


def test_dead_surface_watchdog_not_claimed_ready():
    now = time.monotonic()
    keys = SimpleNamespace(status="active")
    focus = SimpleNamespace(
        enabled=True, status="active", focus_ok=True, checked_at=now
    )
    processes = SimpleNamespace(
        status="active", checked_at=now, interval=1, blocking=[]
    )
    clip = SimpleNamespace(_active=True, status="active")
    surface = SimpleNamespace(
        status="active", capture_ok=True, overlays_ok=True, checked_at=now
    )
    assert guard_health(keys, focus, processes, clip, now, surface=surface)[0]
    surface.checked_at -= 2
    assert not guard_health(keys, focus, processes, clip, now, surface=surface)[0]
    assert not guard_health(keys, focus, processes, clip, now)[0]


def test_password_dialog_owned_by_application_does_not_lose_focus():
    native = SimpleNamespace(
        IsWindow=lambda hwnd: True,
        GetForegroundWindow=lambda: 20,
        GetAncestor=lambda hwnd, kind: 20,
        owner_pid=lambda hwnd: os.getpid(),
        SetForegroundWindow=lambda hwnd: pytest.fail(
            "owned dialog must not be displaced"
        ),
    )
    focus = FocusWatch(lambda: 10)
    focus.check(native)
    assert focus.focus_ok


@pytest.mark.skipif(sys.platform == "win32", reason="requires unsupported OS")
def test_default_cli_refuses_insecure_fallback_on_non_windows():
    result = subprocess.run(
        [sys.executable, "-m", "proctor", "--perf", "weak"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Windows" in result.stderr and "--preview" in result.stderr


def test_second_monitor_connected_during_session_loses_security_health():
    native = FakeNative()
    surface = SurfaceWatch(10, native)
    assert surface.arm()
    surface.check()
    assert surface.status == "active"
    native.monitor_count = 2
    surface.check()
    assert surface.status == "error" and not surface.capture_ok
    surface.stop()


def test_clipboard_is_cleared_at_start_and_restored_to_normal_behavior():
    from PyQt6.QtWidgets import QApplication
    from proctor.guard.clipboard import ClipboardGuard

    app = QApplication.instance() or QApplication([])
    clipboard = app.clipboard()
    clipboard.setText("pre-session")
    seen = []
    guard = ClipboardGuard(app, on_violation=seen.append)
    guard.start()
    assert clipboard.text() == "" and seen == []
    clipboard.setText("copy attempt")
    app.processEvents()
    assert clipboard.text() == "" and seen == ["COPY_ATTEMPT"]
    guard.stop()
    clipboard.setText("after session")
    assert clipboard.text() == "after session"
    clipboard.clear()


def test_clipboard_failure_is_not_claimed_active():
    from PyQt6.QtWidgets import QApplication
    from proctor.guard.clipboard import ClipboardGuard

    app = QApplication.instance() or QApplication([])
    guard = ClipboardGuard(app)
    guard._active = True
    guard.cb = SimpleNamespace(mimeData=lambda: None)
    guard._hit()
    assert guard.status == "error"
    guard._active = False


def test_observer_shows_failed_security_even_without_cv_warning(tmp_path):
    import numpy as np
    from PyQt6.QtWidgets import QApplication
    from proctor.core.monitoring import MonitorPublisher, write_session
    from proctor.monitor import Observer

    app = QApplication.instance() or QApplication([])
    directory = tmp_path / "run"
    write_session(directory, "Learner", "running")
    publisher = MonitorPublisher(directory)
    window = None
    try:
        publisher.offer(
            np.zeros((480, 640, 3), np.uint8),
            1,
            time.monotonic(),
            dict(
                head="CENTER",
                gaze="CENTER",
                level=0,
                calibrated=True,
                protected=True,
                protection_ready=False,
            ),
        )
        deadline = time.monotonic() + 3
        while not publisher.published and time.monotonic() < deadline:
            time.sleep(0.01)
        window = Observer(tmp_path)
        window.refresh()
        app.processEvents()
        assert (
            "КРАСНЫЙ" in window.status.text()
            and "Защита не готова" in window.status.text()
        )
    finally:
        publisher.stop()
        if window:
            window.close()
