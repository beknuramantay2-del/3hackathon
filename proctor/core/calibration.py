"""Calibration 5s: median + MAD of yaw/pitch/iris."""
import numpy as np

class Calibration:
    def __init__(self, duration=5.0):
        self.duration = duration
        self.samples = []
        self.done = False
        self.base = dict(yaw=0.0, pitch=0.0, iris_h=0.5, iris_v=0.5,
                         yaw_mad=5.0, pitch_mad=5.0)

    def add(self, yaw, pitch, iris_h, iris_v):
        if self.done:
            return False
        self.samples.append((yaw, pitch, iris_h, iris_v))
        return True

    def finish(self):
        a = np.array(self.samples) if self.samples else np.zeros((1, 4))
        med = np.median(a, axis=0)
        mad = np.median(np.abs(a - med), axis=0) + 1e-6
        self.base = dict(yaw=float(med[0]), pitch=float(med[1]),
                         iris_h=float(med[2]), iris_v=float(med[3]),
                         yaw_mad=float(mad[0] + 2.0), pitch_mad=float(mad[1] + 2.0))
        self.done = True
        return self.base
