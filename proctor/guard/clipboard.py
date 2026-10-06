"""GUI-thread clipboard guard. Ignore self-clears, throttle external change bursts."""
import time
from PyQt6.QtCore import QObject,QTimer

class ClipboardGuard(QObject):
    def __init__(self,app,interval=5.,on_violation=None,parent=None):
        super().__init__(parent)
        self.app,self.on_violation = app,on_violation
        self.cb = app.clipboard()
        self._clearing = False
        self._active = False
        self._last_hit = -1e9
        self.timer = QTimer(self)
        self.timer.setInterval(max(100,int(interval*1000)))
        self.timer.timeout.connect(self._hit)

    def start(self):
        if not self._active:
            self._active = True
            self.cb.dataChanged.connect(self._hit)
            self.timer.start()

    def _hit(self):
        if self._clearing or not self._active:
            return
        md = self.cb.mimeData()
        if md is None or not md.formats():
            return
        self._clearing = True
        try:
            self.cb.clear()
        finally:
            self._clearing = False
        now = time.monotonic()
        if now-self._last_hit >= 1.:
            self._last_hit = now
            if self.on_violation:
                self.on_violation("COPY_ATTEMPT")

    def stop(self):
        self.timer.stop()
        if self._active:
            self.cb.dataChanged.disconnect(self._hit)
        self._active = False
