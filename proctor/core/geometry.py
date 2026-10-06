"""Shared geometry for live and replay. Directions are subject-relative, not mirrored."""
import cv2
import numpy as np

PNP_IDX = [1, 152, 33, 263, 61, 291]  # nose, chin, eyes, mouth; exact matching order
MODEL_3D = np.array([(0,0,0), (0,330,65), (-225,-170,135),
                     (225,-170,135), (-150,150,125), (150,150,125)], dtype=np.float64)

def head_pose(pts, w, h):
    cam = np.array([[w,0,w/2], [0,w,h/2], [0,0,1]], dtype=np.float64)
    image = np.asarray(pts[PNP_IDX], dtype=np.float64)
    try:
        ok, rv, tv = cv2.solvePnP(MODEL_3D, image, cam, np.zeros((4,1)), flags=cv2.SOLVEPNP_SQPNP)
        # Global SQPnP avoids the negative-depth/180° iterative solution.
        if ok and tv[2,0] > 0:
            rv,tv = cv2.solvePnPRefineLM(MODEL_3D,image,cam,None,rv,tv)
        if not ok or tv[2,0] <= 0:
            return None
        projected, _ = cv2.projectPoints(MODEL_3D, rv, tv, cam, None)
        err = np.linalg.norm(projected.reshape(-1,2)-image, axis=1).mean()
        if err > max(6., w*.035):
            return None
        rot, _ = cv2.Rodrigues(rv)
        pitch, yaw, roll = cv2.RQDecomp3x3(rot)[0]
        if max(abs(pitch), abs(yaw)) > 80:
            return None
        return float(yaw), float(pitch), float(roll)
    except (cv2.error, ValueError):
        return None

def eye_ratio(pts, a, b, top, bottom, iris_index):
    """Eye-local axes remove in-plane head roll; each iris belongs to its own eye."""
    if len(pts) <= iris_index:
        return None
    axis = pts[b] - pts[a]
    width = np.linalg.norm(axis)
    if width < 4:
        return None
    ex = axis/width
    ey = np.array([-ex[1], ex[0]])
    if ey[1] < 0:
        ey = -ey
    height = np.dot(pts[bottom]-pts[top], ey)
    if height/width < .12:  # blink/closed eye is UNKNOWN, not DOWN
        return None
    center = pts[iris_index]  # 468 and 473 are iris centres
    horizontal = np.dot(center-pts[a], ex)/width
    vertical = np.dot(center-pts[top], ey)/height
    if not (-.1 <= horizontal <= 1.1 and -.25 <= vertical <= 1.25):
        return None
    return float(np.clip(horizontal,0,1)), float(np.clip(vertical,0,1))

def eye_gaze(pts):
    # Both axes run image-left -> image-right.
    right = eye_ratio(pts,33,133,159,145,468)
    left = eye_ratio(pts,362,263,386,374,473)
    valid = [e for e in (left,right) if e is not None]
    if not valid:
        return left, right, None
    if len(valid) == 2 and np.max(np.abs(np.subtract(left,right))) > .4:
        return left, right, None
    return left, right, tuple(np.mean(valid, axis=0))

class TimeEMA:
    def __init__(self, tau=.08):
        self.tau, self.value, self.last = tau, None, None

    def update(self, value, now):
        a = np.asarray(value, dtype=float)
        if self.value is None or self.last is None or now-self.last > .5:
            self.value = a
        else:
            alpha = 1-np.exp(-max(0.,now-self.last)/self.tau)
            self.value = self.value + alpha*(a-self.value)
        self.last = now
        return tuple(self.value)

    def reset(self):
        self.value = self.last = None
