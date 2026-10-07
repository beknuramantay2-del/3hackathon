"""Opt-in native exam/HTML/bridge/SQLite E2E. Calibration is an explicit stub!
This proves account/test wiring, NOT real pose quality or physical OS guards.
"""
import os,subprocess,sys,json,textwrap
from pathlib import Path
import pytest,yaml,cv2
from proctor.school.database import SchoolDB

@pytest.mark.skipif(os.getenv('PROCTOR_SCHOOL_SMOKE')!='1',reason='opt-in native model/WebEngine school workflow')
def test_native_authenticated_exam_round_trip(tmp_path):
    image=cv2.imread(os.environ['PROCTOR_FACE_FIXTURE']);assert image is not None
    video=tmp_path/'static.mp4';h,w=image.shape[:2]
    cap=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),30,(w,h))
    for n in range(600):cap.write(image)
    cap.release()
    db=SchoolDB(tmp_path/'school.db');db.setup_owner('owner','Synthetic Administrator','Owner-password-123')
    admin=db.login('owner','Owner-password-123');db.register_student('student','Synthetic Student','Student-password-123');student=db.login('student','Student-password-123')
    test=db.create_test(admin,'Synthetic workflow test',60,[dict(text='Two plus two?',options=['Three','Four'],correct=1)])
    ident,lease=db.new_attempt(student,test,True)
    launch=tmp_path/'launch'/ident;launch.mkdir(parents=True)
    config=yaml.safe_load(Path('proctor/config.yaml').read_text());config['profile']='dev';config['test']['test_url']=str(launch/'exam.html');config['store']['db']=str(tmp_path/'session.db');config['hash_chain']['salt_hex']='';config['guard']['examiner']=db.guard_credentials()
    conf=launch/'config.yaml';conf.write_text(yaml.safe_dump(config))
    script=textwrap.dedent('''
        import sys,time
        import proctor.main as m
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from proctor.ui.test_window import TestWindow
        class ExplicitCalibrationStub:
            duration=30
            stage='gaze'
            base=dict(complete=True,cal_version=2,mode='synthetic-test-stub',head_calibrated=True,yaw=0,pitch=0,iris_h=.5,iris_v=.5,head_x_threshold=10,head_y_threshold=8,gaze_x_threshold=.08,gaze_y_threshold=.08,head_targets={'LEFT':[-20,0],'RIGHT':[20,0],'UP':[0,-20],'DOWN':[0,20]},gaze_targets={'LEFT':[.2,0],'RIGHT':[-.2,0],'UP':[0,-.2],'DOWN':[0,.2]})
            def start(self,now):self.started_at=now
            def feed(self,f):pass
            def phase(self,now):return 'CENTER'
            def remaining(self,now):return .5
            def advance(self,now):return now-self.started_at>.5
        m.MandatoryCalibration=ExplicitCalibrationStub
        m.monitor_count=lambda:1 # offscreen test has one synthetic display
        http_called=[]
        def never_http(*a,**k):
            http_called.append(True);raise AssertionError('Student must not expose legacy HTTP examiner page')
        m.ExaminerServer=never_http
        original=m.PreflightScreen.__init__
        def ready(self,*a,**kw):
            original(self,*a,**kw)
            seen=set()
            def drive():
                app=QApplication.instance()
                for obj in app.allWidgets():
                    if isinstance(obj,m.PreflightScreen) and obj.isVisible():
                        state=obj.cam_status.text()
                        if state not in seen:seen.add(state);print('Preflight:',state,flush=True)
                        if obj.start_btn.isEnabled():obj.start_btn.click()
                    if isinstance(obj,TestWindow):
                        obj.page.runJavaScript("if(window.bridge && document.querySelector('fieldset')){document.querySelector('input[value=\\\"1\\\"]').checked=true;submitQuiz();}")
            self.driver=QTimer(self);self.driver.timeout.connect(drive);self.driver.start(300)
            QTimer.singleShot(18000,QApplication.instance().quit)
        m.PreflightScreen.__init__=ready
        code=m.main();assert not http_called;sys.exit(code)
    ''')
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PROCTOR_SCHOOL_DB=str(db.path),PROCTOR_ATTEMPT_LEASE=lease)
    result=subprocess.run([sys.executable,'-c',script,'--embedded-test','--config',str(conf),'--profile','dev','--no-guard','--video',str(video)],env=env,text=True,capture_output=True,timeout=35)
    (tmp_path/'child.log').write_text(result.stdout+'\n'+result.stderr)
    try:
        assert result.returncode==0,result.stderr[-2000:]
        row=db.attempts(admin)[0]
        assert row['status']=='finished' and row['score']==1 and row['total']==1,(row,result.stdout[-3000:],result.stderr[-1500:])
        assert row['source']=='video replay' and Path(row['run_dir']).is_dir()
        assert db.con.execute('SELECT answers FROM attempts WHERE id=?',(ident,)).fetchone()[0]!='{}'
    finally:db.close()
