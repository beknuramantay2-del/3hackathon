import base64
import json
import os
import threading
import time
from pathlib import Path
import cv2

MAX_SNAPSHOT_BYTES = 300_000
ACTIVE_STATUSES = ("preparing", "calibrating", "running")


class MonitorPublisher:
    def __init__(self, directory, fps=2):
        self.path = Path(directory) / "monitor.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.interval = 1 / max(1, min(2, fps))
        self.condition = threading.Condition()
        self.pending = None
        self.closed = False
        self.last_offer = -float("inf")
        self.error = ""
        self.published = 0
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="monitor-preview"
        )
        self.thread.start()

    def offer(self, frame, seq, captured_at, status):
        now = time.monotonic()
        with self.condition:
            if self.closed or now - self.last_offer < self.interval:
                return False
            self.last_offer = now
            self.pending = (frame, seq, captured_at, dict(status))
            self.condition.notify()
        return True

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                frame, seq, captured_at, status = self.pending
                self.pending = None
            try:
                h, w = frame.shape[:2]
                scale = min(1.0, 480 / w, 360 / h)
                preview = cv2.resize(
                    frame, (max(1, int(w * scale)), max(1, int(h * scale)))
                )
                ok, encoded = cv2.imencode(
                    ".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 65]
                )
                if not ok:
                    raise OSError("Не удалось подготовить кадр наблюдения")
                data = dict(
                    version=1,
                    seq=seq,
                    captured_at=time.time() - max(0.0, time.monotonic() - captured_at),
                    published_at=time.time(),
                    status=status,
                    image=base64.b64encode(encoded).decode("ascii"),
                )
                payload = json.dumps(data, ensure_ascii=False).encode("utf8")
                if len(payload) > MAX_SNAPSHOT_BYTES:
                    raise OSError("Кадр наблюдения превышает лимит")
                temporary = self.path.with_suffix(".tmp")
                with open(temporary, "wb") as out:
                    os.chmod(temporary, 0o600)
                    out.write(payload)
                os.replace(temporary, self.path)
                self.published += 1
                self.error = ""
            except (OSError, ValueError, cv2.error) as exc:
                self.error = str(exc)

    def stop(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify()
        self.thread.join(timeout=3)
        return not self.thread.is_alive()


def read_snapshot(directory, attempt_status, max_age=3.0):
    result = dict(live=False, reason="Новых кадров пока нет", status={}, image=None)
    if attempt_status not in ACTIVE_STATUSES:
        result["reason"] = "Сессия завершена; откройте сохранённый футаж"
        return result
    try:
        path = Path(directory) / "monitor.json"
        if path.is_symlink() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("Некорректный кадр наблюдения")
        with path.open("rb") as stream:
            payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Кадр наблюдения превышает лимит")
        data = json.loads(payload)
        age = time.time() - float(data["captured_at"])
        if data["version"] != 1 or not 0 <= age <= max_age:
            result["reason"] = (
                "Нет свежего кадра; проверьте приложение ученика и камеру"
            )
            return result
        if not isinstance(data["status"], dict):
            raise ValueError("Неверный статус наблюдения")
        image = base64.b64decode(data["image"], validate=True)
        if not image.startswith(b"\xff\xd8"):
            raise ValueError("Неверный формат кадра")
        return dict(
            live=True,
            reason="Связь активна",
            status=data["status"],
            image=image,
            age=age,
            seq=data["seq"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return result
