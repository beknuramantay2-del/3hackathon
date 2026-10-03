"""SQLite + JPEG screenshots."""
import os, sqlite3, time, cv2
from .hash_chain import EventHashChain

SCHEMA = """CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, severity INT,
 t_start REAL, duration REAL, screenshot TEXT, details TEXT, event_hash TEXT)"""
INDEX = "CREATE INDEX IF NOT EXISTS idx_events_type_start ON events(type, t_start)"

class Store:
    def __init__(self, db="data/session.db", shots="data/shots", salt=""):
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        os.makedirs(shots, exist_ok=True)
        self.db, self.shots = db, shots
        self.con = sqlite3.connect(db)
        self.con.execute(SCHEMA)
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(events)").fetchall()]
        if "event_hash" not in cols:
            self.con.execute("ALTER TABLE events ADD COLUMN event_hash TEXT")
        self.con.execute(INDEX)
        self.con.commit()
        self.t0 = time.time()
        self.chain = EventHashChain(salt) if salt else None
        self._resumed = False

    def save_frame(self, frame, vtype):
        import time as t
        fn = f"{self.shots}/{int(t.time())}_{vtype}.jpg"
        try:
            cv2.imwrite(fn, frame)
        except Exception:
            fn = ""
        return fn

    def add(self, v):
        details = str(v.details)
        event_hash = None
        if self.chain is not None:
            if not self._resumed:
                self._resumed = True
                row = self.con.execute(
                    "SELECT event_hash FROM events WHERE event_hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    self.chain.head = row[0]
            event_hash = self.chain.add_event(v.type, v.severity, v.t_start, v.duration, details)
        self.con.execute(
            "INSERT INTO events(type,severity,t_start,duration,screenshot,details,event_hash) VALUES(?,?,?,?,?,?,?)",
            (v.type, v.severity, v.t_start, v.duration, v.screenshot, details, event_hash))
        self.con.commit()

    def all(self):
        return self.con.execute("SELECT type,severity,t_start,duration,screenshot,details FROM events ORDER BY t_start").fetchall()

    def all_with_hash(self):
        return self.con.execute("SELECT type,severity,t_start,duration,details,event_hash FROM events ORDER BY t_start").fetchall()

    def counts(self):
        rows = self.con.execute("SELECT type,COUNT(*) FROM events GROUP BY type").fetchall()
        return dict(rows)
