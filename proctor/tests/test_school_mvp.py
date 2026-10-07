"""Logic/UX regressions, not a claim of real-world CV accuracy."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest,yaml,numpy as np
from PyQt6.QtWidgets import QApplication
from proctor.core.models import FaceResult,HandResult,Box
from proctor.core.rules import RuleEngine
from proctor.core.calibration import Calibration
from proctor.core.phone_evidence import PartialPhoneEvidence
from proctor.core.detector_yolo import DetectorYolo
from proctor.core.pipeline import FramePacket
from proctor.core.overlay import render_overlay
from proctor.ui.monitor_window import MonitorWindow,DIRECTIONS

@pytest.fixture
def engine():
    cfg=yaml.safe_load(open('proctor/config.yaml',encoding='utf8'))
    return RuleEngine(cfg,{'yaw':0,'pitch':0,'iris_h':.5,'iris_v':.5})

def fresh_face(t,v=.8,valid=True):
    return FaceResult(n_faces=1,iris_h=.5,iris_v=v,pose_valid=True,gaze_valid=valid,
        brightness=120,variance=2000,seq=int(t*10),captured_at=100+t)

def test_down_real_timer_short_blink_and_return(engine):
    events=[]
    for i in range(33):
        t=i*.1;engine.on_face(fresh_face(t,valid=i not in (12,13)))
        found=engine.tick(100+t);events += [(t,k) for k in found]
        assert engine.debug['head']=='CENTER'
    down=[t for t,k in events if k=='GAZE_DOWN']
    assert len(down)==1 and 3<=down[0]<=3.2
    for i in range(33,42):
        t=i*.1;engine.on_face(fresh_face(t,.5));engine.tick(100+t)
    assert engine.debug['gaze']=='CENTER'
    assert engine.rules['GAZE_DOWN'].active_duration(104.2)==0

@pytest.mark.parametrize('h,v,direction',[(.5,.5,'CENTER'),(.8,.5,'LEFT'),(.2,.5,'RIGHT'),(.5,.2,'UP'),(.5,.8,'DOWN')])
def test_all_eye_directions_separate_from_head(engine,h,v,direction):
    engine.on_face(FaceResult(n_faces=1,iris_h=h,iris_v=v,yaw=0,brightness=120,variance=2000))
    engine.tick(100)
    assert engine.debug['head']=='CENTER' and engine.debug['gaze']==direction


def test_calibration_does_not_consume_direction_cooldown(engine):
    for i in range(42):
        t=i*.1;engine.on_face(fresh_face(t));assert 'GAZE_DOWN' not in engine.tick(100+t,directions_enabled=False)
    events=[]
    for i in range(42,75):
        t=i*.1;engine.on_face(fresh_face(t));events.extend(engine.tick(100+t))
    assert 'GAZE_DOWN' in events


def test_quick_calibration_partial_not_fake_success():
    c=Calibration(10,min_samples=6,settle=.4,reject_head_motion=True);c.start(100)
    for n in range(12):c.add(0,0,.5,.5,now=100+.5+n*.1)
    for n in range(8):assert not c.add(20,0,.8,.5,now=102+.5+n*.1)
    base=c.finish(allow_partial=True)
    assert base['yaw']==0 and base['unresolved_targets']==['LEFT','RIGHT','UP','DOWN']
    assert base['gaze_targets']=={}


def test_weak_phone_requires_hand_time_and_real_candidate():
    gate=PartialPhoneEvidence();hands=HandResult(boxes=[(0,30,50,100)],captured_at=100)
    box=Box(.18,10,10,40,80)
    assert not gate.update([box],hands,100,.25)[0].supported
    for t in (100.2,100.4):
        hands.captured_at=t;box=gate.update([Box(.18,10,10,40,80)],hands,t,.25)[0]
    assert box.supported and box.conf==.18
    stale=gate.update([Box(.18,10,10,40,80)],hands,101.1,.25)[0]
    assert not stale.supported
    assert gate.update([],hands,101.2,.25)==[]


def test_weak_phone_without_hand_never_starts():
    d=DetectorYolo(adaptive=False,detail_search=False)
    d._infer=lambda *a:([Box(.18,10,10,40,80)],[])
    for n in range(8):
        r=d.process(FramePacket(n,100+n*.2,np.zeros((100,100,3),np.uint8)))
        assert not r.phone_voted and not r.phones


def test_hand_supported_partial_phone_then_raising_confidence():
    d=DetectorYolo(adaptive=False,detail_search=False)
    d._infer=lambda *a:([Box(.18,10,10,40,80)],[])
    hand=HandResult(boxes=[(0,30,50,100)])
    d.hand_provider=lambda:hand
    for n in range(6):
        hand.captured_at=100+n*.2
        r=d.process(FramePacket(n,hand.captured_at,np.zeros((100,100,3),np.uint8)))
    assert r.phone_voted and r.phones[0].supported and r.phones[0].conf==.18
    assert r.phones[0].strong_at is None  # not a fabricated high-confidence anchor
    d.requested_confidence=.6;hand.captured_at=101.2
    r=d.process(FramePacket(7,101.2,np.zeros((100,100,3),np.uint8)))
    assert not r.phone_voted


def test_preview_arrows_need_valid_calibrated_eyes():
    p=FramePacket(1,100.,np.zeros((100,120,3),np.uint8))
    f=FaceResult(captured_at=100.,eye_points=[(60,50)],gaze_valid=True)
    ordinary=render_overlay(p,f,None,None,mirror=True,states={'gaze':'DOWN','calibrated':False})
    arrow=render_overlay(p,f,None,None,mirror=True,states={'gaze':'DOWN','calibrated':True})
    assert ordinary[60:70,58:63].sum()==0 and arrow[60:70,58:63].sum()>0


def test_school_window_direction_cases_and_controls():
    app=QApplication.instance() or QApplication([])
    w=MonitorWindow();w.resize(1280,800);w.show();app.processEvents()
    w.show_directions('CENTER','DOWN',True)
    assert 'Вниз' in w.gaze_card.state.text() and 'Прямо' in w.head_card.state.text()
    assert 'ГЛАЗА: ↓ Вниз' in w.gaze_card.state.text()
    assert len(w.rows)==17 and w.tabs.count()==3
    assert w.down_hold.value()==3 and w.gaze_hold.value()==2
    w.show_target('LEFT');app.processEvents()
    assert 0<=w.target.x()<w.width() and 0<=w.target.y()<w.height()
    assert w.camera.width()>300 and w.camera.height()>=220
    assert all(w.tabs.tabText(i) in ('События','Кейс №3','Настройки') for i in range(3))
    w.close()


def test_persistent_near_strong_phone_requires_actual_global_roi_agreement():
    d=DetectorYolo(adaptive=False)
    def infer(image,size,classes,offset=(0,0)):
        if classes==[67]:return [Box(.235,10,10,40,80,source='detail')],[]
        return [Box(.18,10,10,40,80)],[]
    d._infer=infer
    for n in range(8):r=d.process(FramePacket(n,100+n*.2,np.zeros((100,100,3),np.uint8)))
    assert r.phone_voted and r.phones[0].support_kind=='ROI+time'
    assert r.phones[0].conf==.235 and r.phones[0].strong_at is None
    # Missing observations are not perpetuated as phone evidence.
    d._infer=lambda *a: ([],[])
    r=d.process(FramePacket(9,101.8,np.zeros((100,100,3),np.uint8)))
    assert not r.phone_voted and not any(b.observed for b in r.phones)


def test_primary_change_invalidates_personal_centre(engine):
    engine.on_face(FaceResult(n_faces=1,primary_changed=True))
    assert not engine.calib
