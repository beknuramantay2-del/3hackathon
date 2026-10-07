"""Synthetic regressions; these do NOT measure real gaze/phone recall."""
import os,time,json,hashlib
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np,cv2,pytest,yaml
from proctor.core.mandatory_calibration import MandatoryCalibration,calibration_complete
from proctor.core.calibration import CalibrationError,POSES
from proctor.core.geometry import head_pose_mesh
from proctor.core.models import FaceResult,YoloResult,Box
from proctor.core.rules import RuleEngine
from proctor.core.episodes import EpisodePolicy
from proctor.core.store import Store
from proctor.core.evidence import EvidenceRecorder
from proctor.core.pipeline import FramePacket

HEAD={'CENTER':(2,-4),'LEFT':(-18,-4),'RIGHT':(22,-4),'UP':(2,-24),'DOWN':(2,16)}
GAZE={'CENTER':(.5,.5),'LEFT':(.7,.5),'RIGHT':(.3,.5),'UP':(.5,.3),'DOWN':(.5,.7)}

def feed_pass(c,start,head=False,missing=None,static=False):
    for i,p in enumerate(POSES):
        if p==missing:continue
        yaw,pitch=HEAD[p] if head else HEAD['CENTER']
        h,v=GAZE['CENTER'] if head or static else GAZE[p]
        for n in range(10):
            f=FaceResult(n_faces=1,yaw=yaw,pitch=pitch,iris_h=h,iris_v=v,
                left_eye=(h-.02,v),right_eye=(h+.02,v),captured_at=start+i*3+.7+n*.1)
            assert c.feed(f)

def complete():
    c=MandatoryCalibration();c.start(100)
    feed_pass(c,100,head=True);assert not c.advance(115)
    feed_pass(c,115);assert c.advance(130)
    return c.base

def test_all_ten_targets_and_learned_head_range():
    base=complete();assert calibration_complete(base)
    assert base['head_x_threshold']==8 and base['head_y_threshold']==8
    assert base['head_targets']['LEFT'][0]==-20
    cfg=yaml.safe_load(open('proctor/config.yaml'))
    e=RuleEngine(cfg,base);e.on_face(FaceResult(n_faces=1,yaw=-11,pitch=-4,iris_h=.5,iris_v=.5))
    e.tick(100)
    assert e.debug['thresholds']['yaw']==8 # not clamped to config's 18°
    assert e.debug['head']=='LEFT' and e.debug['gaze']=='CENTER'

@pytest.mark.parametrize('missing',POSES)
def test_head_pass_requires_every_pose(missing):
    c=MandatoryCalibration();c.start(100);feed_pass(c,100,True,missing=missing)
    with pytest.raises(CalibrationError):c.advance(115)
    assert not calibration_complete(c.base)

@pytest.mark.parametrize('missing',[None,'LEFT','UP','DOWN'])
def test_static_or_partial_gaze_does_not_unlock(missing):
    c=MandatoryCalibration();c.start(100);feed_pass(c,100,True);c.advance(115)
    feed_pass(c,115,missing=missing,static=missing is None)
    with pytest.raises(CalibrationError):c.advance(130)
    assert not calibration_complete(c.base)

def test_person_change_invalidates_whole_calibration():
    c=MandatoryCalibration();c.start(100);c.feed(FaceResult(n_faces=1,primary_changed=True))
    with pytest.raises(CalibrationError):c.advance(101)

@pytest.mark.parametrize('yaw,pitch,roll',[(20,0,0),(0,20,0),(0,0,25),(-20,-15,10)])
def test_face_basis_rotation_axes(yaw,pitch,roll):
    a=np.zeros((478,3));a[33]=[-50,0,0];a[263]=[50,0,0];a[10]=[0,-70,0];a[152]=[0,100,0]
    r=lambda deg: np.deg2rad(deg)
    x,y,z=map(r,(pitch,yaw,roll));cx,sx=np.cos(x),np.sin(x);cy,sy=np.cos(y),np.sin(y);cz,sz=np.cos(z),np.sin(z)
    rx=np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]]);ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]]);rz=np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    found=head_pose_mesh(a@(rz@ry@rx).T)
    assert found==pytest.approx((yaw,pitch,roll),abs=1e-5)

def test_degenerate_mesh_unknown():
    assert head_pose_mesh(np.zeros((478,3))) is None

@pytest.mark.parametrize('kind',['HEAD_LEFT','HEAD_RIGHT','HEAD_UP','HEAD_DOWN','GAZE_LEFT','GAZE_RIGHT','GAZE_UP','GAZE_DOWN'])
def test_directions_three_yellow_five_red_once(kind):
    p=EpisodePolicy();notices=[]
    for i in range(81):
        changes,rows=p.update({kind:True},100+i*.1);notices+=rows
    assert [(row['level'],round(row['duration'],1)) for row in notices]==[(1,3.),(2,5.)]
    assert notices[0]['ident']==notices[1]['ident']

