"""FaceMesh: faces, head pose (solvePnP), iris. Mirror only for preview."""
import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from .models import FaceResult
from .camera import CameraThread

# solvePnP pairs: (mesh idx -> 3d model)
_PNP_IDX = [1, 152, 33, 263, 61, 291]
_MODEL_3D = np.array([
    (0, 0, 0), (-165, -170, -135), (165, -170, -135),
    (-150, -150, -125), (150, -150, -125),
    (0, -330, -65),
], dtype=np.float64)

class FaceMeshThread(QThread):
    result_ready = pyqtSignal(object)

    def __init__(self, max_faces=2, parent=None):
        super().__init__(parent)
        self.max_faces = max_faces
        self._run = True
        self._frame = None
        self.mesh = None

    def push(self, frame):
        self._frame = frame

    def run(self):
        try:
            import mediapipe as mp
            self.mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=self.max_faces, refine_landmarks=True,
                min_detection_confidence=0.5, min_tracking_confidence=0.5)
        except Exception as e:
            print("[FaceMesh] init fail:", e)
            self.mesh = None
        while self._run:
            if self._frame is None:
                self.msleep(15)
                continue
            frame = self._frame
            h, w = frame.shape[:2]
            bright, var = CameraThread.brightness_variance(frame)
            if self.mesh is None:
                self.result_ready.emit(FaceResult(brightness=bright, variance=var))
                self.msleep(50)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = self.mesh.process(rgb)
            if not res.multi_face_landmarks:
                self.result_ready.emit(FaceResult(brightness=bright, variance=var))
                continue
            lm = res.multi_face_landmarks[0]
            pts = np.array([(p.x * w, p.y * h) for p in lm.landmark])
            # head pose
            img_pts = np.array([(pts[i][0], pts[i][1]) for i in _PNP_IDX], dtype=np.float64)
            focal = w
            cam = np.array([[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]], dtype=np.float64)
            dist = np.zeros((4, 1))
            try:
                _, rvec, tvec = cv2.solvePnP(_MODEL_3D, img_pts, cam, dist)
                rmat, _ = cv2.Rodrigues(rvec)
                import math
                yaw = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))
                pitch = math.degrees(math.atan2(-rmat[2, 0], (rmat[2, 1]**2 + rmat[2, 2]**2) ** 0.5))
                roll = math.degrees(math.atan2(rmat[2, 1], rmat[2, 2]))
            except Exception:
                yaw = pitch = roll = 0.0
            # iris: refine landmarks 468-477; horizontal vs eye corners
            try:
                iris = pts[468:478].mean(axis=0)
                # left eye 33-133, right 362-263 -> use dominant (larger) eye
                def ratio(c_out, c_in):
                    d = abs(pts[c_in][0] - pts[c_out][0]) + 1e-6
                    return (iris[0] - pts[c_out][0]) / d
                iris_h = (ratio(33, 133) + ratio(362, 263)) / 2
                iris_h = float(np.clip(iris_h, 0, 1))
                top = (pts[159][1] + pts[386][1]) / 2
                bot = (pts[145][1] + pts[374][1]) / 2
                iris_v = float(np.clip((iris[1] - top) / (bot - top + 1e-6), 0, 1))
            except Exception:
                iris_h, iris_v = 0.5, 0.5
            xs, ys = pts[:, 0], pts[:, 1]
            box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            self.result_ready.emit(FaceResult(
                n_faces=len(res.multi_face_landmarks),
                yaw=yaw, pitch=pitch, roll=roll,
                iris_h=iris_h, iris_v=iris_v, face_box=box,
                brightness=bright, variance=var))

    def stop(self):
        self._run = False
        self.wait(1000)
