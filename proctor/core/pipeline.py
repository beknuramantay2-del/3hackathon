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
            return (
                (self._version, self._item)
                if self._version != version
                else (version, None)
            )

    def peek(self):
        with self._lock:
            return self._item


class Timings:

    def __init__(self, size=120):
        self.size = size
        self._lock = threading.Lock()
        self._values = {}

    def add(self, name, seconds):
        with self._lock:
            self._values.setdefault(name, deque(maxlen=self.size)).append(
                seconds * 1000
            )

    def snapshot(self):
        with self._lock:
            out = {}
            for key, values in self._values.items():
                a = sorted(values)
                out[key] = dict(
                    n=len(a),
                    mean_ms=sum(a) / len(a),
                    p50_ms=a[len(a) // 2],
                    p95_ms=a[min(len(a) - 1, int(len(a) * 0.95))],
                )
            return out


@dataclass
class AdaptiveBudget:
    target_fps: float
    imgsz: int = 384
    enabled: bool = True
    min_imgsz: int = 320
    ewma: float = 0.0
    observations: int = 0
    ceiling: float = field(init=False)
    image_ceiling: int = field(init=False)

    def __post_init__(self):
        self.ceiling = self.target_fps
        self.image_ceiling = self.imgsz

    def observe(self, seconds):
        self.ewma = seconds if not self.ewma else 0.9 * self.ewma + 0.1 * seconds
        self.observations += 1
        if not self.enabled or self.observations % 30:
            return
        if self.ewma > 0.85 / self.target_fps:
            self.target_fps = max(
                3.0, min(self.target_fps, 0.7 / max(self.ewma, 0.001))
            )
            if self.imgsz > self.min_imgsz:
                self.imgsz = max(self.min_imgsz, self.imgsz - 32)
        elif self.ewma < 0.5 / self.target_fps:
            self.target_fps = min(self.ceiling, self.target_fps + 1)
            if self.target_fps == self.ceiling and self.observations % 60 == 0:
                self.imgsz = min(self.image_ceiling, self.imgsz + 32)


def hardware_profile(mode="auto"):
    import os

    import psutil

    process = psutil.Process()
    try:
        cores = len(process.cpu_affinity())
    except (AttributeError, psutil.Error):
        cores = os.cpu_count() or 2
    ram = psutil.virtual_memory().total
    weak = mode == "weak" or mode == "auto" and (cores <= 4 or ram <= 8.5 * 1024**3)

    return dict(
        width=640,
        height=480,
        imgsz=416 if weak else 640,
        yolo_fps=6 if weak else 10,
        face_fps=12 if weak else 20,
        hands_fps=4 if weak else 6,
        threads=1 if weak else max(1, min(2, cores // 2)),
    )
