import os
import time
import psutil
from PyQt6.QtCore import QThread, pyqtSignal


def forbidden_processes(blacklist):
    names = {name.lower().removesuffix(".exe") for name in blacklist}
    found = []
    for process in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            if (
                process.info["pid"] != os.getpid()
                and (process.info["name"] or "").lower().removesuffix(".exe") in names
            ):
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


class ProcWatch(QThread):
    found = pyqtSignal(str)

    def __init__(
        self, blacklist, interval=2.0, mode="log", parent=None, enforcing=True
    ):
        super().__init__(parent)
        self.black = blacklist
        self.interval = max(0.2, interval)
        self.mode = mode
        self.enforcing = enforcing
        self._run = True
        self.status = "starting"
        self.error = ""
        self.checked_at = 0.0
        self.blocking = []
        self.seen = set()

    def scan(self):
        processes = forbidden_processes(self.black)
        self.blocking = [f"{p.info['name']} (pid {p.info['pid']})" for p in processes]
        current = set()
        errors = []
        for process, name in zip(processes, self.blocking):
            ident = (process.info["pid"], process.info["create_time"])
            current.add(ident)
            if self.enforcing:
                if ident not in self.seen:
                    self.found.emit(name)
                if self.mode == "close":
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                    except (psutil.AccessDenied, OSError) as exc:
                        errors.append(str(exc))
        self.seen = current if self.enforcing else set()
        self.checked_at = time.monotonic()
        self.error = " · ".join(errors)
        self.status = "partial" if errors else "active"

    def run(self):
        while self._run and not self.isInterruptionRequested():
            try:
                self.scan()
            except Exception as exc:
                self.error = str(exc)
                self.status = "error"
            until = time.monotonic() + self.interval
            while (
                self._run
                and not self.isInterruptionRequested()
                and time.monotonic() < until
            ):
                self.msleep(50)
        self.status = "stopped"

    def stop(self):
        self._run = False
        self.requestInterruption()
        return self.wait(1000)
