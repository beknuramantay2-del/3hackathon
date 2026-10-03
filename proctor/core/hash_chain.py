"""Целостность лога: каждое событие хеширует предыдущее. Подмена задним числом разрывает цепочку."""
import hashlib


def digest(prev, etype, severity, t_start, duration, details, salt):
    raw = "|".join([prev, etype, str(severity), repr(float(t_start)),
                    repr(float(duration)), details, salt])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EventHashChain:
    def __init__(self, salt):
        self.salt = salt
        self.head = salt

    def add_event(self, etype, severity, t_start, duration, details):
        h = digest(self.head, etype, severity, t_start, duration, details, self.salt)
        self.head = h
        return h

    def verify(self, rows):
        """rows: (type, severity, t_start, duration, details, event_hash) по порядку времени."""
        head = self.salt
        for etype, severity, t_start, duration, details, event_hash in rows:
            head = digest(head, etype, severity, t_start, duration, details, self.salt)
            if head != event_hash:
                return False
        return True
