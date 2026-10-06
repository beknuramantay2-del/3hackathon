"""Five labelled targets, 20 seconds. Skip transitions, trim outliers, reject empty data."""
import numpy as np

POSES = ("CENTER", "LEFT", "RIGHT", "UP", "DOWN")

class CalibrationError(ValueError):
    pass

class Calibration:
    def __init__(self, duration=20.0):
        self.duration = duration
        self.samples = {p:[] for p in POSES}
        self.done = False
        self.base = {}
        self.started_at = None

    def start(self, now):
        self.started_at = now
        self.samples = {p:[] for p in POSES}
        self.done = False

    def phase(self, elapsed):
        return POSES[min(4,max(0,int(elapsed/(self.duration/5))))]

    def add(self, yaw, pitch, iris_h, iris_v, now=None, pose=None):
        if self.done:
            return False
        if now is not None and self.started_at is not None:
            elapsed = now-self.started_at
            if elapsed < 0 or elapsed >= self.duration or elapsed % (self.duration/5) < .7:
                return False
            pose = self.phase(elapsed)
        values = (yaw,pitch,iris_h,iris_v)
        if not np.isfinite(values).all():
            return False
        self.samples[pose or "CENTER"].append(values)
        return True

    @staticmethod
    def trimmed(samples):
        if len(samples) < 8:
            raise CalibrationError("Недостаточно стабильных кадров: повторите калибровку")
        a = np.asarray(samples)
        med = np.median(a,axis=0)
        mad = np.median(np.abs(a-med),axis=0)
        keep = (np.abs(a-med) <= np.maximum(3.5*1.4826*mad, [1.,1.,.015,.015])).all(axis=1)
        a = a[keep]
        if len(a) < 8:
            raise CalibrationError("Слишком много шума: больше света и повторная калибровка")
        return np.median(a,axis=0), np.median(np.abs(a-np.median(a,axis=0)),axis=0)

    def finish(self, center_only=False):
        required = ("CENTER",) if center_only else POSES
        measured = {p:self.trimmed(self.samples[p]) for p in required}
        c,mad = measured["CENTER"]
        if mad[0] > 6 or mad[1] > 6 or max(mad[2:]) > .06:
            raise CalibrationError("Центральная поза нестабильна: повторите")
        self.base = dict(yaw=float(c[0]),pitch=float(c[1]),iris_h=float(c[2]),iris_v=float(c[3]),
                         yaw_mad=float(mad[0]),pitch_mad=float(mad[1]),
                         head_x_threshold=max(10.,float(mad[0]*4)),
                         head_y_threshold=max(8.,float(mad[1]*4)),
                         gaze_x_threshold=max(.06,float(mad[2]*4)),
                         gaze_y_threshold=max(.08,float(mad[3]*4)),
                         head_targets={},gaze_targets={})
        for p in required:
            if p == "CENTER":
                continue
            d = measured[p][0]-c
            axis = 0 if p in ("LEFT","RIGHT") else 1
            # Ignore poorly demonstrated poses; keep conservative defaults for that axis.
            if abs(d[axis]) >= 12:
                self.base["head_targets"][p] = list(map(float,d[:2]))
            if abs(d[axis+2]) >= .08:
                self.base["gaze_targets"][p] = list(map(float,d[2:]))
        # Opposite labels must lie on opposite sides of the neutral position.
        for kind in ("head_targets", "gaze_targets"):
            for a,b,axis in (("LEFT","RIGHT",0),("UP","DOWN",1)):
                targets = self.base[kind]
                if a in targets and b in targets and targets[a][axis]*targets[b][axis] >= 0:
                    targets.pop(a); targets.pop(b)
        self.base["unresolved_targets"] = [p for p in required if p != "CENTER" and p not in self.base["gaze_targets"]]
        self.done = True
        return self.base
