"""Bounded, latest-only transport. No per-frame Qt event queue."""
import threading
import time
from dataclasses import dataclass, field
from collections import deque

@dataclass(frozen=True)
class FramePacket:
    seq: int
    captured_at: float
    frame: object
    source_time: float | None = None

class LatestSlot:
    def __init__(self):
        self._lock = threading.Lock()
        self._item = None
        self._version = 0

    def put(self, item):
        with self._lock:
            self._version += 1
            self._item = item

    def take_after(self, version):
        with self._lock:
            return (self._version, self._item) if self._version != version else (version, None)

    def peek(self):
        with self._lock:
            return self._item

class Timings:
    """Small rolling windows, not an unbounded history."""
    def __init__(self, size=120):
        self.size = size
        self._lock = threading.Lock()
        self._values = {}

    def add(self, name, seconds):
        with self._lock:
            self._values.setdefault(name, deque(maxlen=self.size)).append(seconds * 1000)

    def snapshot(self):
        with self._lock:
            out = {}
            for key, values in self._values.items():
                a = sorted(values)
                out[key] = dict(n=len(a), mean_ms=sum(a) / len(a),
                                p50_ms=a[len(a)//2], p95_ms=a[min(len(a)-1, int(len(a)*.95))])
            return out

@dataclass
class AdaptiveBudget:
    target_fps: float
    imgsz: int = 384
    enabled: bool = True
    ewma: float = 0.0
    observations: int = 0
    ceiling: float = field(init=False)

    def __post_init__(self):
        self.ceiling = self.target_fps

    def observe(self, seconds):
        self.ewma = seconds if not self.ewma else .9 * self.ewma + .1 * seconds
        self.observations += 1
        if not self.enabled or self.observations % 30:
            return
        if self.ewma > .85 / self.target_fps:
            self.target_fps = max(3.0, min(self.target_fps, .7 / max(self.ewma, .001)))
            if self.imgsz > 320:
                self.imgsz = max(320, self.imgsz - 32)
        elif self.ewma < .5 / self.target_fps:
            self.target_fps = min(self.ceiling, self.target_fps + 1)


def hardware_profile(mode="auto"):
    import os
    cores = os.cpu_count() or 2
    weak = mode == "weak" or (mode == "auto" and cores <= 4)
    # Start conservatively; measured worker latency, not core count alone, controls rate.
    return dict(width=640, height=480, imgsz=320 if weak else 384,
                yolo_fps=6 if weak else 10, face_fps=12 if weak else 20,
                hands_fps=4 if weak else 6, threads=max(1, min(4, cores//2)))
