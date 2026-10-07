from dataclasses import dataclass, asdict
import uuid


@dataclass
class Episode:
    ident: str
    kind: str
    started: float
    last_seen: float
    duration: float = 0.0
    level: int = 0
    closed: bool = False
    measurement_age_ms: float | None = None


class EpisodePolicy:
    def __init__(self, yellow=3.0, red=5.0, gap=0.3, phone_conf=0.55):
        if not 0 < yellow < red:
            raise ValueError("0 < yellow < red required")
        self.yellow, self.red, self.gap, self.phone_conf = yellow, red, gap, phone_conf
        self.active = {}
        self.written = {}

    def update(self, conditions, now, age=None):
        changes, notices = [], []
        for kind in set(conditions) | set(self.active):
            ep = self.active.get(kind)
            if conditions.get(kind):

                if ep is not None and now - ep.last_seen > self.gap + 0.1:
                    ep.closed = True
                    changes.append(asdict(ep))
                    self.active.pop(kind)
                    self.written.pop(ep.ident, None)
                    ep = None
                new = ep is None
                if new:
                    ep = Episode(uuid.uuid4().hex, kind, now, now)
                    self.active[kind] = ep
                ep.last_seen = now
                ep.duration = max(0.0, now - ep.started)
                ep.measurement_age_ms = age.get(kind) if isinstance(age, dict) else age
                old = ep.level
                if kind == "PHONE_DETECTED":
                    ep.level = 2
                elif kind.startswith("PHONE_"):
                    ep.level = 0
                else:
                    ep.level = (
                        2
                        if ep.duration >= self.red
                        else 1 if ep.duration >= self.yellow else 0
                    )
                if ep.level > old:
                    notices.append(asdict(ep))
                if new or ep.level > old or now - self.written.get(ep.ident, -1e9) >= 1:
                    changes.append(asdict(ep))
                    self.written[ep.ident] = now
            elif ep is not None and now - ep.last_seen > self.gap:
                ep.closed = True
                ep.duration = max(0.0, ep.last_seen - ep.started)
                changes.append(asdict(ep))
                self.active.pop(kind)
                self.written.pop(ep.ident, None)
        return changes, notices

    @staticmethod
    def pulse(kind, now, level=2):
        return asdict(
            Episode(uuid.uuid4().hex, kind, now, now, level=level, closed=True)
        )

    @staticmethod
    def ages(engine, now):
        return {
            kind: (
                max(
                    0.0,
                    now
                    - (
                        engine.yolo if kind.startswith("PHONE_") else engine.face
                    ).captured_at,
                )
                * 1000
                if (engine.yolo if kind.startswith("PHONE_") else engine.face).seq >= 0
                else None
            )
            for kind in engine.debug.get("conds", {})
        }

    def close(self):
        rows = []
        for ep in self.active.values():
            ep.closed = True
            ep.duration = max(0.0, ep.last_seen - ep.started)
            rows.append(asdict(ep))
        self.active.clear()
        self.written.clear()
        return rows

    def observations(self, engine, now, ready):
        d = engine.debug
        conds = dict(d.get("conds", {}))
        for kind in conds:
            if kind.startswith(("HEAD_", "GAZE_")) and not ready:
                conds[kind] = False
        y = engine.yolo

        conds["PHONE_DETECTED"] = bool(
            y.seq >= 0
            and engine.fresh(y, now)
            and (
                y.phone_voted
                or any(
                    p.observed
                    and p.conf
                    >= max(
                        self.phone_conf,
                        engine.cfg.get("yolo", {}).get("conf_phone", 0.0),
                    )
                    for p in y.candidates
                )
            )
        )
        return conds

    @property
    def level(self):
        return max((e.level for e in self.active.values()), default=0)
