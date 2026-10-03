"""CameraThread: cv2 capture 640x480, queue size 1."""
import time
import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

class CameraThread(QThread):
    frame_ready = pyqtSignal(object)

    def __init__(self, width=640, height=480, video_file=None, index=0, parent=None):
        super().__init__(parent)
        self.width = width
        self.height = height
        self.video_file = video_file
        self.index = index
        self._run = True
        self.latest = None

    def run(self):
        src = self.video_file if self.video_file else self.index
        cap = cv2.VideoCapture(src)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        while self._run:
            ok, frame = cap.read()
            if not ok:
                if self.video_file:  # loop demo video
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.05)
                continue
            self.latest = frame
            self.frame_ready.emit(frame)
            self.msleep(33)
        cap.release()

    def stop(self):
        self._run = False
        self.wait(1000)

    @staticmethod
    def brightness_variance(frame) -> tuple:
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(g.mean()), float(g.var())
