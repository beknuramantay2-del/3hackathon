"""Role/attempt/HTML contracts. Synthetic students, not real school/user data."""
import os,json,sqlite3
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from proctor.school.database import SchoolDB,SchoolError
from proctor.school.exam import SchoolExam,exam_html

@pytest.fixture
def school(tmp_path):
    db=SchoolDB(tmp_path/'school.db');owner=db.setup_owner('owner','Test Administrator','Owner-password-123')
    admin=db.login('OWNER','Owner-password-123');sid=db.register_student('student','Test Student','Student-password-123')
    student=db.login('student','Student-password-123')
    tid=db.create_test(admin,'Synthetic test',60,[dict(text='2 + 2?',options=['3','4'],correct=1)])
    yield db,admin,student,tid,sid
    db.close()

def test_first_owner_is_atomic_and_registration_never_elevates(school):
    db,a,s,t,u=school
    with pytest.raises(SchoolError):db.setup_owner('second','Fake Owner','Strong-password-123')
    assert db.actor(s)['role']=='student'
    with pytest.raises(SchoolError):db.create_user(s,'evil','Evil User','Strong-password-123','admin')
    assert 'salt' not in db.users(a)[0] and 'hash' not in db.users(a)[0]

def test_password_hashes_and_session_tokens_not_plain(school):
    db,a,s,t,u=school
    rows=db.con.execute('SELECT * FROM users').fetchall()
    assert all(r['hash']!='Owner-password-123' and r['iterations']>=300000 for r in rows)
    assert db.con.execute('SELECT 1 FROM sessions WHERE hash=?',(s,)).fetchone() is None
    with pytest.raises(SchoolError):db.actor('invented-session')
    db.logout(s)
    with pytest.raises(SchoolError):db.actor(s)

def test_failed_login_rate_limit(school):
    db,a,s,t,u=school
    for n in range(5):
        with pytest.raises(SchoolError):db.login('student','bad-password')
    with pytest.raises(SchoolError,match='минуту'):db.login('student','Student-password-123')
    assert db.con.execute('SELECT amount FROM failures WHERE login=?',('student',)).fetchone()[0]==5

def test_permissions_disable_and_last_admin(school):
    db,a,s,t,u=school
    with pytest.raises(SchoolError):db.users(s)
    with pytest.raises(SchoolError):db.set_active(s,u,False)
    with pytest.raises(SchoolError):db.set_active(a,db.actor(a)['id'],False)
    db.set_active(a,u,False)
    with pytest.raises(SchoolError):db.actor(s)
    with pytest.raises(SchoolError):db.login('student','Student-password-123')

@pytest.mark.parametrize('question',[dict(text='',options=['a','b'],correct=0),dict(text='Question',options=['a'],correct=0),dict(text='Question',options=['a','b'],correct=5),dict(text='Question',options=['a','b'],correct=True)])
def test_bad_questions_rejected(school,question):
    db,a,s,t,u=school
    with pytest.raises(SchoolError):db.create_test(a,'Test',60,[question])
    with pytest.raises(SchoolError):db.create_test(s,'Test',60,[dict(text='q',options=['a','b'],correct=0)])

def test_one_active_attempt_and_consent(school):
    db,a,s,t,u=school
    with pytest.raises(SchoolError):db.new_attempt(s,t)
    ident,lease=db.new_attempt(s,t,True)
    with pytest.raises(SchoolError):db.new_attempt(s,t,True)
    with pytest.raises(SchoolError):db.new_attempt(a,t,True)
    assert db.attempts(s)[0]['id']==ident
    assert db.con.execute('SELECT lease_hash FROM attempts').fetchone()[0]!=lease

