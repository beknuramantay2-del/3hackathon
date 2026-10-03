"""Core data contracts."""
from dataclasses import dataclass, field

@dataclass
class FaceResult:
    n_faces: int = 0
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    iris_h: float = 0.5
    iris_v: float = 0.5
    face_box: tuple | None = None
    brightness: float = 0.0
    variance: float = 0.0

@dataclass
class Box:
    conf: float
    x1: int; y1: int; x2: int; y2: int

@dataclass
class YoloResult:
    phones: list = field(default_factory=list)
    n_persons: int = 0

@dataclass
class Violation:
    type: str
    severity: int
    t_start: float
    duration: float
    screenshot: str
    details: dict = field(default_factory=dict)
