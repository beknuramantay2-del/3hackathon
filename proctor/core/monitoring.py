import base64
import json
import os
import threading
import time
from pathlib import Path
import cv2

MAX_SNAPSHOT_BYTES = 300_000
ACTIVE_STATUSES = ("running",)


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


def read_snapshot(directory, session_state, max_age=3.0):
    result = dict(live=False, reason="Новых кадров пока нет", status={}, image=None)
    if session_state not in ACTIVE_STATUSES:
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


def write_session(directory, name, state):
    target = Path(directory) / "session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(name=name, state=state, updated_at=time.time())
    temporary = target.with_suffix(".tmp")
    with temporary.open("w", encoding="utf8") as out:
        os.chmod(temporary, 0o600)
        json.dump(payload, out, ensure_ascii=False)
    os.replace(temporary, target)


def session_list(root):
    results = []
    for directory in sorted(Path(root).glob("*"), reverse=True):
        try:
            metadata = directory / "session.json"
            if (
                directory.is_symlink()
                or metadata.is_symlink()
                or metadata.stat().st_size > 4096
            ):
                continue
            data = json.loads(metadata.read_text(encoding="utf8"))
            if data["state"] not in ("running", "finished", "failed"):
                continue
            results.append(
                dict(
                    directory=directory,
                    name=str(data["name"])[:120],
                    state=data["state"],
                )
            )
            if len(results) >= 100:
                break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return results


def read_events(directory, limit=100):
    import sqlite3

    database = Path(directory) / "session.db"
    if not database.is_file() or database.is_symlink():
        return []
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2
    )
    try:
        return connection.execute(
            "SELECT type,severity,t_start,duration FROM events ORDER BY id DESC LIMIT ?",
            (max(1, min(100, limit)),),
        ).fetchall()
    finally:
        connection.close()
