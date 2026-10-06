"""Workers consume fresh snapshots once; GUI polls results, no queued frame signals."""
import time
from PyQt6.QtCore import QThread
from .pipeline import LatestSlot, FramePacket, AdaptiveBudget

class LatestWorker(QThread):
    def __init__(self, fps=15, parent=None, adaptive=True):
        super().__init__(parent)
        self.input = LatestSlot()
        self.output = LatestSlot()
        self.budget = AdaptiveBudget(fps, enabled=adaptive)
        self.status = "loading"
        self.error = ""
        self._compat_seq = 0
        self.processed_count = 0
        self.actual_fps = 0.0
        self.skipped_frames = 0
        self.latency_ms = None

    def push(self, packet):
        if not isinstance(packet, FramePacket):
            self._compat_seq += 1
            packet = FramePacket(self._compat_seq,time.monotonic(),packet)
        self.input.put(packet)

    def setup(self):
        pass

    def teardown(self):
        pass

    def process(self, packet):
        raise NotImplementedError

    def run(self):
        version, deadline, last_seq = 0, 0., -1
        window, count = time.monotonic(), 0
        try:
            self.setup()
            self.status = "ready"
            window,count = time.monotonic(),0
            while not self.isInterruptionRequested():
                now = time.monotonic()
                if now < deadline:
                    self.msleep(min(10,max(1,int((deadline-now)*1000))))
                    continue
                new_version, packet = self.input.take_after(version)
                if packet is None:
                    self.msleep(5)
                    continue
                version = new_version
                if packet.seq <= last_seq:
                    continue
                if last_seq >= 0:
                    self.skipped_frames += max(0,packet.seq-last_seq-1)
                last_seq = packet.seq
                if now-packet.captured_at > .7:  # never infer an old frame
                    continue
                start = time.perf_counter()
                result = self.process(packet)
                elapsed = time.perf_counter()-start
                self.processed_count += 1
                count += 1
                self.latency_ms = (time.monotonic()-packet.captured_at)*1000
                if time.monotonic()-window >= 1:
                    self.actual_fps = count/(time.monotonic()-window)
                    window,count = time.monotonic(),0
                self.budget.observe(elapsed)
                self.output.put(result)
                deadline = now+1/self.budget.target_fps
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"
        finally:
            self.teardown()

    def stop(self):
        self.requestInterruption()
        # Do not destroy a running inference thread or force-terminate native libraries.
        return self.wait(5000)
