"""Single-client SQLite registry. Permissions enforced here, not just hidden in UI.
CV keeps its per-run hash chain; central registry stores attempts and final evidence.
"""
import hashlib,hmac,json,os,secrets,sqlite3,threading,time,uuid
from pathlib import Path
from contextlib import contextmanager

ITERATIONS=310000
ROLES=('student','examiner','admin')
SCHEMA='''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,login TEXT UNIQUE NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,active INT DEFAULT 1,salt TEXT,hash TEXT,iterations INT);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,user_id TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS failures(login TEXT PRIMARY KEY,amount INT,until_time REAL);
CREATE TABLE IF NOT EXISTS tests(id TEXT PRIMARY KEY,title TEXT,seconds INT,questions TEXT,published INT,author TEXT);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,user_id TEXT,test_id TEXT,status TEXT,created REAL,started REAL,ended REAL,questions TEXT,answers TEXT,score INT,total INT,lease_hash TEXT UNIQUE,lease_expiry REAL,claimed INT DEFAULT 0,run_dir TEXT,source TEXT,consent INT);
CREATE TABLE IF NOT EXISTS evidence(attempt_id TEXT,kind TEXT,source_id TEXT,payload TEXT,PRIMARY KEY(attempt_id,kind,source_id));
CREATE INDEX IF NOT EXISTS ix_attempt_student ON attempts(user_id,created);
'''

class SchoolError(ValueError):pass

