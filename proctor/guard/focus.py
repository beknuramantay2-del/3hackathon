import time
from PyQt6.QtCore import QThread, pyqtSignal
from .security import FULL_GUARD, OS


class FocusWatch(QThread):
    lost = pyqtSignal()

    def __init__(self, get_hwnd=None, enabled=True, parent=None):
        super().__init__(parent)
        self.get_hwnd = get_hwnd
        self.enabled = enabled
        self._run = True
        self.status = "starting" if enabled else "disabled"
        self.error = ""
        self.checked_at = 0.0
        self.focus_ok = False
        self._lost = False

    def check(self, native):
        mine = self.get_hwnd() if self.get_hwnd else None
        if not mine or not native.IsWindow(mine):
            self.status = "error"
            self.error = "Окно экзамена недоступно"
            self.focus_ok = False
            return
        foreground = native.GetForegroundWindow()
        self.focus_ok = foreground == mine or native.GetAncestor(foreground, 3) == mine
        if not self.focus_ok:
            if not self._lost:
                self.lost.emit()
            self._lost = True
            try:
                native.SetForegroundWindow(mine)
                foreground = native.GetForegroundWindow()
                self.focus_ok = (
                    foreground == mine or native.GetAncestor(foreground, 3) == mine
                )
            except Exception as exc:
                self.error = str(exc)
        else:
            self._lost = False
        self.checked_at = time.monotonic()
        self.status = "active"
        if self.focus_ok:
            self.error = ""

    def run(self):
        if not self.enabled:
            self.status = "disabled"
            return
        if not FULL_GUARD:
            self.status = "unsupported"
            self.error = f"{OS}: возврат фокуса недоступен"
            return
        try:
            import win32gui
        except ImportError as exc:
            self.status = "error"
            self.error = str(exc)
            return
        while self._run and not self.isInterruptionRequested():
            if self.enabled:
                try:
                    self.check(win32gui)
                except Exception as exc:
                    self.status = "error"
                    self.focus_ok = False
                    self.error = str(exc)
            self.msleep(100)
        self.status = "stopped"

    def stop(self):
        self._run = False
        self.requestInterruption()
        return self.wait(1000)
