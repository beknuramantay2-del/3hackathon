"""Worker snapshots; monotonic timestamps and sequence IDs travel with detections."""
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
    face_boxes: list = field(default_factory=list)
    left_eye: tuple | None = None
    right_eye: tuple | None = None
    pose_method: str = ""
    gaze_preview_valid: bool = False
    pose_valid: bool = True
    gaze_valid: bool = True
    primary_changed: bool = False
    gaze_reason: str = ""
    pose_reason: str = ""
    eye_points: list = field(default_factory=list)
    eye_boxes: list = field(default_factory=list)
    seq: int = -1
    captured_at: float = 0.0
    processed_at: float = 0.0
    timings: dict = field(default_factory=dict)
    error: str = ""

@dataclass
class Box:
    conf: float
    x1: int; y1: int; x2: int; y2: int
    track_id: int = -1
    confirmed: bool = False
    observed: bool = True
    velocity: tuple = (0.,0.,0.,0.)
    strong_at: float | None = None
    supported: bool = False
    support_kind: str = ""
    source: str = "global"
    detail_agreement: bool = False

@dataclass
class YoloResult:
    phones: list = field(default_factory=list)
    n_persons: int = 0
    persons: list = field(default_factory=list)
    phone_voted: bool = False
    candidates: list = field(default_factory=list)
    inference_size: int = 0
    detail_region: str = ""
    detail_rect: tuple | None = None
    detail_inference_size: int = 0
    seq: int = -1
    captured_at: float = 0.0
    processed_at: float = 0.0
    timings: dict = field(default_factory=dict)
    error: str = ""

@dataclass
class HandResult:
    boxes: list = field(default_factory=list)
    seq: int = -1
    captured_at: float = 0.0
    error: str = ""
    timings: dict = field(default_factory=dict)

@dataclass
class Violation:
    type: str
    severity: int
    t_start: float
    duration: float
    screenshot: str
    details: dict = field(default_factory=dict)