def test_short_deviation_recorded_no_alert_and_gap_hysteresis():
    p=EpisodePolicy();changes=[];notices=[]
    for i in range(22):
        r,n=p.update({'GAZE_DOWN':i not in (10,11)},100+i*.1);changes+=r;notices+=n
    assert len(p.active)==1 and not notices
    ident=p.active['GAZE_DOWN'].ident
    r,n=p.update({'GAZE_DOWN':False},102.6)
    assert r[0]['closed'] and r[0]['duration']==pytest.approx(2.1)
    assert not p.active
    r,n=p.update({'GAZE_DOWN':True},102.7);assert r[0]['ident']!=ident and r[0]['duration']==0

def test_phone_first_reliable_result_not_predicted_or_stale():
    cfg=yaml.safe_load(open('proctor/config.yaml'));e=RuleEngine(cfg);p=EpisodePolicy()
    e.debug={'conds':{}}
    e.on_yolo(YoloResult(seq=1,captured_at=100,candidates=[Box(.7,0,0,20,30)]))
    cond=p.observations(e,100,False);changes,notices=p.update(cond,100)
    assert notices[0]['kind']=='PHONE_DETECTED' and notices[0]['level']==2 and notices[0]['duration']==0
    assert not p.observations(e,101,False)['PHONE_DETECTED']
    e.on_yolo(YoloResult(seq=2,captured_at=101,candidates=[Box(.7,0,0,20,30,observed=False)]))
    assert not p.observations(e,101,False)['PHONE_DETECTED']
    e.on_yolo(YoloResult(seq=3,captured_at=101,candidates=[Box(.38,0,0,20,30)]))
    assert not p.observations(e,101,False)['PHONE_DETECTED']
    e.yolo.phone_voted=True;assert p.observations(e,101,False)['PHONE_DETECTED']

def test_mandatory_ui_cannot_show_calibrated_partial():
    from PyQt6.QtWidgets import QApplication
    from proctor.ui.monitor_window import MonitorWindow
    app=QApplication.instance() or QApplication([]);w=MonitorWindow()
    w.show_directions('RIGHT','DOWN',False)
    assert 'Не определяется' in w.head_card.state.text() and 'Не определяется' in w.gaze_card.state.text()
    assert w.neutral.isHidden() and not w.neutral.isEnabled()
    assert '30 с' in w.five.text();assert w.gaze_hold.value()==3 and w.down_hold.value()==5
    w.close()

def test_evidence_real_codec_database_preroll_and_hash(tmp_path):
    store=Store(str(tmp_path/'session.db'),str(tmp_path/'shots'));r=EvidenceRecorder(store,tmp_path/'clips',fps=10,pre=.5,post=.2,segment=5)
    p=EpisodePolicy();ident=None;start=time.monotonic()
    try:
        for i in range(18):
            now=time.monotonic();r.push(FramePacket(i,now,np.full((480,640,3),40+i*5,np.uint8)))
            if i==6:
                changes,_=p.update({'GAZE_DOWN':True},now);ident=changes[0]['ident'];r.submit(changes)
            if i==12:r.submit(p.close())
            time.sleep(.11)
        r.stop()
        assert not r.error and not r.failures
        rows=store.episode_rows();assert len(rows)==1 and rows[0][5]==1
        clips=store.clips_for(ident);assert len(clips)==1
        path,meta=clips[0];assert meta['capture_times'][0]<start+.6
        assert meta['frames']>=9 and meta['source']=='camera' and not meta['truncated']
        assert meta['sha256']==hashlib.sha256(open(path,'rb').read()).hexdigest()
        cap=cv2.VideoCapture(path);count=0
        while True:
            ok,frame=cap.read()
            if not ok:break
            assert frame.shape[:2]==(360,480);count+=1
        cap.release();assert count==meta['frames'];assert len(r.ring)<=7
    finally:
        if r.thread.is_alive():r.stop()
        store.close()

def test_guard_pulse_and_actual_measurement_age():
    row=EpisodePolicy.pulse('HOTKEY_BLOCKED',100,2)
    assert row['closed'] and row['duration']==0 and row['level']==2
    cfg=yaml.safe_load(open('proctor/config.yaml'));e=RuleEngine(cfg)
    e.debug={'conds':{'GAZE_DOWN':True,'PHONE_DETECTED':True}}
    e.face=FaceResult(seq=1,captured_at=99.9);e.yolo=YoloResult(seq=2,captured_at=99.8)
    ages=EpisodePolicy.ages(e,100)
    assert ages['GAZE_DOWN']==pytest.approx(100.) and ages['PHONE_DETECTED']==pytest.approx(200.)
    p=EpisodePolicy();changes,_=p.update({'GAZE_DOWN':True},100,ages)
    assert changes[0]['measurement_age_ms']==pytest.approx(100.)
