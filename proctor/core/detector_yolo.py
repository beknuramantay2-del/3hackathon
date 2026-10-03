"""YOLO thread ~8 FPS, imgsz 320-416, queue 1. Classes: person(0), cell phone(67)."""
import time
from collections import deque
from PyQt6.QtCore import QThread, pyqtSignal

from .models import YoloResult, Box

class DetectorYolo(QThread):
    result_ready = pyqtSignal(object)

    def __init__(self, model_name="yolov8n.pt", imgsz=384, conf_phone=0.35,
                 conf_person=0.5, vote_window=8, vote_threshold=4, parent=None):
        super().__init__(parent)
        self.model_name = model_name
        self.imgsz = imgsz
        self.conf_phone = conf_phone
        self.conf_person = conf_person
        self.vote_window = vote_window
        self.vote_threshold = vote_threshold
        self._run = True
        self._frame = None
        self._votes = deque(maxlen=vote_window)
        self.model = None
        self.PERSON = 0
        self.PHONE = 67

    def push(self, frame):
        self._frame = frame  # queue size 1: old dropped

    def run(self):
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.model_name)
        except Exception as e:
            print(f"[YOLO] cannot load {self.model_name}: {e}, mock mode")
            self.model = None
        import time as t
        last = 0
        while self._run:
            if self._frame is None:
                self.msleep(20)
                continue
            now = t.time()
            if now - last < 1.0 / 8:  # ~8 FPS
                self.msleep(10)
                continue
            last = now
            frame = self._frame
            if self.model is None:
                self._votes.append(0)
                self.result_ready.emit(YoloResult(phones=[], n_persons=0))
                continue
            try:
                r = self.model.predict(frame, imgsz=self.imgsz, verbose=False)[0]
                phones, persons = [], 0
                for b in r.boxes:
                    cls = int(b.cls[0]); conf = float(b.conf[0])
                    if cls == self.PHONE and conf >= self.conf_phone:
                        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
                        phones.append(Box(conf, x1, y1, x2, y2))
                    elif cls == self.PERSON and conf >= self.conf_person:
                        persons += 1
                self._votes.append(1 if phones else 0)
                self.result_ready.emit(YoloResult(phones=phones, n_persons=persons))
            except Exception as e:
                print("[YOLO] infer error:", e)
                self.msleep(200)

    def stop(self):
        self._run = False
        self.wait(1000)

    def phone_voted(self) -> bool:
        """phone in >= vote_threshold of last vote_window frames."""
        return sum(self._votes) >= self.vote_threshold
