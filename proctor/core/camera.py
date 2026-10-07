import time
import cv2
from PyQt6.QtCore import QThread
from .pipeline import FramePacket, LatestSlot


class CameraThread(QThread):
    def __init__(
        self, width=640, height=480, video_file=None, index=0, parent=None, fps=30
    ):
        super().__init__(parent)
        self.width, self.height = width, height
        self.video_file, self.index, self.fps = video_file, index, fps
        self.output = LatestSlot()
        self.sinks = []
        self.status = "loading"
        self.error = ""
        self.seq = 0
        self.camera_fps = 0.0
        self.read_ms = 0.0
        self.reconnects = 0

    @property
    def latest(self):
        p = self.output.peek()
        return p.frame if p else None

    def subscribe(self, sink):
        self.sinks.append(sink)

    def run(self):
        cap = None
        try:
            while not self.isInterruptionRequested():
                self.status = "loading" if self.seq == 0 else "reconnecting"
                src = self.video_file if self.video_file else self.index

                cap = cv2.VideoCapture(src)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                source_fps = cap.get(cv2.CAP_PROP_FPS) or self.fps
                if not cap.isOpened():
                    self.status, self.error = "error", "Камера/видео не открывается"
                    cap.release()
                    if self.video_file:
                        return
                    self.msleep(500)
                    continue
                failures, last, window, count = 0, None, time.monotonic(), 0
                while not self.isInterruptionRequested():
                    start = time.monotonic()
                    ok, frame = cap.read()
                    self.read_ms = (time.monotonic() - start) * 1000
                    if not ok:
                        failures += 1
                        self.status, self.error = "reconnecting", "Нет новых кадров"
                        if self.video_file:
                            self.status, self.error = "eof", "Видео завершено"
                            return
                        if failures >= 10:
                            break
                        self.msleep(50)
                        continue
                    failures = 0
                    captured = time.monotonic()
                    if (
                        not self.video_file
                        and last is not None
                        and captured - last < 1 / self.fps
                    ):

                        self.msleep(1)
                        continue
                    self.seq += 1
                    source_time = (
                        (self.seq - 1) / source_fps if self.video_file else None
                    )
                    packet = FramePacket(self.seq, captured, frame, source_time)
                    self.output.put(packet)
                    for sink in self.sinks:
                        sink(packet)
                    self.status, self.error = "ready", ""
                    last = captured
                    count += 1
                    if captured - window >= 1:
                        self.camera_fps = count / (captured - window)
                        window, count = captured, 0
                    if self.video_file:
                        time.sleep(
                            max(0.0, 1 / source_fps - (time.monotonic() - start))
                        )
                cap.release()
                if self.isInterruptionRequested():
                    break
                self.reconnects += 1
                self.msleep(300)
        except Exception as e:
            self.status, self.error = "error", str(e)
        finally:
            if cap is not None:
                cap.release()

    def stop(self):
        self.requestInterruption()
        return self.wait(5000)

    @staticmethod
    def brightness_variance(frame):
        g = cv2.cvtColor(cv2.resize(frame, (80, 60)), cv2.COLOR_BGR2GRAY)
        return float(g.mean()), float(g.var())
