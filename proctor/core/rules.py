"""HoldRule + RuleEngine state machine on time.monotonic()."""
import time
from .models import FaceResult, YoloResult

class HoldRule:
    def __init__(self, hold, cooldown=5.0, gap_tolerance=0.3):
        self.hold = hold
        self.cooldown = cooldown
        self.gap = gap_tolerance
        self._start = None
        self._gap_start = None
        self._last_fire = -1e9

    def update(self, cond: bool, now: float) -> bool:
        if cond:
            if self._start is None:
                self._start = now
            self._gap_start = None
            if now - self._start >= self.hold and now - self._last_fire >= self.cooldown:
                self._last_fire = now
                return True
            return False
        else:
            if self._start is None:
                return False
            if self._gap_start is None:
                self._gap_start = now
            if now - self._gap_start > self.gap:
                self._start = None
                self._gap_start = None
            return False

    def active_duration(self, now):
        return (now - self._start) if self._start else 0.0


class RuleEngine:
    """Consumes latest FaceResult + YoloResult, emits violation type strings."""

    def __init__(self, cfg, calib_base=None):
        r = cfg["rules"]
        g = r.get("gap_tolerance", 0.3)
        self.cfg = cfg
        self.calib = calib_base or {}
        self.rules = {
            "PHONE_DETECTED": HoldRule(r["phone_detected"]["hold"], r["phone_detected"]["cooldown"], g),
            "PHONE_RAISED": HoldRule(r["phone_raised"]["hold"], r["phone_raised"]["cooldown"], g),
            "PHONE_AIMED": HoldRule(r["phone_aimed"]["hold"], r["phone_aimed"]["cooldown"], g),
            "GAZE_DOWN": HoldRule(r["gaze_down"]["hold"], r["gaze_down"]["cooldown"], g),
            "GAZE_LEFT": HoldRule(r["gaze_side"]["hold"], r["gaze_side"]["cooldown"], g),
            "GAZE_RIGHT": HoldRule(r["gaze_side"]["hold"], r["gaze_side"]["cooldown"], g),
            "NO_FACE": HoldRule(r["no_face"]["hold"], r["no_face"]["cooldown"], g),
            "MULTI_FACE": HoldRule(r["multi_face"]["hold"], r["multi_face"]["cooldown"], g),
            "CAMERA_COVERED": HoldRule(r["camera_covered"]["hold"], r["camera_covered"]["cooldown"], g),
        }
        self.face = FaceResult()
        self.yolo = YoloResult()
        self.phone_voted = False
        self.debug = {}
        self._raised_start = None  # непрерывное удержание телефона в зоне съёмки

    def on_face(self, f: FaceResult):
        self.face = f

    def on_yolo(self, y: YoloResult, voted: bool):
        self.yolo = y
        self.phone_voted = voted

    def _in_shoot_zone(self) -> bool:
        """Phone center above chin or intersects expanded face box."""
        if not self.yolo.phones or not self.face.face_box:
            return bool(self.yolo.phones)  # fallback: any phone counts as raised
        fx1, fy1, fx2, fy2 = self.face.face_box
        chin_y = fy2 - (fy2 - fy1) * 0.15
        for p in self.yolo.phones:
            cx, cy = (p.x1 + p.x2) / 2, (p.y1 + p.y2) / 2
            expand = (fx1 - 60, fy1 - 60, fx2 + 60, fy2 + 60)
            if cy < chin_y or (expand[0] < cx < expand[2] and expand[1] < cy < expand[3]):
                return True
        return False

    def tick(self, now=None):
        now = now if now is not None else time.monotonic()
        f, cfg, out = self.face, self.cfg["rules"], []
        yaw0 = self.calib.get("yaw", 0.0)
        pitch0 = self.calib.get("pitch", 0.0)
        # MAD-адаптивные пороги: разброс калибровки расширяет допуск
        yaw_mad = self.calib.get("yaw_mad", 5.0)
        pitch_mad = self.calib.get("pitch_mad", 5.0)
        yaw_thresh = max(cfg["gaze_side"]["yaw_thresh"], yaw_mad * 3.0)
        pitch_thresh = max(cfg["gaze_down"]["pitch_thresh"], pitch_mad * 3.0)
        dyaw, dpitch = f.yaw - yaw0, f.pitch - pitch0
        # iris deviation from calib (0..1)
        iris_h0 = self.calib.get("iris_h", 0.5)
        d_iris = abs(f.iris_h - iris_h0)

        c_phone = self.phone_voted
        c_raised = c_phone and self._in_shoot_zone()
        # AIMED: непрерывное удержание в зоне съёмки, не зависит от срабатывания RAISED
        if c_raised:
            if self._raised_start is None:
                self._raised_start = now
        else:
            self._raised_start = None
        raised_dur = (now - self._raised_start) if self._raised_start is not None else 0.0
        aimed_active = raised_dur >= cfg["phone_aimed"]["hold"] and c_raised
        c_down = (dpitch > pitch_thresh) or (f.iris_v > 0.72)
        c_left = (dyaw > yaw_thresh) or (f.iris_h < iris_h0 - cfg["gaze_side"]["iris_thresh"])
        c_right = (dyaw < -yaw_thresh) or (f.iris_h > iris_h0 + cfg["gaze_side"]["iris_thresh"])
        c_no = f.n_faces == 0
        c_multi = (f.n_faces >= 2) or (self.yolo.n_persons >= 2)
        c_cover = (f.brightness < cfg["camera_covered"]["brightness_thresh"] and
                   f.variance < cfg["camera_covered"]["variance_thresh"])

        conds = {"PHONE_DETECTED": c_phone, "PHONE_RAISED": c_raised,
                 "PHONE_AIMED": aimed_active, "GAZE_DOWN": c_down,
                 "GAZE_LEFT": c_left, "GAZE_RIGHT": c_right,
                 "NO_FACE": c_no, "MULTI_FACE": c_multi, "CAMERA_COVERED": c_cover}
        key_of = {"PHONE_DETECTED": "phone_detected", "PHONE_RAISED": "phone_raised",
                  "PHONE_AIMED": "phone_aimed", "GAZE_DOWN": "gaze_down",
                  "GAZE_LEFT": "gaze_side", "GAZE_RIGHT": "gaze_side",
                  "NO_FACE": "no_face", "MULTI_FACE": "multi_face",
                  "CAMERA_COVERED": "camera_covered"}
        self.debug = dict(dyaw=dyaw, dpitch=dpitch, iris_h=f.iris_h,
                          n_faces=f.n_faces, phones=len(self.yolo.phones),
                          bright=f.brightness, conds=conds)
        for k, c in conds.items():
            if not cfg.get(key_of[k], {}).get("enabled", True):
                continue
            if self.rules[k].update(c, now):
                out.append(k)
        return out
