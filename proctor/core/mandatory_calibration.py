"""Two labelled passes; a neutral-only/partial calibration never unlocks gaze."""
import numpy as np
from .calibration import Calibration, CalibrationError, POSES
LABELS={"CENTER":"Прямо","LEFT":"Влево","RIGHT":"Вправо","UP":"Вверх","DOWN":"Вниз"}


def calibration_complete(base):
    return bool(base.get('complete') and base.get('cal_version') == 2 and
                all(p in base.get(k, {}) for k in ('head_targets', 'gaze_targets') for p in POSES[1:]))


class MandatoryCalibration:
    duration = 30.
    def __init__(self, min_samples=8):
        self.min_samples = min_samples
        self.stage = 'head'
        self.cal = Calibration(15., min_samples=min_samples, settle=.6)
        self.head_base = {}; self.base = {}; self.done = False; self.invalid=False
        self.started_at = None
        self._neutral_preview = {}

    def start(self, now):
        self.started_at = now
        self.cal.start(now)

    def phase(self, now):
        return self.cal.phase(now-self.cal.started_at)

    def remaining(self, now):
        return max(0., 15.-(now-self.cal.started_at)) + (15. if self.stage == 'head' else 0.)

    def feed(self, f):
        if f.primary_changed:self.invalid=True
        if self.done or f.n_faces != 1 or not f.pose_valid or self.invalid:
            return False
        if self.stage == 'gaze':
            if not f.gaze_valid or f.left_eye is None or f.right_eye is None:
                return False
            if abs(f.yaw-self.head_base['yaw']) > 7 or abs(f.pitch-self.head_base['pitch']) > 7:
                return False
        return self.cal.add(f.yaw, f.pitch, f.iris_h if self.stage == 'gaze' else .5,
                            f.iris_v if self.stage == 'gaze' else .5, now=f.captured_at,
                            left_eye=f.left_eye, right_eye=f.right_eye)

    def preview(self):
        """Measured neutral/partial targets for DISPLAY, never marks calibration complete.
        Freeze neutral from the labelled CENTER step, not from arbitrary stable turns.
        Keep a valid head pass when the subsequent iris pass fails.
        """
        if self.invalid:return {}
        if self.stage=='head' and not self._neutral_preview:
            try:center,mad=self.cal.trimmed(self.cal.samples['CENTER'],self.min_samples)
            except CalibrationError:return {}
            if max(mad[:2])>3:return {}
            self._neutral_preview=dict(yaw=float(center[0]),pitch=float(center[1]),head_center_measured=True,
                head_x_threshold=18.,head_y_threshold=12.,gaze_x_threshold=.06,gaze_y_threshold=.06,
                head_targets={},gaze_targets={},eye_centers={},complete=False)
        base=dict(self._neutral_preview)
        if not base:return {}
        if self.head_base:base.update(self.head_base)
        if self.stage=='gaze':
            # A copy: finish() on the live collector would stop accepting samples.
            partial=Calibration(15.,min_samples=self.min_samples)
            partial.samples={p:list(v) for p,v in self.cal.samples.items()}
            partial.eye_samples={p:list(v) for p,v in self.cal.eye_samples.items()}
            try:
                measured=partial.finish(allow_partial=True)
                for key in ('yaw','pitch','iris_h','iris_v','eye_centers','gaze_targets','gaze_x_threshold','gaze_y_threshold'):
                    base[key]=measured[key]
            except CalibrationError:pass
        if not base.get('eye_centers'):
            centers={}
            for index,name in enumerate(('left','right')):
                values=[e[index] for e in self.cal.eye_samples['CENTER'] if e[index] is not None]
                if len(values)<self.min_samples:continue
                values=np.asarray(values);center=np.median(values,axis=0);noise=np.median(abs(values-center),axis=0)
                if np.isfinite(values).all() and noise[0]<=.04 and noise[1]<=.06:centers[name]=list(map(float,center))
            base['eye_centers']=centers
        if base.get('eye_centers'):
            center=np.mean(list(base['eye_centers'].values()),axis=0)
            base.update(iris_h=float(center[0]),iris_v=float(center[1]),gaze_center_measured=True)
        else:base['gaze_center_measured']=False
        base['complete']=False
        self._neutral_preview=dict(base)
        return base

    def _finish_head(self):
        measured={}
        for p in POSES:
            try:measured[p]=self.cal.trimmed(self.cal.samples[p],self.min_samples)
            except CalibrationError as exc:raise CalibrationError('Голова · '+LABELS[p]+': '+str(exc)) from exc
        center, mad = measured['CENTER']
        if max(mad[:2]) > 3:
            raise CalibrationError('Голова в центре нестабильна: сядьте прямо и повторите')
        targets = {}
        for p in POSES[1:]:
            axis = 0 if p in ('LEFT', 'RIGHT') else 1
            delta = measured[p][0][:2]-center[:2]
            if abs(delta[axis]) < max(7., 5*mad[axis]) or abs(delta[1-axis]) > max(5., .8*abs(delta[axis])):
                raise CalibrationError('Не различён поворот головы: '+LABELS[p])
            targets[p] = list(map(float, delta))
        thresholds = []
        for a, b, axis in (('LEFT', 'RIGHT', 0), ('UP', 'DOWN', 1)):
            if targets[a][axis]*targets[b][axis] >= 0:
                raise CalibrationError('Противоположные повороты головы не различаются')
            thresholds.append(max(4., 4*mad[axis], .4*min(abs(targets[a][axis]), abs(targets[b][axis]))))
        return dict(yaw=float(center[0]), pitch=float(center[1]), head_targets=targets,
                    head_x_threshold=thresholds[0], head_y_threshold=thresholds[1], head_calibrated=True)

    def advance(self, now):
        if self.invalid:raise CalibrationError('Основное лицо сменилось: повторите всю настройку')
        if self.done or now-self.cal.started_at < 15.:
            return False
        if self.stage == 'head':
            self.preview()
            self.head_base = self._finish_head()
            self.stage = 'gaze'
            self.cal = Calibration(15., min_samples=self.min_samples, settle=.6, reject_head_motion=True)
            self.cal.start(now)
            return False
        for p in POSES:
            try:self.cal.trimmed(self.cal.samples[p],self.min_samples)
            except CalibrationError as exc:raise CalibrationError('Глаза · '+LABELS[p]+': '+str(exc)) from exc
        base = self.cal.finish(allow_partial=False)
        if base['unresolved_targets'] or len(base['eye_centers']) != 2:
            raise CalibrationError('Не различены глаза: '+', '.join(LABELS[p] for p in base['unresolved_targets'])+'. Свет на лицо, без бликов; повторите')
        # The neutral head sample in the eyes pass compensates repositioning between passes.
        yaw, pitch = base['yaw'], base['pitch']
        base.update(self.head_base)
        base.update(yaw=yaw, pitch=pitch, complete=True, cal_version=2, mode='head+eyes')
        self.base = base; self.done = True
        return True
