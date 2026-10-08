import time
from PyQt6.QtCore import QObject, QTimer


class ClipboardGuard(QObject):
    def __init__(self, app, interval=5.0, on_violation=None, parent=None):
        super().__init__(parent)
        self.app, self.on_violation = app, on_violation
        self.cb = app.clipboard()
        self._clearing = False
        self._active = False
        self.status = "disabled"
        self.error = ""
        self._last_hit = -1e9
        self.timer = QTimer(self)
        self.timer.setInterval(max(100, int(interval * 1000)))
        self.timer.timeout.connect(self._hit)

    def start(self):
        if not self._active:
            self._active = True
            self.status = "active"
            self.cb.dataChanged.connect(self._hit)
            self.timer.start()
            self._hit(notify=False)

    def _hit(self, notify=True):
        if self._clearing or not self._active:
            return
        md = self.cb.mimeData()
        if md is None:
            self.status, self.error = "error", "Буфер обмена недоступен"
            return
        if not md.formats():
            self.status, self.error = "active", ""
            return
        self._clearing = True
        try:
            self.cb.clear()
            if self.cb.mimeData().formats():
                raise RuntimeError("Буфер обмена не очищен")
            self.status = "active"
        except Exception as exc:
            self.status, self.error = "error", str(exc)
        finally:
            self._clearing = False
        now = time.monotonic()
        if notify and now - self._last_hit >= 1.0:
            self._last_hit = now
            if self.on_violation:
                self.on_violation("COPY_ATTEMPT")

    def stop(self):
        self.timer.stop()
        if self._active:
            self.cb.dataChanged.disconnect(self._hit)
        self._active = False
        self.status = "disabled"
