"""Clipboard guard: clear on timer + on change signal."""
from PyQt6.QtCore import QThread

class ClipboardGuard(QThread):
    def __init__(self, app, interval=5.0, on_violation=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.interval = interval
        self.on_violation = on_violation
        self._run = True

    def run(self):
        try:
            cb = self.app.clipboard()
            cb.dataChanged.connect(lambda: self._hit())
        except Exception:
            pass
        while self._run:
            try:
                self.app.clipboard().clear()
            except Exception:
                pass
            self.msleep(int(self.interval * 1000))

    def _hit(self):
        try:
            self.app.clipboard().clear()
        except Exception:
            pass
        if self.on_violation:
            self.on_violation("COPY_ATTEMPT")

    def stop(self):
        self._run = False
        self.wait(1000)
