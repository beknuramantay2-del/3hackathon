import os
import time
from .worker import LatestWorker
from .models import YoloResult, Box
from .tracking import LiteTracker, iou, coords
from .phone_evidence import PartialPhoneEvidence


class DetectorYolo(LatestWorker):
    def __init__(
        self,
        model_name="yolov8n.pt",
        imgsz=640,
        conf_phone=0.25,
        conf_person=0.5,
        vote_window=8,
        vote_threshold=4,
        parent=None,
        target_fps=8,
        device="auto",
        threads=2,
        offline=True,
        adaptive=True,
        phone_class=67,
        person_class=0,
        detail_search=True,
        desk_search=True,
    ):
        super().__init__(target_fps, parent, adaptive)
        self.model_name, self.imgsz = model_name, imgsz
        self.conf_phone, self.conf_person = conf_phone, conf_person
        self.PHONE, self.PERSON = phone_class, person_class
        self.device, self.threads, self.offline = device, threads, offline
        self.budget.imgsz = imgsz
        self.budget.min_imgsz = min(imgsz, 416)
        self.tracker = LiteTracker(strong=conf_phone, min_hits=2, ttl=0.65)
        self.model = None
        self.requested_confidence = conf_phone
        self.detail_search = detail_search
        self.hand_provider = None
        self.partial_evidence = PartialPhoneEvidence()
        self.calls = 0
        self.tile = 0
        self.search_step = 0
        self.desk_search = desk_search
        self.detail_region = ""
        self.detail_rect = None
        self.detail_calls = 0

    def setup(self):
        if self.offline and not os.path.isfile(self.model_name):
            raise FileNotFoundError(
                f"Нет весов {self.model_name}: python -m proctor.tools.fetch_weights"
            )
        import torch

        torch.set_num_threads(self.threads)
        if self.device == "auto":
            self.device = "0" if torch.cuda.is_available() else "cpu"
        from ultralytics import YOLO

        self.model = YOLO(self.model_name)
        import numpy as np

        self.model.predict(
            np.zeros((320, 320, 3), dtype=np.uint8),
            imgsz=self.budget.imgsz,
            classes=[self.PHONE, self.PERSON],
            device=self.device,
            verbose=False,
        )

    def _infer(self, image, size, classes, offset=(0, 0)):
        result = self.model.predict(
            image,
            imgsz=size,
            conf=0.10,
            classes=classes,
            device=self.device,
            max_det=20,
            verbose=False,
        )[0]
        phones, persons = [], []
        if result.boxes is None:
            return phones, persons
        dx, dy = offset
        for row in result.boxes.data.cpu().numpy():
            x1, y1, x2, y2, confidence, cls = row[:6]
            box = Box(
                float(confidence),
                int(x1 + dx),
                int(y1 + dy),
                int(x2 + dx),
                int(y2 + dy),
            )
            if int(cls) == self.PHONE:
                box.source = "detail" if classes == [self.PHONE] else "global"
                phones.append(box)
            elif int(cls) == self.PERSON and confidence >= self.conf_person:
                persons.append(box)
        return phones, persons

    def _detail_roi(self, frame, candidates, captured_at=None):
        h, w = frame.shape[:2]
        previous = self.output.peek()
        observed = [b for b in candidates if b.conf >= 0.12]
        if (
            not observed
            and previous is not None
            and captured_at is not None
            and 0 <= captured_at - previous.captured_at <= 0.65
        ):
            observed = [b for b in previous.candidates if b.conf >= 0.12]
        if observed and self.calls % 8 != 0:
            b = max(observed, key=lambda p: p.conf)
            cx, cy = (b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2
            tiny = b.conf < 0.15 and min(b.x2 - b.x1, b.y2 - b.y1) < 24
            rw, rh = (
                (
                    max(120, w * 0.24, (b.x2 - b.x1) * 4),
                    max(100, h * 0.30, (b.y2 - b.y1) * 4),
                )
                if tiny
                else (
                    max(w * 0.35, (b.x2 - b.x1) * 3),
                    max(h * 0.35, (b.y2 - b.y1) * 3),
                )
            )
            x1, y1 = max(0, int(cx - rw / 2)), max(0, int(cy - rh / 2))
            x2, y2 = min(w, int(cx + rw / 2)), min(h, int(cy + rh / 2))
            self.detail_region = "candidate zoom" if tiny else "candidate context"
        elif self.desk_search and self.search_step % 4 != 3:
            phase = self.search_step % 4
            left = (0.31, 0.0, 0.62)[phase]
            x1, y1 = int(w * left), int(h * 0.55)
            x2, y2 = min(w, x1 + max(1, int(w * 0.38))), h
            self.detail_region = ("desk center", "desk left", "desk right")[phase]
            self.search_step += 1
        else:
            left, top = self.tile % 2, self.tile // 2
            self.tile = (self.tile + 1) % 4
            x1, y1 = int(w * 0.4 * left), int(h * 0.4 * top)
            x2, y2 = min(w, x1 + max(1, int(w * 0.6))), min(
                h, y1 + max(1, int(h * 0.6))
            )
            self.detail_region = "full-frame quadrant"
            self.search_step += 1
        self.detail_rect = (x1, y1, x2, y2)
        return frame[y1:y2, x1:x2], (x1, y1)

    def process(self, packet):
        if self.requested_confidence > self.conf_phone:
            for track in self.tracker.tracks.values():
                track.hits = 0
                track.last_strong = None
            self.partial_evidence.history = []
        self.conf_phone = self.requested_confidence
        self.tracker.strong = self.conf_phone
        self.calls += 1
        start = time.perf_counter()
        phones, persons = self._infer(
            packet.frame, self.budget.imgsz, [self.PHONE, self.PERSON]
        )
        detail_time = 0.0
        self.detail_region = ""
        self.detail_rect = None
        detail_size = 0
        needs_detail = (
            not phones
            or max(p.conf for p in phones) < 0.65
            or any(min(p.x2 - p.x1, p.y2 - p.y1) < 64 for p in phones)
        )
        if self.detail_search and needs_detail and self.calls % 2 == 0:
            roi, origin = self._detail_roi(packet.frame, phones, packet.captured_at)
            if roi.size:
                s = time.perf_counter()
                detail_size = min(640, self.budget.imgsz + 128)
                detail, _ = self._infer(roi, detail_size, [self.PHONE], origin)
                self.detail_calls += 1
                detail_time = time.perf_counter() - s
                phones.extend(detail)
        inferred = time.perf_counter()

        for box in phones:
            box.detail_agreement = any(
                other.source != box.source and iou(coords(box), coords(other)) > 0.25
                for other in phones
            )
        unique = []
        for box in sorted(phones, key=lambda b: b.conf, reverse=True):
            if not any(
                iou(coords(box), coords(other))
                > (0.25 if box.source != other.source else 0.45)
                for other in unique
            ):
                unique.append(box)
        tracking_started = time.perf_counter()
        hand_result = self.hand_provider() if self.hand_provider else None
        self.partial_evidence.update(
            unique, hand_result, packet.captured_at, self.conf_phone
        )
        tracks = self.tracker.update(unique, packet.captured_at)
        for b in tracks:
            anchored = (
                b.strong_at is not None and 0 <= packet.captured_at - b.strong_at <= 0.8
            )

            b.confirmed = b.confirmed and (
                b.conf >= self.conf_phone or anchored or b.supported
            )
        voted = any(b.confirmed and b.observed for b in tracks)
        return YoloResult(
            phones=tracks,
            candidates=unique,
            n_persons=len(persons),
            persons=persons,
            phone_voted=voted,
            inference_size=self.budget.imgsz,
            detail_region=self.detail_region,
            detail_rect=self.detail_rect,
            detail_inference_size=detail_size,
            seq=packet.seq,
            captured_at=packet.captured_at,
            processed_at=time.monotonic(),
            timings={
                "yolo": inferred - start,
                "detail_search": detail_time,
                "postprocess": tracking_started - inferred,
                "tracker": time.perf_counter() - tracking_started,
            },
        )

    def phone_voted(self):
        result = self.output.peek()
        return bool(result and result.phone_voted)
