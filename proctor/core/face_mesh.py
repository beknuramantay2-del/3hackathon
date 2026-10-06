"""FaceMesh on downscaled full frame; independent eye smoothing, stable primary ROI.
Full-frame multi-face scanning is intentional: cropping to one face loses a second face.
"""
import time
import cv2
import numpy as np
from .worker import LatestWorker
from .models import FaceResult
from .camera import CameraThread
from .geometry import head_pose, eye_gaze, TimeEMA
from .tracking import iou

class FaceMeshThread(LatestWorker):
    def __init__(self, max_faces=2, parent=None, target_fps=15, max_width=640,
                 min_detection_confidence=.6, min_tracking_confidence=.6, adaptive=True):
        super().__init__(target_fps,parent,adaptive)
        self.max_faces,self.max_width = max_faces,max_width
        self.min_detection_confidence,self.min_tracking_confidence = min_detection_confidence,min_tracking_confidence
        self.mesh = None
        self.box_filter = TimeEMA(.04)
        self.pose_filter,self.left_filter,self.right_filter = TimeEMA(.08),TimeEMA(.06),TimeEMA(.06)
        self.primary = None
        self.last_face_at = 0.

    def setup(self):
        import mediapipe as mp
        if not hasattr(mp,"solutions"):
            raise RuntimeError("Нужен MediaPipe 0.10.14–0.10.21 и Python 3.10–3.11 (Face Mesh API)")
        self.mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=self.max_faces,refine_landmarks=True,
            min_detection_confidence=self.min_detection_confidence,
            min_tracking_confidence=self.min_tracking_confidence)

    def teardown(self):
        if self.mesh:
            self.mesh.close()

    def process(self, packet):
        frame = packet.frame
        h,w = frame.shape[:2]
        scale = min(1.,self.max_width/w)
        small = frame if scale == 1 else cv2.resize(frame,(int(w*scale),int(h*scale)))
        brightness,variance = CameraThread.brightness_variance(small)
        s = time.perf_counter()
        rgb = cv2.cvtColor(small,cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        res = self.mesh.process(rgb)
        mp_end = time.perf_counter()
        faces = []
        for lm in res.multi_face_landmarks or []:
            pts = np.array([(p.x*w,p.y*h) for p in lm.landmark])
            x1,y1 = np.maximum(0,pts.min(axis=0)).astype(int)
            x2,y2 = np.minimum((w-1,h-1),pts.max(axis=0)).astype(int)
            if x2-x1 >= 35 and y2-y1 >= 35:
                faces.append(((int(x1),int(y1),int(x2),int(y2)),pts))
        result = FaceResult(n_faces=len(faces),face_boxes=[a[0] for a in faces],
                            brightness=brightness,variance=variance,seq=packet.seq,
                            captured_at=packet.captured_at,pose_valid=False,gaze_valid=False,
                            timings={"mediapipe":mp_end-s})
        if not faces:
            result.processed_at = time.monotonic()
            return result
        if self.primary is None or packet.captured_at-self.last_face_at > .7:
            selected = max(faces,key=lambda f:(f[0][2]-f[0][0])*(f[0][3]-f[0][1]))
            changed = self.primary is not None
        else:
            selected = max(faces,key=lambda f:iou(self.primary,f[0]))
            changed = iou(self.primary,selected[0]) < .1
        if changed:
            self.pose_filter.reset(); self.left_filter.reset(); self.right_filter.reset(); self.box_filter.reset()
        self.primary = selected[0]
        self.last_face_at = packet.captured_at
        result.face_box = tuple(map(int,self.box_filter.update(self.primary,packet.captured_at)))
        result.face_boxes = [result.face_box if b == self.primary else b for b in result.face_boxes]
        result.primary_changed = changed
        pts = selected[1]
        s = time.perf_counter()
        pose = head_pose(pts,w,h)
        if pose is not None:
            result.yaw,result.pitch,result.roll = self.pose_filter.update(pose,packet.captured_at)
            result.pose_valid = True
        result.timings["head_pose"] = time.perf_counter()-s
        s = time.perf_counter()
        left,right,gaze = eye_gaze(pts)
        result.left_eye = self.left_filter.update(left,packet.captured_at) if left is not None else None
        result.right_eye = self.right_filter.update(right,packet.captured_at) if right is not None else None
        valid = [e for e in (result.left_eye,result.right_eye) if e is not None]
        if gaze is not None and valid:
            result.iris_h,result.iris_v = np.mean(valid,axis=0)
            result.gaze_valid = True
        result.timings["eye_gaze"] = time.perf_counter()-s
        result.processed_at = time.monotonic()
        return result
