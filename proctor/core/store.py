import os
import sqlite3
import time
import json
import threading
import queue
import uuid
import re
import cv2
from .hash_chain import EventHashChain, digest

SCHEMA = """CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, severity INT,
 t_start REAL, duration REAL, screenshot TEXT, details TEXT, event_hash TEXT)"""


class Store:
    def __init__(self, db="data/session.db", shots="data/shots", salt=""):
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        os.makedirs(shots, exist_ok=True)
        self.db, self.shots = db, shots
        self.lock = threading.RLock()
        self.con = sqlite3.connect(db, check_same_thread=False, timeout=5.0)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        self.con.execute(SCHEMA)
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(events)")]
        if "event_hash" not in cols:
            self.con.execute("ALTER TABLE events ADD COLUMN event_hash TEXT")
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_start ON events(type,t_start)"
        )
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS episodes(
              ident TEXT PRIMARY KEY, kind TEXT, started REAL, duration REAL,
              level INTEGER, closed INTEGER, details TEXT);
            CREATE TABLE IF NOT EXISTS clips(path TEXT PRIMARY KEY, metadata TEXT);
            CREATE TABLE IF NOT EXISTS episode_clips(ident TEXT,path TEXT,PRIMARY KEY(ident,path));
        """)
        self.con.commit()
        self.t0 = time.time()
        self.chain = EventHashChain(salt) if salt else None

    def save_frame(self, frame, vtype):
        if frame is None:
            return ""
        safe_type = re.sub(r"[^A-Za-z0-9_-]", "", vtype)[:60] or "event"
        fn = os.path.join(
            self.shots, f"{time.time_ns()}_{safe_type}_{uuid.uuid4().hex[:6]}.jpg"
        )
        return fn if cv2.imwrite(fn, frame, [cv2.IMWRITE_JPEG_QUALITY, 80]) else ""

    def add(self, v):
        details = json.dumps(v.details, ensure_ascii=False, sort_keys=True, default=str)
        with self.lock:
            try:
                self.con.execute("BEGIN IMMEDIATE")
                event_hash = None
                if self.chain:
                    row = self.con.execute(
                        "SELECT event_hash FROM events ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    previous = row[0] if row and row[0] else self.chain.salt
                    event_hash = digest(
                        previous,
                        v.type,
                        v.severity,
                        v.t_start,
                        v.duration,
                        details,
                        self.chain.salt,
                    )
                self.con.execute(
                    "INSERT INTO events(type,severity,t_start,duration,screenshot,details,event_hash) VALUES(?,?,?,?,?,?,?)",
                    (
                        v.type,
                        v.severity,
                        v.t_start,
                        v.duration,
                        v.screenshot,
                        details,
                        event_hash,
                    ),
                )
                self.con.commit()
                if self.chain:
                    self.chain.head = event_hash
            except Exception:
                self.con.rollback()
                raise

    def _rows(self, sql):
        with self.lock:
            return self.con.execute(sql).fetchall()

    def all(self):
        return self._rows(
            "SELECT type,severity,t_start,duration,screenshot,details FROM events ORDER BY id"
        )

    def all_with_hash(self):
        return self._rows(
            "SELECT type,severity,t_start,duration,details,event_hash FROM events ORDER BY id"
        )

    def counts(self):
        return dict(self._rows("SELECT type,COUNT(*) FROM events GROUP BY type"))

    def save_episode(self, episode, wall_offset, source):
        e = dict(episode)
        details = json.dumps(
            dict(e, source=source, wall_offset=wall_offset), ensure_ascii=False
        )
        with self.lock:
            try:
                self.con.execute(
                    "INSERT INTO episodes VALUES(?,?,?,?,?,?,?) ON CONFLICT(ident) DO UPDATE SET duration=excluded.duration,level=excluded.level,closed=excluded.closed,details=excluded.details",
                    (
                        e["ident"],
                        e["kind"],
                        e["started"] + wall_offset,
                        e["duration"],
                        e["level"],
                        int(e["closed"]),
                        details,
                    ),
                )
                self.con.commit()
            except Exception:
                self.con.rollback()
                raise

    def save_clip(self, path, metadata, episodes):
        with self.lock:
            try:
                self.con.execute(
                    "INSERT OR REPLACE INTO clips VALUES(?,?)",
                    (str(path), json.dumps(metadata, ensure_ascii=False)),
                )
                self.con.executemany(
                    "INSERT OR IGNORE INTO episode_clips VALUES(?,?)",
                    [(ident, str(path)) for ident in episodes],
                )
                self.con.commit()
            except Exception:
                self.con.rollback()
                raise

    def clips_for(self, ident):
        with self.lock:
            return [
                (p, json.loads(m))
                for p, m in self.con.execute(
                    "SELECT clips.path,clips.metadata FROM clips JOIN episode_clips USING(path) WHERE ident=? ORDER BY clips.path",
                    (ident,),
                )
            ]

    def episode_rows(self):
        return self._rows(
            "SELECT ident,kind,started,duration,level,closed,details FROM episodes ORDER BY started"
        )

    def close(self):
        with self.lock:
            self.con.close()


class EventWriter:
    def __init__(self, store, size=64):
        self.store, self.queue = store, queue.Queue(maxsize=size)
        self.error = ""
        self.failures = 0
        self.thread = threading.Thread(
            target=self._run, name="event-writer", daemon=True
        )
        self.thread.start()

    def submit(self, violation, frame=None):
        try:

            self.queue.put_nowait((violation, frame))
            return True
        except queue.Full:
            self.error = "Очередь записи заполнена: событие не сохранено"
            self.failures += 1
            return False

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                v, frame = item
                if frame is not None:
                    try:
                        v.screenshot = self.store.save_frame(frame, v.type)
                        if not v.screenshot:
                            raise OSError("JPEG encoder returned false")
                    except Exception as e:
                        self.error = "Screenshot: " + str(e)
                        self.failures += 1
                        v.details["screenshot_error"] = str(e)
                        v.screenshot = ""

                self.store.add(v)
            except Exception as e:
                self.error = str(e)
                self.failures += 1
            finally:
                self.queue.task_done()

    def flush(self):
        self.queue.join()

    def stop(self):
        self.flush()
        self.queue.put(None)
        self.thread.join(timeout=5.0)
