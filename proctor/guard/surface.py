import os
import time
from PyQt6.QtCore import QThread, pyqtSignal


class WindowsSurface:
    def __init__(self):
        import ctypes
        import win32gui
        import win32process

        self.gui = win32gui
        self.process = win32process
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.SetWindowDisplayAffinity.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        self.user.SetWindowDisplayAffinity.restype = ctypes.c_int
        self.user.GetWindowDisplayAffinity.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
        )
        self.user.GetWindowDisplayAffinity.restype = ctypes.c_int

    def __getattr__(self, name):
        return getattr(self.gui, name)

    def owner_pid(self, hwnd):
        return self.process.GetWindowThreadProcessId(hwnd)[1]

    def affinity(self, hwnd):
        import ctypes

        value = ctypes.c_uint()
        if not self.user.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def set_affinity(self, hwnd, value):
        import ctypes

        if not self.user.SetWindowDisplayAffinity(hwnd, value):
            raise ctypes.WinError(ctypes.get_last_error())
        if self.affinity(hwnd) != value:
            raise OSError("Защита захвата экрана не подтверждена Windows")

    def raise_window(self, hwnd):
        self.gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)

    def owned_windows(self):
        handles = []
        self.gui.EnumWindows(lambda hwnd, _: handles.append(hwnd), None)
        return [
            h
            for h in handles
            if self.gui.IsWindowVisible(h) and self.owner_pid(h) == os.getpid()
        ]

    def overlays(self, mine):
        protected = self.gui.GetWindowRect(mine)
        current = self.gui.GetWindow(mine, 3)
        result, seen = [], set()
        while current and current not in seen and len(seen) < 1000:
            seen.add(current)
            if (
                self.gui.IsWindowVisible(current)
                and not self.gui.IsIconic(current)
                and self.owner_pid(current) != os.getpid()
            ):
                rect = self.gui.GetWindowRect(current)
                overlap = max(rect[0], protected[0]) < min(
                    rect[2], protected[2]
                ) and max(rect[1], protected[1]) < min(rect[3], protected[3])
                if overlap:
                    result.append(current)
            current = self.gui.GetWindow(current, 3)
        return result

    def minimize(self, hwnd):
        self.gui.ShowWindow(hwnd, 6)

    def restore(self, hwnd):
        self.gui.ShowWindow(hwnd, 9)

    def title(self, hwnd):
        return self.gui.GetWindowText(hwnd)[:120] or "Постороннее окно"

    def monitors(self):
        return self.user.GetSystemMetrics(80)

    def release_topmost(self, hwnd):
        self.gui.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)


class SurfaceWatch(QThread):
    blocked = pyqtSignal(str)

    def __init__(self, hwnd, native=None, parent=None):
        super().__init__(parent)
        self.hwnd, self.native = hwnd, native
        self.status = "starting"
        self.error = ""
        self.capture_ok = False
        self.overlays_ok = False
        self.checked_at = 0.0
        self.saved_affinity = {}
        self.minimized = {}

    def arm(self):
        try:
            self.native = self.native or WindowsSurface()
            self.protect(self.hwnd)
            self.native.raise_window(self.hwnd)
            self.capture_ok = True
            self.checked_at = time.monotonic()
            return True
        except Exception as exc:
            self.status, self.error = "error", str(exc)
            return False

    def protect(self, hwnd):
        if hwnd not in self.saved_affinity:
            self.saved_affinity[hwnd] = self.native.affinity(hwnd)
        if self.native.affinity(hwnd) != 0x11:
            self.native.set_affinity(hwnd, 0x11)

    def check(self):
        try:
            if self.native.monitors() != 1:
                raise OSError("Изменилось число мониторов; защита требует один экран")
            if not self.native.IsWindow(self.hwnd):
                raise OSError("Защищённое окно недоступно")
            for hwnd in {self.hwnd, *self.native.owned_windows()}:
                self.protect(hwnd)
            overlays = self.native.overlays(self.hwnd)
            for hwnd in overlays:
                if self.minimized.get(hwnd) != self.native.owner_pid(hwnd):
                    self.blocked.emit(self.native.title(hwnd))
                self.native.minimize(hwnd)
                self.minimized[hwnd] = self.native.owner_pid(hwnd)
            self.native.raise_window(self.hwnd)
            self.capture_ok = all(
                self.native.affinity(h) == 0x11
                for h in self.saved_affinity
                if self.native.IsWindow(h) and self.native.owner_pid(h) == os.getpid()
            )
            self.overlays_ok = not self.native.overlays(self.hwnd)
            self.status = (
                "active" if self.capture_ok and self.overlays_ok else "partial"
            )
            self.error = (
                "" if self.status == "active" else "Окно или захват экрана не защищены"
            )
        except Exception as exc:
            self.status, self.error = "error", str(exc)
            self.capture_ok = self.overlays_ok = False
        self.checked_at = time.monotonic()

    def run(self):
        while not self.isInterruptionRequested():
            self.check()
            self.msleep(100)
        self.status = "stopped"

    def release(self):
        if self.native is None:
            return
        for hwnd, original in self.saved_affinity.items():
            try:
                if (
                    self.native.IsWindow(hwnd)
                    and self.native.owner_pid(hwnd) == os.getpid()
                ):
                    self.native.set_affinity(hwnd, original)
            except Exception:
                pass
        try:
            if self.native.IsWindow(self.hwnd):
                self.native.release_topmost(self.hwnd)
        except Exception:
            pass
        for hwnd, pid in self.minimized.items():
            try:
                if self.native.IsWindow(hwnd) and self.native.owner_pid(hwnd) == pid:
                    self.native.restore(hwnd)
            except Exception:
                pass
        self.capture_ok = self.overlays_ok = False

    def stop(self):
        self.requestInterruption()
        stopped = self.wait(1500)
        if stopped:
            self.release()
        return stopped
