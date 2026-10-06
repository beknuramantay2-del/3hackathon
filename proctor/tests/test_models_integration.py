"""Opt-in native model smoke, no network downloads. Not a 19-scenario validation."""
import os
import time
import pytest
import cv2
import numpy as np
from proctor.core.pipeline import FramePacket
from proctor.core.face_mesh import FaceMeshThread
from proctor.core.detector_yolo import DetectorYolo

pytestmark = pytest.mark.skipif(os.getenv("PROCTOR_MODEL_SMOKE") != "1",reason="opt-in: local weights and supported MediaPipe required")

def test_native_models_empty_scene():
    yolo = DetectorYolo("proctor/weights/yolov8n.pt",imgsz=320,offline=True)
    face = FaceMeshThread()
    try:
        yolo.setup(); face.setup()
        packet = FramePacket(1,time.monotonic(),np.full((480,640,3),110,dtype=np.uint8))
        y = yolo.process(packet)
        f = face.process(packet)
        assert y.seq == f.seq == 1
        assert not y.phone_voted and not y.phones
        assert f.n_faces == 0 and not f.gaze_valid and not f.pose_valid
    finally:
        face.teardown()


def test_native_face_fixture():
    path = os.getenv("PROCTOR_FACE_FIXTURE")
    if not path:
        pytest.skip("provide a local public face fixture")
    image = cv2.imread(path)
    assert image is not None
    face = FaceMeshThread()
    try:
        face.setup()
        f = face.process(FramePacket(1,time.monotonic(),image))
        assert f.n_faces >= 1 and f.pose_valid and f.gaze_valid
        assert f.left_eye is not None and f.right_eye is not None
    finally:
        face.teardown()


def test_native_hands_gated_roi_empty_scene():
    from proctor.core.hands import HandsThread
    from proctor.core.models import YoloResult,Box
    now = time.monotonic()
    result = YoloResult(phones=[Box(.9,260,260,300,320,confirmed=True)],captured_at=now)
    hands = HandsThread(lambda:result)
    try:
        hands.setup()
        r = hands.process(FramePacket(1,now,np.full((480,640,3),110,dtype=np.uint8)))
        assert r.seq == 1 and r.boxes == []
    finally:
        hands.teardown()
