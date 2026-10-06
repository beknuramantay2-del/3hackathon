"""Tiny two-stage IoU tracker for a handful of objects; no ReID/torch dependency.
Strong detections start IDs; weak detections only recover existing IDs.
Predictions are overlay-only, never evidence for a warning.
"""
from dataclasses import dataclass
import math
from .models import Box

def iou(a, b):
    x = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    y = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = x*y
    return inter / max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)

def coords(b):
    return (b.x1, b.y1, b.x2, b.y2)

@dataclass
class Track:
    ident: int
    bbox: tuple
    last_seen: float
    conf: float
    hits: int = 1
    last_strong: float | None = None
    velocity: tuple = (0., 0., 0., 0.)

    def predict(self, now):
        dt = min(.2, max(0., now-self.last_seen))
        return tuple(x + v*dt for x, v in zip(self.bbox, self.velocity))

class LiteTracker:
    def __init__(self, strong=.35, min_hits=3, ttl=.5, match_iou=.15):
        self.strong, self.min_hits, self.ttl, self.match_iou = strong, min_hits, ttl, match_iou
        self.tracks = {}
        self.next_id = 1

    def update(self, detections, now):
        self.tracks = {k:t for k,t in self.tracks.items() if now-t.last_seen <= self.ttl}
        unused = set(self.tracks)
        remaining = set(range(len(detections)))
        observed = set()
        # High confidence association first, then recovery using low confidence detections.
        for high in (True, False):
            candidates = []
            for k in unused:
                for i in remaining:
                    d = detections[i]
                    if (d.conf >= self.strong) != high:
                        continue
                    track = self.tracks[k]
                    predicted = track.predict(now)
                    score = iou(predicted, coords(d))
                    if score >= self.match_iou:
                        candidates.append((score, k, i))
                    elif high:
                        # Motion-gated strong recovery when a fast phone has no box overlap.
                        # Weak detections cannot use this fallback or create a new ID.
                        pw,ph = predicted[2]-predicted[0],predicted[3]-predicted[1]
                        dw,dh = d.x2-d.x1,d.y2-d.y1
                        distance = math.hypot((predicted[0]+predicted[2]-d.x1-d.x2)/2,
                                              (predicted[1]+predicted[3]-d.y1-d.y2)/2)
                        limit = max(pw,ph)*(1.1+min(.3,now-track.last_seen)*2)
                        area_ratio = dw*dh/max(1.,pw*ph)
                        aspect_ratio = (dw/max(1.,dh))/max(.01,pw/max(1.,ph))
                        if distance < limit and .5 < area_ratio < 2. and .6 < aspect_ratio < 1.7:
                            candidates.append((.1*(1-distance/max(1.,limit)),k,i))
            for _, k, i in sorted(candidates, reverse=True):
                if k not in unused or i not in remaining:
                    continue
                t, d = self.tracks[k], detections[i]
                dt = max(.01, now-t.last_seen)
                # Small smoothing only; follow motion, do not average a long box history.
                pred = t.predict(now)
                bbox = tuple(.8*x + .2*p for x,p in zip(coords(d), pred))
                t.velocity = tuple(.4*v + .6*(x-o)/dt for v,x,o in zip(t.velocity,bbox,t.bbox))
                t.bbox, t.last_seen, t.conf = bbox, now, d.conf
                t.hits += int(d.conf >= self.strong)
                if d.conf >= self.strong:
                    t.last_strong = now
                unused.remove(k); remaining.remove(i); observed.add(k)
        for i in sorted(remaining):
            d = detections[i]
            if d.conf < self.strong:
                continue
            k = self.next_id; self.next_id += 1
            self.tracks[k] = Track(k, coords(d), now, d.conf, last_strong=now)
            observed.add(k)
        return [Box(t.conf, *map(int, t.bbox if k in observed else t.predict(now)),
                    track_id=k, confirmed=t.hits >= self.min_hits, observed=k in observed, velocity=t.velocity,strong_at=t.last_strong)
                for k,t in self.tracks.items()]
