"""Gated Hands worker; ROI near current phone, never run when no phone is observed."""
import cv2
from .worker import LatestWorker
from .models import HandResult

class HandsThread(LatestWorker):
    def __init__(self, phone_provider, target_fps=5, parent=None):
        super().__init__(target_fps,parent)
        self.phone_provider = phone_provider
        self.hands = None

    def setup(self):
        import mediapipe as mp
        self.hands = mp.solutions.hands.Hands(max_num_hands=2,model_complexity=0,
                                            min_detection_confidence=.6,min_tracking_confidence=.6)

    def teardown(self):
        if self.hands:
            self.hands.close()

    def process(self, packet):
        result = HandResult(seq=packet.seq,captured_at=packet.captured_at)
        phones = self.phone_provider()
        if phones is None or packet.captured_at-phones.captured_at > .5:
            return result
        observed = [b for b in phones.phones if b.observed and b.confirmed]
        if not observed:
            return result
        h,w = packet.frame.shape[:2]
        # One padded ROI encloses phone(s) and nearby hand(s).
        pad = max(70,int(w*.18))
        x1 = max(0,min(b.x1 for b in observed)-pad)
        y1 = max(0,min(b.y1 for b in observed)-pad)
        x2 = min(w,max(b.x2 for b in observed)+pad)
        y2 = min(h,max(b.y2 for b in observed)+pad)
        roi = packet.frame[y1:y2,x1:x2]
        if roi.size == 0:
            return result
        rh,rw = roi.shape[:2]
        if rw > 320:
            roi = cv2.resize(roi,(320,max(1,int(rh*320/rw))))
        rgb = cv2.cvtColor(roi,cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        res = self.hands.process(rgb)
        for hand in res.multi_hand_landmarks or []:
            xs = [p.x*rw+x1 for p in hand.landmark]
            ys = [p.y*rh+y1 for p in hand.landmark]
            result.boxes.append((max(0,int(min(xs))),max(0,int(min(ys))),
                                 min(w,int(max(xs))),min(h,int(max(ys)))))
        return result
