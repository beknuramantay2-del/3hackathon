import time
from .models import Violation


class EventBus:
    def __init__(self):
        self.subs = []

    def sub(self, fn):
        self.subs.append(fn)

    def emit(self, v: Violation):
        for fn in self.subs:
            try:
                fn(v)
            except Exception as e:
                print("[bus]", e)


SEVERITY = {
    "PHONE_AIMED": 3,
    "PHONE_RAISED": 3,
    "MULTI_FACE": 3,
    "CAMERA_LOST": 2,
    "PHONE_IN_HAND": 2,
    "PHONE_LIFTED": 2,
    "HEAD_LEFT": 1,
    "HEAD_RIGHT": 1,
    "HEAD_UP": 1,
    "HEAD_DOWN": 1,
    "GAZE_UP": 1,
    "PHONE_DETECTED": 2,
    "NO_FACE": 2,
    "SECOND_MONITOR": 2,
    "CAMERA_COVERED": 2,
    "FORBIDDEN_PROCESS": 2,
    "FOCUS_LOST": 2,
    "HOTKEY_BLOCKED": 1,
    "TAB_SWITCH": 1,
    "COPY_ATTEMPT": 1,
    "GAZE_DOWN": 1,
    "GAZE_LEFT": 1,
    "GAZE_RIGHT": 1,
    "NAV_BLOCKED": 1,
}


def make_violation(vtype, duration, shot_path, details=None) -> Violation:
    return Violation(
        type=vtype,
        severity=SEVERITY.get(vtype, 1),
        t_start=time.time(),
        duration=duration,
        screenshot=shot_path or "",
        details=details or {},
    )
