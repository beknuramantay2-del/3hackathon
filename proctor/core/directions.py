"""Independent directional states with dead zone, exit hysteresis and dwell."""
class DirectionState:
    def __init__(self, kind, dwell=.12):
        self.kind, self.dwell = kind, dwell
        self.entry_gates = {}
        self.state = "UNKNOWN"
        self.candidate, self.since = None, 0.

    def reset(self):
        self.state, self.candidate = "UNKNOWN", None

    def update(self, x, y, base, now, valid=True):
        if not valid:
            self.reset()
            return self.state
        # Personalized signed deltas learned from labelled calibration targets.
        calibrated = base.get(self.kind + "_targets", {})
        cx, cy = (base.get("yaw",0),base.get("pitch",0)) if self.kind == "head" else (base.get("iris_h",.5),base.get("iris_v",.5))
        dx, dy = x-cx, y-cy
        tx = base.get("head_x_threshold",18.) if self.kind == "head" else base.get("gaze_x_threshold",.16)
        ty = base.get("head_y_threshold",12.) if self.kind == "head" else base.get("gaze_y_threshold",.2)
        # Default head +yaw -> RIGHT, +pitch -> DOWN. Gaze image-right -> subject LEFT.
        defaults = {"LEFT":(-tx if self.kind == "head" else tx,0),
                    "RIGHT":(tx if self.kind == "head" else -tx,0), "UP":(0,-ty), "DOWN":(0,ty)}
        scores = {}
        for label,(vx,vy) in defaults.items():
            personalized = label in calibrated
            vx,vy = calibrated.get(label,(vx,vy))
            axis = dx if label in ("LEFT","RIGHT") else dy
            delta = vx if label in ("LEFT","RIGHT") else vy
            threshold = tx if label in ("LEFT","RIGHT") else ty
            # Retain current state down to 70% of entry threshold.
            entry = max(threshold,abs(delta)*.45) if personalized else threshold
            self.entry_gates[label] = entry
            gate = entry * (.7 if self.state == label else 1.)
            scores[label] = axis*(1 if delta > 0 else -1)/max(gate,1e-6)
        label = max(scores,key=scores.get)
        wanted = label if scores[label] >= 1 else "CENTER"
        if wanted != self.candidate:
            self.candidate, self.since = wanted, now
        if now-self.since >= self.dwell or self.state == "UNKNOWN":
            self.state = wanted
        return self.state
