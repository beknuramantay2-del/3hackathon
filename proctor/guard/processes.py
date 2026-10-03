"""Process watchdog: snapshot at start, only NEW allowlisted processes. Mode close/log from config."""
import psutil
from PyQt6.QtCore import QThread, pyqtSignal

class ProcWatch(QThread):
    found = pyqtSignal(str)
    def __init__(self, blacklist, interval=2.0, mode="log", parent=None):
        super().__init__(parent)
        self.black = [b.lower() for b in blacklist]
        self.interval = interval
        self.mode = mode
        self._run = True
        self.base = set()

    def snapshot(self):
        s = set()
        for p in psutil.process_iter(["pid", "name"]):
            try:
                s.add(p.info["pid"])
            except Exception:
                pass
        self.base = s

    def run(self):
        self.snapshot()
        while self._run:
            try:
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        name = (p.info["name"] or "").lower()
                        if p.info["pid"] in self.base:
                            continue
                        if any(b in name for b in self.black):
                            self.found.emit(f"{p.info['name']} (pid {p.info['pid']})")
                            if self.mode == "close":
                                try:
                                    p.kill()
                                except Exception:
                                    pass
                    except Exception:
                        continue
            except Exception as e:
                print("[proc]", e)
            self.msleep(int(self.interval * 1000))

    def stop(self):
        self._run = False
        self.wait(1000)
