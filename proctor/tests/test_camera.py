import cv2
import numpy as np
from proctor.core.camera import CameraThread


def test_empty_video_fails_without_busy_loop(tmp_path):
    camera = CameraThread(video_file=str(tmp_path/"does-not-exist.mp4"))
    camera.start()
    assert camera.wait(3000)
    assert camera.status == "error" and camera.output.peek() is None


def test_video_paced_unique_frames_and_eof(tmp_path):
    path = str(tmp_path/"tiny.avi")
    out = cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*"MJPG"),20,(64,48))
    for i in range(5):
        out.write(np.full((48,64,3),i*20,dtype=np.uint8))
    out.release()
    observed = []
    camera = CameraThread(video_file=path)
    camera.subscribe(observed.append)
    camera.start()
    assert camera.wait(3000)
    assert camera.status == "eof"
    assert [p.seq for p in observed] == [1,2,3,4,5]
    assert observed[-1].captured_at-observed[0].captured_at >= .17
    assert observed[-1].source_time == .2


def test_failed_reads_release_and_reconnect(monkeypatch):
    import time
    opened = []
    class FakeCapture:
        def __init__(self,src):
            self.number = len(opened)
            self.released = False
            opened.append(self)
        def set(self,*args):
            return True
        def get(self,*args):
            return 30.
        def isOpened(self):
            return True
        def read(self):
            if self.number == 0:
                return False,None
            time.sleep(.035)
            return True,np.zeros((48,64,3),dtype=np.uint8)
        def release(self):
            self.released = True
    monkeypatch.setattr('proctor.core.camera.cv2.VideoCapture',FakeCapture)
    camera = CameraThread()
    observed = []
    def sink(p):
        observed.append(p)
        if len(observed) == 3:
            camera.requestInterruption()
    camera.subscribe(sink)
    camera.start()
    assert camera.wait(3000)
    assert camera.reconnects == 1 and len(observed) == 3
    assert all(c.released for c in opened)
