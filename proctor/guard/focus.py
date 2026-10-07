from PyQt6.QtCore import QThread, pyqtSignal
from .security import FULL_GUARD, OS


class FocusWatch(QThread):
    lost = pyqtSignal()

    def __init__(self, get_hwnd=None, enabled=True, parent=None):
        super().__init__(parent)
        self.get_hwnd = get_hwnd
        self.enabled = enabled
        self._run = True
        if enabled and not FULL_GUARD:
            print(f"[focus] {OS}: возврат фокуса недоступен, только логирование.")

    def run(self):
        if not self.enabled:
            return
        try:
            import win32gui, win32process
        except ImportError:
            print("[focus] pywin32 missing, watchdog limited")
            return
        import win32gui

        while self._run:
            if not self.enabled:
                self.msleep(100)
                continue
            try:
                fg = win32gui.GetForegroundWindow()
                mine = self.get_hwnd() if self.get_hwnd else None
                if mine and fg != mine:
                    self.lost.emit()
                    try:
                        win32gui.SetForegroundWindow(mine)
                    except Exception:
                        pass
            except Exception:
                pass
            self.msleep(300)

    def stop(self):
        self._run = False
        self.wait(1000)
