"""Local nano-YOLO with class filtering and small two-stage IoU tracking."""
import os
import time
from .worker import LatestWorker
from .models import YoloResult, Box
from .tracking import LiteTracker

class DetectorYolo(LatestWorker):
    def __init__(self, model_name="yolov8n.pt", imgsz=384, conf_phone=.35,
                 conf_person=.5, vote_window=8, vote_threshold=4, parent=None,
                 target_fps=8, device="auto", threads=2, offline=True, adaptive=True,
                 phone_class=67, person_class=0):
        super().__init__(target_fps,parent,adaptive)
        self.model_name,self.imgsz = model_name,imgsz
        self.conf_phone,self.conf_person = conf_phone,conf_person
        self.PHONE,self.PERSON = phone_class,person_class
        self.device,self.threads,self.offline = device,threads,offline
        self.budget.imgsz = imgsz
        # Frame-voting parameters retained for API compatibility. Time-based HoldRule
        # and confirmed tracks replace variable-FPS vote windows.
        self.tracker = LiteTracker(strong=conf_phone)
        self.model = None

    def setup(self):
        if self.offline and not os.path.isfile(self.model_name):
            raise FileNotFoundError(f"Нет локальных весов: {self.model_name}. Запустите tools/fetch_weights.py заранее")
        import torch
        torch.set_num_threads(self.threads)
        if self.device == "auto":
            self.device = "0" if torch.cuda.is_available() else "cpu"
        from ultralytics import YOLO
        self.model = YOLO(self.model_name)
        import numpy as np
        self.model.predict(np.zeros((320,320,3),dtype=np.uint8), imgsz=self.budget.imgsz,
                           classes=[self.PHONE,self.PERSON], device=self.device, verbose=False)

    def process(self, packet):
        start = time.perf_counter()
        r = self.model.predict(packet.frame,imgsz=self.budget.imgsz,conf=max(.1,self.conf_phone*.5),
                               classes=[self.PHONE,self.PERSON],device=self.device,
                               max_det=12,verbose=False)[0]
        inferred = time.perf_counter()
        phones,persons = [],[]
        if r.boxes is not None:
            # Transfer all boxes once instead of repeated tensor synchronizations.
            data = r.boxes.data.cpu().numpy()
            for row in data:
                x1,y1,x2,y2,confidence,cls = row[:6]
                b = Box(float(confidence),*map(int,(x1,y1,x2,y2)))
                if int(cls) == self.PHONE:
                    phones.append(b)
                elif int(cls) == self.PERSON and confidence >= self.conf_person:
                    persons.append(b)
        tracking_started = time.perf_counter()
        tracks = self.tracker.update(phones,packet.captured_at)
        ended = time.perf_counter()
        voted = any(b.confirmed and b.observed and b.conf >= self.conf_phone for b in tracks)
        return YoloResult(phones=tracks,n_persons=len(persons),persons=persons,phone_voted=voted,
                          seq=packet.seq,captured_at=packet.captured_at,processed_at=time.monotonic(),
                          timings={"yolo":inferred-start,"postprocess":tracking_started-inferred,"tracker":ended-tracking_started})

    def phone_voted(self):
        result = self.output.peek()
        return bool(result and result.phone_voted)