class SchoolDB:
    def __init__(self,path='data/school.db'):
        self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.con=sqlite3.connect(self.path,check_same_thread=False,timeout=3,isolation_level=None)
        self.con.row_factory=sqlite3.Row;self.con.execute('PRAGMA journal_mode=WAL');self.con.execute('PRAGMA busy_timeout=3000');self.con.executescript(SCHEMA)
        try:os.chmod(self.path,0o600)
        except OSError:pass

    @contextmanager
    def tx(self):
        with self.lock:
            self.con.execute('BEGIN IMMEDIATE')
            try:yield;self.con.execute('COMMIT')
            except Exception:self.con.execute('ROLLBACK');raise

    @staticmethod
    def digest(token):return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def credentials(login,name,password):
        login=login.strip().casefold();name=name.strip()
        if not 3<=len(login)<=64 or any(c.isspace() for c in login):raise SchoolError('Логин: 3–64 символа, без пробелов')
        if not 2<=len(name)<=120:raise SchoolError('Имя: 2–120 символов')
        if not 10<=len(password)<=256:raise SchoolError('Пароль: 10–256 символов')
        salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),ITERATIONS).hex()
        return login,name,salt,digest

    def needs_owner(self):
        with self.lock:return not bool(self.con.execute('SELECT 1 FROM users LIMIT 1').fetchone())

    def _insert_user(self,login,name,password,role):
        if role not in ROLES:raise SchoolError('Неизвестная роль')
        login,name,salt,digest=self.credentials(login,name,password);ident=uuid.uuid4().hex
        try:self.con.execute('INSERT INTO users VALUES(?,?,?,?,1,?,?,?)',(ident,login,name,role,salt,digest,ITERATIONS))
        except sqlite3.IntegrityError as exc:raise SchoolError('Логин уже занят') from exc
        return ident

    def setup_owner(self,login,name,password):
        with self.tx():
            if not self.needs_owner():raise SchoolError('Первый администратор уже создан')
            return self._insert_user(login,name,password,'admin')

    def register_student(self,login,name,password):
        with self.tx():
            if self.needs_owner():raise SchoolError('Сначала создайте администратора')
            return self._insert_user(login,name,password,'student')

    def actor(self,token,roles=ROLES):
        with self.lock:
            row=self.con.execute('SELECT users.* FROM users JOIN sessions ON user_id=users.id WHERE sessions.hash=? AND expires>? AND active=1',(self.digest(token),time.time())).fetchone()
        if row is None or row['role'] not in roles:raise SchoolError('Доступ запрещён / войдите снова')
        return {k:row[k] for k in ('id','login','name','role')}

    def login(self,login,password):
        login=login.strip().casefold();now=time.time()
        with self.tx():
            failure=self.con.execute('SELECT amount,until_time FROM failures WHERE login=?',(login,)).fetchone()
            if failure and failure[1]>now:raise SchoolError('Слишком много попыток; повторите через минуту')
            row=self.con.execute('SELECT * FROM users WHERE login=?',(login,)).fetchone()
            salt=bytes.fromhex(row['salt']) if row else b'unknown-user-pad'
            result=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,row['iterations'] if row else ITERATIONS).hex()
            good=bool(row and row['active'] and hmac.compare_digest(result,row['hash']))
            if good:
                self.con.execute('DELETE FROM failures WHERE login=?',(login,))
                token=secrets.token_urlsafe(32);self.con.execute('INSERT INTO sessions VALUES(?,?,?)',(self.digest(token),row['id'],now+8*3600))
                return token
            amount=(failure[0] if failure and failure[1]==0 else 0)+1
            self.con.execute('INSERT OR REPLACE INTO failures VALUES(?,?,?)',(login,amount,now+60 if amount>=5 else 0))
        raise SchoolError('Неверный логин или пароль')

    def logout(self,token):
        with self.tx():self.con.execute('DELETE FROM sessions WHERE hash=?',(self.digest(token),))

    def create_user(self,token,login,name,password,role):
        with self.tx():
            self.actor(token,('admin',));return self._insert_user(login,name,password,role)

    def users(self,token):
        with self.lock:
            self.actor(token,('admin','examiner'))
            return [dict(r) for r in self.con.execute('SELECT id,login,name,role,active FROM users ORDER BY name')]

    def set_active(self,token,ident,active):
        with self.tx():
            actor=self.actor(token,('admin',))
            target=self.con.execute('SELECT * FROM users WHERE id=?',(ident,)).fetchone()
            if not target:raise SchoolError('Пользователь не найден')
            if not active and (ident==actor['id'] or target['role']=='admin' and self.con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]<=1):raise SchoolError('Нельзя отключить себя или последнего администратора')
            self.con.execute('UPDATE users SET active=? WHERE id=?',(int(bool(active)),ident))
            if not active:self.con.execute('DELETE FROM sessions WHERE user_id=?',(ident,))

    def create_test(self,token,title,seconds,questions):
        with self.tx():
            actor=self.actor(token,('admin','examiner'));title=title.strip()
            if not 2<=len(title)<=180 or type(seconds)!=int or not 60<=seconds<=10800:raise SchoolError('Название 2–180 символов; длительность 1–180 минут')
            if not isinstance(questions,list) or not 1<=len(questions)<=100:raise SchoolError('От 1 до 100 вопросов')
            clean=[]
            for q in questions:
                text=q.get('text','').strip();options=q.get('options',[]);correct=q.get('correct')
                if not 1<=len(text)<=2000 or not isinstance(options,list) or not 2<=len(options)<=8 or not all(isinstance(o,str) and 1<=len(o.strip())<=500 for o in options) or type(correct)!=int or not 0<=correct<len(options):raise SchoolError('Вопрос: текст, 2–8 вариантов, индекс правильного ответа')
                clean.append(dict(id=uuid.uuid4().hex,text=text,options=[o.strip() for o in options],correct=correct))
            ident=uuid.uuid4().hex;self.con.execute('INSERT INTO tests VALUES(?,?,?,?,1,?)',(ident,title,seconds,json.dumps(clean,ensure_ascii=False),actor['id']))
            return ident

    def tests(self,token):
        actor=self.actor(token)
        with self.lock:
            rows=self.con.execute('SELECT id,title,seconds,questions,published FROM tests'+(' WHERE published=1' if actor['role']=='student' else '')+' ORDER BY title').fetchall()
        return [dict(id=r['id'],title=r['title'],seconds=r['seconds'],count=len(json.loads(r['questions'])),published=r['published']) for r in rows]

    def new_attempt(self,token,test_id,consent=False):
        with self.tx():
            actor=self.actor(token,('student',))
            if not consent:raise SchoolError('Необходимо согласие на локальную запись')
            test=self.con.execute('SELECT * FROM tests WHERE id=? AND published=1',(test_id,)).fetchone()
            if not test:raise SchoolError('Тест не найден')
            if self.con.execute("SELECT 1 FROM attempts WHERE status IN ('preparing','calibrating','running')").fetchone():raise SchoolError('На этом клиенте уже есть активная попытка. Экзаменатор должен завершить её')
            ident=uuid.uuid4().hex;lease=secrets.token_urlsafe(32)
            self.con.execute('INSERT INTO attempts(id,user_id,test_id,status,created,questions,total,lease_hash,lease_expiry,consent) VALUES(?,?,?,?,?,?,?,?,?,1)',(ident,actor['id'],test_id,'preparing',time.time(),test['questions'],len(json.loads(test['questions'])),self.digest(lease),time.time()+4*3600))
            return ident,lease

    def _lease(self,lease):
        row=self.con.execute('SELECT a.*,u.name,u.active,t.title,t.seconds FROM attempts a JOIN users u ON u.id=a.user_id JOIN tests t ON t.id=a.test_id WHERE lease_hash=? AND lease_expiry>?',(self.digest(lease),time.time())).fetchone()
        if row is None or not row['active'] or row['status'] in ('finished','aborted','interrupted'):raise SchoolError('Попытка не активна / доступ запрещён')
        return row

    def claim(self,lease):
        with self.tx():
            row=self._lease(lease)
            if row['claimed']:raise SchoolError('Попытка уже открыта')
            self.con.execute("UPDATE attempts SET claimed=1,status='calibrating' WHERE id=?",(row['id'],))
            return dict(id=row['id'],name=row['name'],title=row['title'],seconds=row['seconds'],questions=[{k:q[k] for k in ('id','text','options')} for q in json.loads(row['questions'])])

    def bind_run(self,lease,directory,source='camera'):
        root=(self.path.parent/'sessions').resolve();directory=Path(directory).resolve()
        if not directory.is_relative_to(root):raise SchoolError('Неверный каталог CV-сессии')
        with self.tx():
            row=self._lease(lease);self.con.execute('UPDATE attempts SET run_dir=?,source=? WHERE id=?',(str(directory),source,row['id']))

    def start(self,lease):
        with self.tx():
            row=self._lease(lease)
            if row['status']!='calibrating' or not row['claimed']:raise SchoolError('Перед тестом нужна калибровка')
            self.con.execute("UPDATE attempts SET status='running',started=? WHERE id=?",(time.time(),row['id']))

    def finish(self,lease,answers):
        if isinstance(answers,str):
            if len(answers)>32768:raise SchoolError('Ответ слишком большой')
            try:answers=json.loads(answers)
            except ValueError as exc:raise SchoolError('Неверный формат ответов') from exc
        with self.tx():
            row=self._lease(lease)
            if row['status']!='running':raise SchoolError('Тест не начался')
            questions={q['id']:q for q in json.loads(row['questions'])}
            if not isinstance(answers,dict) or set(answers)-questions.keys() or any(type(v)!=int or not 0<=v<len(questions[k]['options']) for k,v in answers.items()):raise SchoolError('Неверные ответы')
            score=sum(answers.get(k)==q['correct'] for k,q in questions.items())
            self.con.execute("UPDATE attempts SET status='finished',ended=?,answers=?,score=? WHERE id=?",(time.time(),json.dumps(answers),score,row['id']))
            return score,row['total']

    def attempts(self,token):
        actor=self.actor(token)
        with self.lock:
            rows=self.con.execute('SELECT a.id,u.name,t.title,a.status,a.created,a.started,a.ended,a.score,a.total,a.run_dir,a.source FROM attempts a JOIN users u ON a.user_id=u.id JOIN tests t ON a.test_id=t.id'+(' WHERE u.id=?' if actor['role']=='student' else '')+' ORDER BY a.created DESC LIMIT 200', (actor['id'],) if actor['role']=='student' else ()).fetchall()
        return [dict(r) for r in rows]

    def interrupt(self,token,ident):
        with self.tx():
            actor=self.actor(token)
            row=self.con.execute('SELECT * FROM attempts WHERE id=?',(ident,)).fetchone()
            if not row or actor['role']=='student' and row['user_id']!=actor['id']:raise SchoolError('Доступ запрещён')
            if row['status'] in ('preparing','calibrating','running'):
                self.con.execute("UPDATE attempts SET status='interrupted',ended=? WHERE id=?",(time.time(),ident))

    def guard_credentials(self):
        # Only used to configure existing Windows guards; never returned through UI/student APIs.
        with self.lock:row=self.con.execute("SELECT salt,hash,iterations FROM users WHERE role='admin' AND active=1 ORDER BY rowid LIMIT 1").fetchone()
        if not row:raise SchoolError('Нет администратора')
        return dict(salt_hex=row[0],hash_hex=row[1],iterations=row[2])

    def privileged_password(self,password):
        with self.lock:rows=self.con.execute("SELECT salt,hash,iterations FROM users WHERE role IN ('admin','examiner') AND active=1").fetchall()
        return any(hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(r[0]),r[2]).hex(),r[1]) for r in rows)

    def inspection(self,token,ident):
        self.actor(token,('admin','examiner'))
        with self.lock:row=self.con.execute('SELECT run_dir FROM attempts WHERE id=?',(ident,)).fetchone()
        if not row:raise SchoolError('Попытка не найдена')
        if not row[0]:return dict(events=[],episodes=[],clips=[])
        root=(self.path.parent/'sessions').resolve();directory=Path(row[0]).resolve()
        if not directory.is_relative_to(root):raise SchoolError('Неверный путь к записям')
        db=directory/'session.db'
        if not db.exists():return dict(events=[],episodes=[],clips=[])
        con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.2);con.row_factory=sqlite3.Row
        try:
            return dict(events=[dict(r) for r in con.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')],episodes=[dict(r) for r in con.execute('SELECT * FROM episodes ORDER BY started DESC LIMIT 100')],clips=[dict(r) for r in con.execute('SELECT * FROM clips ORDER BY path DESC LIMIT 100')],directory=str(directory))
        finally:con.close()

    def archive_evidence(self,lease):
        # Called after CV writers stop; a closed quiz still permits this one archive step.
        with self.lock:row=self.con.execute('SELECT id,run_dir FROM attempts WHERE lease_hash=?',(self.digest(lease),)).fetchone()
        if not row or not row['run_dir']:return
        db=Path(row['run_dir'])/'session.db'
        con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=1);con.row_factory=sqlite3.Row
        try:
            with self.tx():
                for table,key in (('events','id'),('episodes','ident'),('clips','path')):
                    for item in con.execute('SELECT * FROM '+table):self.con.execute('INSERT OR REPLACE INTO evidence VALUES(?,?,?,?)',(row['id'],table,str(item[key]),json.dumps(dict(item),ensure_ascii=False)))
        finally:con.close()

    def close(self):
        with self.lock:self.con.close()