def test_child_claim_public_html_and_server_side_grading(school):
    db,a,s,t,u=school;ident,lease=db.new_attempt(s,t,True);spec=db.claim(lease)
    assert all('correct' not in q for q in spec['questions'])
    html=exam_html(spec);assert 'correct' not in html and 'collectAnswers' in html
    with pytest.raises(SchoolError):db.claim(lease)
    with pytest.raises(SchoolError):db.finish(lease,{})
    db.start(lease);qid=spec['questions'][0]['id']
    with pytest.raises(SchoolError):db.finish(lease,{'foreign-question':1})
    with pytest.raises(SchoolError):db.finish(lease,{qid:True})
    with pytest.raises(SchoolError):db.finish(lease,{qid:8})
    assert db.finish(lease,json.dumps({qid:1}))==(1,1)
    assert db.attempts(a)[0]['score']==1
    with pytest.raises(SchoolError):db.finish(lease,{qid:1})

def test_missing_answer_is_wrong_not_implicit_correct(school):
    db,a,s,t,u=school;ident,lease=db.new_attempt(s,t,True);db.claim(lease);db.start(lease)
    assert db.finish(lease,{})==(0,1)

def test_students_cannot_inspect_other_students_or_cv(school):
    db,a,s,t,u=school;ident,lease=db.new_attempt(s,t,True)
    uid=db.register_student('other','Other Student','Other-password-123');other=db.login('other','Other-password-123')
    assert db.attempts(other)==[]
    with pytest.raises(SchoolError):db.inspection(s,ident)
    with pytest.raises(SchoolError):db.interrupt(other,ident)
    assert db.inspection(a,ident)==dict(events=[],episodes=[],clips=[])

def test_real_evidence_link_and_central_archive(school,tmp_path):
    from proctor.core.store import Store
    from proctor.core.events import make_violation
    db,a,s,t,u=school;ident,lease=db.new_attempt(s,t,True);db.claim(lease)
    run=tmp_path/'sessions'/'run';store=Store(str(run/'session.db'),str(run/'shots'))
    store.add(make_violation('HEAD_DOWN',5,'',{'episode_id':'sample'}))
    store.save_episode(dict(ident='sample',kind='HEAD_DOWN',started=100,duration=5,level=2,closed=True),0,'synthetic fixture')
    with pytest.raises(SchoolError):db.bind_run(lease,tmp_path/'outside')
    db.bind_run(lease,run,'synthetic fixture');store.close()
    data=db.inspection(a,ident);assert data['episodes'][0]['level']==2 and data['events'][0]['type']=='HEAD_DOWN'
    db.archive_evidence(lease)
    assert db.con.execute('SELECT COUNT(*) FROM evidence WHERE attempt_id=?',(ident,)).fetchone()[0]==2

def test_school_context_idempotent_cleanup_and_abort(school):
    db,a,s,t,u=school;ident,lease=db.new_attempt(s,t,True)
    context=SchoolExam(str(db.path),lease);context.close();context.close()
    assert db.attempts(a)[0]['status']=='interrupted' and db.attempts(a)[0]['score'] is None

def test_html_escapes_real_author_content():
    spec=dict(name='<script>bad</script>',title='<img src=x>',questions=[dict(id='abc',text='<script>alert(1)</script>',options=['<b>a</b>','b'])])
    html=exam_html(spec)
    assert '<script>bad</script>' not in html and '<img src=x>' not in html and '&lt;b&gt;a&lt;/b&gt;' in html

def test_native_role_hub_layout_and_hidden_student_admin_controls(school):
    from PyQt6.QtWidgets import QApplication,QPushButton
    from proctor.school.hub import Hub
    db,a,s,t,u=school;app=QApplication.instance() or QApplication([]);w=Hub(db,preview=True)
    w.token=s;w.actor=db.actor(s);w.dashboard();w.show();app.processEvents()
    assert w.tabs.count()==2 and not hasattr(w,'users_table')
    assert w.tests_table.rowCount()==1
    assert not any(b.text()=='Создать учётную запись' and b.isVisible() for b in w.findChildren(QPushButton))
    w.sign_out();w.token=a;w.actor=db.actor(a);w.dashboard();app.processEvents()
    assert w.tabs.count()==4 and w.users_table.rowCount()==2
    assert w.width()>=1000;w.timer.stop();w.close();app.processEvents()
