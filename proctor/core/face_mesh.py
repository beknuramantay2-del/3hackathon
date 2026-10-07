"""Low-rate full-frame face scan + primary-face ROI mesh/iris, with frame-space outputs."""
import time
import cv2
import numpy as np
from .worker import LatestWorker
from .models import FaceResult
from .camera import CameraThread
from .geometry import head_pose_mesh,eye_gaze,TimeEMA
from .tracking import iou

class FaceMeshThread(LatestWorker):
    def __init__(self,max_faces=2,parent=None,target_fps=15,max_width=640,
                 min_detection_confidence=.6,min_tracking_confidence=.6,adaptive=True):
        super().__init__(target_fps,parent,adaptive)
        self.max_faces,self.max_width=max_faces,max_width
        self.detection_conf,self.tracking_conf=min_detection_confidence,min_tracking_confidence
        self.mesh=self.detector=None
        self.box_filter=TimeEMA(.04)
        self.pose_filter,self.left_filter,self.right_filter=TimeEMA(.06),TimeEMA(.04),TimeEMA(.04)
        self.primary=None
        self.last_face_at=0.
        self.next_scan=0.
        self.boxes=[]
        self.scanned_at=0.

    def setup(self):
        import mediapipe as mp
        if not hasattr(mp,"solutions"):
            raise RuntimeError("Нужен MediaPipe 0.10.14–0.10.21 / Python 3.10–3.11")
        self.detector=mp.solutions.face_detection.FaceDetection(model_selection=0,
            min_detection_confidence=self.detection_conf)
        self.mesh=mp.solutions.face_mesh.FaceMesh(max_num_faces=1,refine_landmarks=True,
            min_detection_confidence=self.detection_conf,min_tracking_confidence=self.tracking_conf)

    def teardown(self):
        for model in (self.mesh,self.detector):
            if model:
                model.close()

    def process(self,packet):
        frame=packet.frame
        h,w=frame.shape[:2]
        now=packet.captured_at
        brightness,variance=CameraThread.brightness_variance(frame)
        start=time.perf_counter()
        if now>=self.next_scan:
            scale=min(1.,self.max_width/w)
            small=frame if scale==1 else cv2.resize(frame,(int(w*scale),int(h*scale)))
            detections=self.detector.process(cv2.cvtColor(small,cv2.COLOR_BGR2RGB)).detections or []
            boxes=[]
            for detection in detections:
                b=detection.location_data.relative_bounding_box
                x1,y1=max(0,int(b.xmin*w)),max(0,int(b.ymin*h))
                x2,y2=min(w,int((b.xmin+b.width)*w)),min(h,int((b.ymin+b.height)*h))
                if x2-x1>=30 and y2-y1>=30:
                    boxes.append((x1,y1,x2,y2))
            self.boxes=boxes[:self.max_faces]
            self.scanned_at=now
            self.next_scan=now+.2
        scan_end=time.perf_counter()
        visible=self.boxes if now-self.scanned_at<=.45 else []
        if visible:
            selected=max(visible,key=lambda b:iou(self.primary,b)) if self.primary and now-self.last_face_at<.7 else max(visible,key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
        else:
            selected=self.primary if self.primary and now-self.last_face_at<.4 else None
        result=FaceResult(n_faces=0,brightness=brightness,variance=variance,seq=packet.seq,
            captured_at=now,pose_valid=False,gaze_valid=False,timings={"face_scan":scan_end-start})
        if selected is None:
            result.gaze_reason=result.pose_reason="Лицо не найдено; камера/свет/ракурс"
            result.processed_at=time.monotonic()
            return result
        changed=self.primary is not None and iou(self.primary,selected)<.1
        if changed or now-self.last_face_at>.7:
            for f in (self.pose_filter,self.left_filter,self.right_filter,self.box_filter):
                f.reset()
        x1,y1,x2,y2=selected
        bw,bh=x2-x1,y2-y1
        rx1,ry1=max(0,int(x1-bw*.25)),max(0,int(y1-bh*.35))
        rx2,ry2=min(w,int(x2+bw*.25)),min(h,int(y2+bh*.30))
        roi=frame[ry1:ry2,rx1:rx2]
        if roi.size==0:
            result.pose_reason=result.gaze_reason="Пустая ROI лица"
            result.processed_at=time.monotonic()
            return result
        rgb=cv2.cvtColor(roi,cv2.COLOR_BGR2RGB)
        rgb.flags.writeable=False
        mesh_result=self.mesh.process(rgb)
        mesh_end=time.perf_counter()
        result.timings["mediapipe"]=mesh_end-scan_end
        if not mesh_result.multi_face_landmarks:
            result.pose_reason=result.gaze_reason="FaceMesh потерял landmarks; направление неизвестно"
            result.n_faces=len(visible)  # face scan is presence evidence, not valid gaze evidence
            result.face_boxes=list(visible)
            result.processed_at=time.monotonic()
            return result
        lm=mesh_result.multi_face_landmarks[0].landmark
        pts=np.array([(p.x*(rx2-rx1)+rx1,p.y*(ry2-ry1)+ry1) for p in lm])
        box=(max(0,int(pts[:,0].min())),max(0,int(pts[:,1].min())),
             min(w,int(pts[:,0].max())),min(h,int(pts[:,1].max())))
        self.primary=box
        self.last_face_at=now
        result.face_box=tuple(map(int,self.box_filter.update(box,now)))
        # Exclude only the scan box corresponding to this primary; keep every second face.
        result.face_boxes=[result.face_box]+[b for b in visible if iou(selected,b)<.45]
        result.n_faces=len(result.face_boxes)
        result.primary_changed=changed
        s=time.perf_counter()
        xyz=np.array([(p.x*(rx2-rx1)+rx1,p.y*(ry2-ry1)+ry1,p.z*(rx2-rx1)) for p in lm])
        pose=head_pose_mesh(xyz)
        if pose is not None:
            result.yaw,result.pitch,result.roll=self.pose_filter.update(pose,now)
            result.pose_valid=True
        else:
            result.pose_reason="Face-local basis: плохая геометрия/крайний ракурс"
        result.timings["head_pose"]=time.perf_counter()-s
        s=time.perf_counter()
        left,right,gaze=eye_gaze(pts,xyz)
        result.left_eye=self.left_filter.update(left,now) if left is not None else None
        result.right_eye=self.right_filter.update(right,now) if right is not None else None
        valid=[e for e in (result.left_eye,result.right_eye) if e is not None]
        if gaze is not None and len(valid)==2:
            result.iris_h,result.iris_v=map(float,np.mean(valid,axis=0))
            result.gaze_valid=True
            result.gaze_reason="Оба глаза"
        else:
            result.gaze_reason="Зрачки не читаются: моргание/малые глаза/перекрытие или несогласие глаз"
        result.eye_points=[tuple(map(int,pts[i])) for i,e in ((468,right),(473,left)) if e is not None]
        for indices,e in (((33,133,159,145),right),((362,263,386,374),left)):
            if e is not None:
                eye=pts[list(indices)]
                result.eye_boxes.append((int(eye[:,0].min())-3,int(eye[:,1].min())-3,int(eye[:,0].max())+3,int(eye[:,1].max())+3))
        result.timings["eye_gaze"]=time.perf_counter()-s
        result.processed_at=time.monotonic()
        return result
