"""Freshness-gated independent HEAD/GAZE and monotonic hold/cooldown rules."""
import time
from .models import FaceResult,YoloResult,HandResult
from .directions import DirectionState
from .tracking import iou,coords

class HoldRule:
    def __init__(self, hold, cooldown=5., gap_tolerance=.3):
        self.hold,self.cooldown,self.gap = hold,cooldown,gap_tolerance
        self._start = self._gap_start = None
        self._last_fire = -1e9

    def reset(self):
        self._start = self._gap_start = None

    def update(self, cond, now):
        if self._gap_start is not None and now-self._gap_start > self.gap:
            self.reset()  # also reset on true: a gap does not disappear between samples
        if cond:
            if self._start is None:
                self._start = now
            self._gap_start = None
            if now-self._start >= self.hold and now-self._last_fire >= self.cooldown:
                self._last_fire = now
                return True
        elif self._start is not None:
            if self._gap_start is None:
                self._gap_start = now
        return False

    def active_duration(self, now):
        return max(0.,now-self._start) if self._start is not None else 0.

class RuleEngine:
    def __init__(self,cfg,calib_base=None):
        self.cfg,self.calib = cfg,calib_base or {}
        self.face,self.yolo,self.hands = FaceResult(),YoloResult(),HandResult()
        self.phone_voted = False
        self.debug = {}
        self.head,self.gaze = DirectionState("head"),DirectionState("gaze")
        r = cfg["rules"]
        self.key_of = {"PHONE_DETECTED":"phone_detected","PHONE_RAISED":"phone_raised",
                       "PHONE_AIMED":"phone_aimed","PHONE_LIFTED":"phone_raised","PHONE_IN_HAND":"phone_detected",
                       "NO_FACE":"no_face","MULTI_FACE":"multi_face","CAMERA_COVERED":"camera_covered"}
        for prefix in ("HEAD","GAZE"):
            for d in ("LEFT","RIGHT","UP","DOWN"):
                self.key_of[f"{prefix}_{d}"] = "gaze_down" if d in ("UP","DOWN") else "gaze_side"
        self.rules = {name:HoldRule(r[key]["hold"],r[key]["cooldown"],r.get("gap_tolerance",.3))
                      for name,key in self.key_of.items()}
        self.rules["PHONE_LIFTED"].hold = .15

    def reset(self):
        for rule in self.rules.values():
            rule.reset()
        self.head.reset(); self.gaze.reset()

    def on_face(self,f):
        if f.primary_changed:
            self.calib={}
            self.reset()
        self.face = f

    def on_yolo(self,y,voted=None):
        self.yolo = y
        self.phone_voted = y.phone_voted if voted is None else voted

    def on_hands(self,h):
        self.hands = h

    @staticmethod
    def fresh(result,now,ttl=.7):
        # Zero timestamp supports explicitly supplied synchronous fixtures; real workers always timestamp.
        return not result.error and (result.captured_at == 0 or 0 <= now-result.captured_at <= ttl)

    def _in_shoot_zone(self,face_valid):
        if not face_valid or not self.face.face_box:
            return False  # no face is NOT evidence of a raised phone
        fx1,fy1,fx2,fy2 = self.face.face_box
        fw,fh = fx2-fx1,fy2-fy1
        for p in self.yolo.phones:
            if not (p.observed and p.confirmed):
                continue
            cx,cy = (p.x1+p.x2)/2,(p.y1+p.y2)/2
            if fx1-fw*.7 < cx < fx2+fw*.7 and fy1-fh*.6 < cy < fy2-fh*.1:
                return True
        return False

    def tick(self,now=None,directions_enabled=True):
        now = time.monotonic() if now is None else now
        f,y = self.face,self.yolo
        ff,yf,hf = self.fresh(f,now),self.fresh(y,now),self.fresh(self.hands,now,.5)
        present = ff and f.n_faces >= 1
        base = dict(self.calib)
        base["head_x_threshold"] = max(base.get("head_x_threshold",0),self.cfg["rules"]["gaze_side"]["yaw_thresh"])
        base["head_y_threshold"] = max(base.get("head_y_threshold",0),self.cfg["rules"]["gaze_down"]["pitch_thresh"])
        base.setdefault("gaze_x_threshold",self.cfg["rules"]["gaze_side"]["iris_thresh"])
        base.setdefault("gaze_y_threshold",self.cfg["rules"]["gaze_down"].get("iris_thresh",.12))
        head = self.head.update(f.yaw,f.pitch,base,now,present and f.pose_valid and not f.primary_changed)
        gh,gv = f.iris_h,f.iris_v
        offsets = []
        for name,eye in (("left",f.left_eye),("right",f.right_eye)):
            center = base.get("eye_centers",{}).get(name)
            if eye is not None and center is not None:
                offsets.append((eye[0]-center[0],eye[1]-center[1]))
        if offsets:
            gh = base.get("iris_h",.5)+sum(e[0] for e in offsets)/len(offsets)
            gv = base.get("iris_v",.5)+sum(e[1] for e in offsets)/len(offsets)
        gaze = self.gaze.update(gh,gv,base,now,present and f.gaze_valid and not f.primary_changed)
        phone = yf and self.phone_voted
        raised = phone and self._in_shoot_zone(present)
        lifted = phone and any(p.confirmed and p.observed and (p.velocity[1]+p.velocity[3])/2 < -max(60.,(p.y2-p.y1)*1.2) for p in y.phones)
        held = phone and hf and any(iou(coords(p),b) > .015 for p in y.phones if p.confirmed and p.observed for b in self.hands.boxes)
        conds = {"PHONE_DETECTED":phone,"PHONE_RAISED":raised,"PHONE_LIFTED":lifted,"PHONE_AIMED":raised,
                 "PHONE_IN_HAND":held,"NO_FACE":ff and f.n_faces == 0,
                 "MULTI_FACE":ff and f.n_faces >= 2,
                 "CAMERA_COVERED":ff and f.brightness < self.cfg["rules"]["camera_covered"]["brightness_thresh"]
                    and f.variance < self.cfg["rules"]["camera_covered"]["variance_thresh"]}
        for prefix,state in (("HEAD",head),("GAZE",gaze)):
            for d in ("LEFT","RIGHT","UP","DOWN"):
                conds[f"{prefix}_{d}"] = directions_enabled and state == d
        out = []
        for k,c in conds.items():
            if self.cfg["rules"][self.key_of[k]].get("enabled",True) and self.rules[k].update(c,now):
                out.append(k)
        self.debug = dict(head=head,gaze=gaze,dyaw=f.yaw-base.get("yaw",0),dpitch=f.pitch-base.get("pitch",0),
                          iris_h=float(gh),iris_v=float(gv),
                          gaze_dx=float(gh-base.get("iris_h",.5)),gaze_dy=float(gv-base.get("iris_v",.5)),
                          thresholds={"yaw":base["head_x_threshold"],"pitch":base["head_y_threshold"],
                                      "gaze_h":base["gaze_x_threshold"],"gaze_v":base.get("gaze_y_threshold",.12)},
                          gaze_entry_gates=dict(self.gaze.entry_gates),head_entry_gates=dict(self.head.entry_gates),
                          calibrated=bool(self.calib),n_faces=f.n_faces if ff else None,
                          phones=len(y.phones) if yf else None,bright=f.brightness,conds=conds,
                          durations={k:r.active_duration(now) for k,r in self.rules.items()},
                          warnings_active=[k for k in conds if conds[k] and self.rules[k].active_duration(now)>=self.rules[k].hold],
                          camera_fresh=ff,phone_aimed="heuristic: position+hold, not camera orientation")
        return out
