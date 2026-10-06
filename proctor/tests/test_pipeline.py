import time
import threading
import pytest
from proctor.core.pipeline import LatestSlot,FramePacket,AdaptiveBudget,Timings
from proctor.core.worker import LatestWorker


def test_latest_slot_bounded():
    slot = LatestSlot()
    for i in range(10000):
        slot.put(i)
    version,value = slot.take_after(0)
    assert version == 10000 and value == 9999
    assert slot.take_after(version) == (version,None)
    assert slot.peek() == 9999


def test_slot_parallel_producer():
    slot = LatestSlot()
    def producer():
        for i in range(1000):
            slot.put(i)
    thread = threading.Thread(target=producer)
    thread.start(); thread.join()
    assert slot.peek() == 999

class CounterWorker(LatestWorker):
    def __init__(self):
        super().__init__(100,adaptive=False)
        self.count = 0
    def process(self,p):
        self.count += 1
        return p


def test_worker_no_duplicate_or_stale():
    w = CounterWorker()
    w.start()
    try:
        p = FramePacket(1,time.monotonic(),None)
        w.push(p)
        deadline = time.monotonic()+2
        while w.count == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        assert w.count == 1
        for _ in range(20):
            w.push(p)
        time.sleep(.05)
        assert w.count == 1
        w.push(FramePacket(2,time.monotonic()-2,None))
        time.sleep(.05)
        assert w.count == 1
        w.push(FramePacket(3,time.monotonic(),None))
        time.sleep(.05)
        assert w.count == 2
    finally:
        assert w.stop()


def test_worker_failure_explicit():
    class Failing(LatestWorker):
        def setup(self):
            raise RuntimeError("Missing model")
    w = Failing(); w.start(); w.wait(2000)
    assert w.status == "error" and "Missing model" in w.error
    assert w.output.peek() is None


def test_adaptive_bounds_and_fixed_mode():
    b = AdaptiveBudget(10,imgsz=416)
    for _ in range(300):
        b.observe(.35)
    assert 3 <= b.target_fps < 10 and b.imgsz == 320
    for _ in range(300):
        b.observe(.005)
    assert b.target_fps <= 10
    fixed = AdaptiveBudget(8,enabled=False)
    for _ in range(100):
        fixed.observe(.5)
    assert fixed.target_fps == 8


def test_timings_bounded():
    t = Timings(4)
    for x in range(10):
        t.add("test",x/1000)
    assert t.snapshot()["test"]["n"] == 4
    assert t.snapshot()["test"]["p95_ms"] == 9
