"""Индекс доверия 0-100. Единственная итоговая метрика сессии."""

REPEAT_WINDOW = 5.0
REPEAT_FACTOR = 0.3
TYPE_CAP = 50.0


class TrustCalculator:
    def __init__(self, weights):
        self.weights = dict(weights)
        self.score = 100.0
        self.by_type = {}
        self._last = {}

    def apply_violation(self, violation):
        w = float(self.weights.get(violation.type, 0.0))
        if w <= 0:
            return
        now = violation.t_start
        last = self._last.get(violation.type)
        if last is not None and now - last < REPEAT_WINDOW:
            w = w * REPEAT_FACTOR
        self._last[violation.type] = now
        entry = self.by_type.setdefault(
            violation.type, {"count": 0, "total_loss": 0.0, "weight": float(self.weights.get(violation.type, 0.0))})
        room = TYPE_CAP - entry["total_loss"]
        if room <= 0:
            return
        loss = min(violation.severity * w, room)
        entry["count"] += 1
        entry["total_loss"] += loss
        self.score = max(0.0, self.score - loss)

    def get_score(self, labels=None):
        labels = labels or {}
        parts = []
        for t, e in self.by_type.items():
            if e["total_loss"] > 0:
                parts.append(f"-{e['total_loss']:.0f} {labels.get(t, t)}")
        return {
            "trust_score": round(self.score),
            "by_type": {k: {"count": v["count"], "total_loss": round(v["total_loss"], 1),
                            "weight": v["weight"]} for k, v in self.by_type.items()},
            "breakdown_text": ", ".join(parts),
        }
