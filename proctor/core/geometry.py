import cv2
import numpy as np

PNP_IDX = [1, 152, 33, 263, 61, 291]
MODEL_3D = np.array(
    [
        (0, 0, 0),
        (0, 330, 65),
        (-225, -170, 135),
        (225, -170, 135),
        (-150, 150, 125),
        (150, 150, 125),
    ],
    dtype=np.float64,
)


def head_pose(pts, w, h):
    cam = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], dtype=np.float64)
    image = np.asarray(pts[PNP_IDX], dtype=np.float64)
    try:
        ok, rv, tv = cv2.solvePnP(
            MODEL_3D, image, cam, np.zeros((4, 1)), flags=cv2.SOLVEPNP_SQPNP
        )

        if ok and tv[2, 0] > 0:
            rv, tv = cv2.solvePnPRefineLM(MODEL_3D, image, cam, None, rv, tv)
        if not ok or tv[2, 0] <= 0:
            return None
        projected, _ = cv2.projectPoints(MODEL_3D, rv, tv, cam, None)
        err = np.linalg.norm(projected.reshape(-1, 2) - image, axis=1).mean()
        if err > max(6.0, w * 0.035):
            return None
        rot, _ = cv2.Rodrigues(rv)
        pitch, yaw, roll = cv2.RQDecomp3x3(rot)[0]
        if max(abs(pitch), abs(yaw)) > 80:
            return None
        return float(yaw), float(pitch), float(roll)
    except (cv2.error, ValueError):
        return None


def eye_ratio(pts, a, b, top, bottom, iris_index):

    if len(pts) <= iris_index:
        return None
    axis = pts[b] - pts[a]
    width = np.linalg.norm(axis)
    if width < 4:
        return None
    ex = axis / width
    ey = np.array([-ex[1], ex[0]])
    if ey[1] < 0:
        ey = -ey
    height = np.dot(pts[bottom] - pts[top], ey)
    if height / width < 0.065:
        return None
    center = pts[iris_index]
    horizontal = np.dot(center - pts[a], ex) / width
    vertical = 0.5 + np.dot(center - (pts[a] + pts[b]) / 2, ey) / (width * 0.30)
    if not (-0.1 <= horizontal <= 1.1 and -0.25 <= vertical <= 1.25):
        return None
    return float(np.clip(horizontal, 0, 1)), float(vertical)


def eye_ratio_3d(pts, xyz, a, b, top, bottom, iris_index):

    if len(xyz) <= iris_index or np.linalg.norm(pts[b] - pts[a]) < 4:
        return None
    axis = xyz[b] - xyz[a]
    width = np.linalg.norm(axis)
    if width < 4:
        return None
    ex = axis / width
    vertical = xyz[152] - xyz[10]
    vertical = vertical - ex * np.dot(vertical, ex)
    norm = np.linalg.norm(vertical)
    if norm < 1:
        return None
    ey = vertical / norm
    if np.dot(xyz[bottom] - xyz[top], ey) / width < 0.065:
        return None
    center = xyz[iris_index]
    h = np.dot(center - xyz[a], ex) / width
    v = 0.5 + np.dot(center - (xyz[a] + xyz[b]) / 2, ey) / (width * 0.30)
    if not (-0.1 <= h <= 1.1 and -0.25 <= v <= 1.25):
        return None
    return float(np.clip(h, 0, 1)), float(v)


def eye_gaze(pts, xyz=None):

    if xyz is not None:
        right = eye_ratio_3d(pts, xyz, 33, 133, 159, 145, 468)
        left = eye_ratio_3d(pts, xyz, 362, 263, 386, 374, 473)
    else:
        right = eye_ratio(pts, 33, 133, 159, 145, 468)
        left = eye_ratio(pts, 362, 263, 386, 374, 473)
    valid = [e for e in (left, right) if e is not None]
    if not valid:
        return left, right, None
    if len(valid) == 2 and (
        abs(left[0] - right[0]) > 0.4 or abs(left[1] - right[1]) > 1.0
    ):
        return left, right, None
    return left, right, tuple(np.mean(valid, axis=0))


class TimeEMA:
    def __init__(self, tau=0.08):
        self.tau, self.value, self.last = tau, None, None

    def update(self, value, now):
        a = np.asarray(value, dtype=float)
        if self.value is None or self.last is None or now - self.last > 0.5:
            self.value = a
        else:
            alpha = 1 - np.exp(-max(0.0, now - self.last) / self.tau)
            self.value = self.value + alpha * (a - self.value)
        self.last = now
        return tuple(self.value)

    def reset(self):
        self.value = self.last = None


def head_pose_mesh(xyz):

    a = np.asarray(xyz, dtype=float)
    if len(a) <= 263 or not np.isfinite(a).all():
        return None
    ex = a[263] - a[33]
    width = np.linalg.norm(ex)
    if width < 20:
        return None
    ex /= width
    ey = a[152] - a[10]
    ey -= ex * np.dot(ey, ex)
    length = np.linalg.norm(ey)
    if length < width * 0.7:
        return None
    ey /= length
    rot = np.column_stack((ex, ey, np.cross(ex, ey)))
    pitch, yaw, roll = cv2.RQDecomp3x3(rot)[0]
    if abs(yaw) > 75 or abs(pitch) > 75 or abs(roll) > 65:
        return None
    return float(yaw), float(pitch), float(roll)
