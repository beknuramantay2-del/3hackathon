"""Local role-specific desktop hub. One exam client, not a distributed monitor."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
import yaml
from PyQt6.QtCore import Qt,QTimer
from PyQt6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QTabWidget,
 QTableWidget,QTableWidgetItem,QHeaderView,QDialog,QFormLayout,QComboBox,QPlainTextEdit,QSpinBox,QCheckBox,QMessageBox,QListWidget)
from PyQt6.QtGui import QPixmap
from .database import SchoolDB,SchoolError
from ..ui.clip_viewer import ClipViewer

STATUS={'preparing':'Подготовка','calibrating':'Калибровка','running':'Экзамен','finished':'Завершён','interrupted':'Прерван','aborted':'Отменён'}
ROLES={'student':'Ученик','examiner':'Экзаменатор','admin':'Администратор'}
HELP='''Перед экзаменом\n1. Установите веса/зависимости заранее. Для защиты нужны Windows и запуск с правами администратора. Preview не защищает экзамен.\n2. Создайте учеников или разрешите самостоятельную регистрацию. Опубликуйте собственный тест.\n3. Ученик входит в свой аккаунт, выбирает тест, подтверждает локальную запись.\n4. Обязательная калибровка 30 с: отдельно пять поворотов головы и пять направлений глаз. Неполная настройка не открывает ответы.\n\nВо время и после экзамена\nТелефон — красный сигнал после первого надёжного результата. Взгляд/голова: 3 с жёлтый, 5 с красный; короткие отводы записываются без предупреждения. Сигналы не доказывают списывание. В «Попытках» откройте эпизоды и готовые фрагменты; последнее изображение — снимок события, не live-камера/идентификация лица.\nНа одном компьютере одна активная попытка. Админ-панель скрыта, пока ученик проходит тест; удалённого наблюдения за классом здесь нет. Выход экзаменатора Ctrl+Shift+F12; пароль активного администратора/экзаменатора. После сбоя найдите попытку и завершите её перед новым запуском.\n\nДанные и ограничения\nОбщая SQLite data/school.db: аккаунты, тесты, попытки, ответы и архив событий. Сами ролики и per-run SQLite — data/sessions. Пароли PBKDF2, без дефолтных учётных записей. Права UI/сервиса не защищают SQLite от человека с доступом к файлам/кодам под той же учётной записью ОС.\nШкольное использование требует согласия, прав ОС и сроков удаления. Реальная точность глаз/головы/частично скрытого телефона и Windows-блокировки всё ещё требуют отдельной приёмки. PHONE_AIMED — эвристика, не доказанное наведение объектива. Баллы теста — ответы, не «рейтинг честности».'''


def table(headers):
    w=QTableWidget(0,len(headers));w.setHorizontalHeaderLabels(headers);w.verticalHeader().hide()
    w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);w.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    w.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);w.setSelectionMode(QTableWidget.SelectionMode.SingleSelection);return w


def fill(widget,rows):
    widget.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for j,val in enumerate(row):widget.setItem(i,j,QTableWidgetItem(str(val)))

class AccountDialog(QDialog):
    def __init__(self,parent=None,roles=False):
        super().__init__(parent);self.setWindowTitle('Учётная запись');self.resize(480,340)
        f=QFormLayout(self);self.login=QLineEdit();self.login.setMaxLength(64);self.name=QLineEdit();self.name.setMaxLength(120)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.EchoMode.Password);self.password.setMaxLength(256)
        f.addRow('Логин',self.login);f.addRow('Полное имя',self.name);f.addRow('Пароль (от 10 символов)',self.password)
        self.role=QComboBox();self.role.addItems(list(ROLES.values()))
        if roles:f.addRow('Роль',self.role)
        self.message=QLabel('Без демонстрационных паролей.');self.message.setWordWrap(True);f.addRow(self.message)
        save=QPushButton('Создать');save.clicked.connect(self.accept);cancel=QPushButton('Отмена');cancel.clicked.connect(self.reject);f.addRow(save,cancel)
    def values(self):return self.login.text(),self.name.text(),self.password.text(),list(ROLES)[self.role.currentIndex()]

class TestDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('Новый тест');self.resize(720,650);self.questions=[]
        f=QFormLayout(self);self.title=QLineEdit();self.minutes=QSpinBox();self.minutes.setRange(1,180);self.minutes.setValue(20)
        f.addRow('Название',self.title);f.addRow('Минут',self.minutes)
        self.text=QPlainTextEdit();self.text.setPlaceholderText('Текст вопроса');self.text.setMaximumHeight(100)
        self.options=QPlainTextEdit();self.options.setPlaceholderText('По одному варианту ответа в строке (2–8)');self.options.setMaximumHeight(130)
        self.correct=QSpinBox();self.correct.setRange(1,8);f.addRow('Вопрос',self.text);f.addRow('Варианты',self.options);f.addRow('Номер правильного ответа',self.correct)
        add=QPushButton('Добавить вопрос');add.clicked.connect(self.add);f.addRow(add)
        self.list=QListWidget();f.addRow('Вопросы теста',self.list)
        save=QPushButton('Опубликовать тест');save.clicked.connect(self.accept);cancel=QPushButton('Отмена');cancel.clicked.connect(self.reject);f.addRow(save,cancel)
    def add(self):
        q=dict(text=self.text.toPlainText().strip(),options=[x.strip() for x in self.options.toPlainText().splitlines() if x.strip()],correct=self.correct.value()-1)
        if not q['text'] or not 2<=len(q['options'])<=8 or q['correct']>=len(q['options']):QMessageBox.warning(self,'Вопрос','Нужен текст, 2–8 вариантов и правильный номер');return
        self.questions.append(q);self.list.addItem(str(len(self.questions))+'. '+q['text']);self.text.clear();self.options.clear();self.correct.setValue(1)

class Inspector(QDialog):
    def __init__(self,db,token,ident,parent=None):
        super().__init__(parent);self.db,self.token,self.ident=db,token,ident;self.rows=[];self.directory=None
        self.setWindowTitle('Эпизоды · ручная проверка');self.resize(1000,760);lay=QVBoxLayout(self)
        note=QLabel('Жёлтый/красный — сигнал для проверки, не автоматическое обвинение. Изображение ниже — последний снимок события, не live-видео.');note.setWordWrap(True);lay.addWidget(note)
        self.episodes=table(['Событие','Длительность','Уровень','Завершено']);lay.addWidget(self.episodes,1)
        self.image=QLabel('Снимка события пока нет');self.image.setAlignment(Qt.AlignmentFlag.AlignCenter);self.image.setMinimumHeight(240);lay.addWidget(self.image)
        self.info=QLabel('');self.info.setWordWrap(True);lay.addWidget(self.info)
        footage=QPushButton('Открыть футаж выбранного эпизода');footage.clicked.connect(self.play);lay.addWidget(footage)
        close=QPushButton('Закрыть');close.clicked.connect(self.reject);lay.addWidget(close)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(2000);self.refresh()
    def refresh(self):
        try:
            data=self.db.inspection(self.token,self.ident);self.rows=data['episodes'];self.directory=data.get('directory')
            selected=self.episodes.currentRow();fill(self.episodes,[[e['kind'],f"{e['duration']:.1f} с",('Зелёный · запись','Жёлтый','Красный')[min(2,e['level'])],'Да' if e['closed'] else 'Нет'] for e in self.rows])
            if 0<=selected<len(self.rows):self.episodes.selectRow(selected)
            if data['events'] and self.directory:
                p=Path(data['events'][0]['screenshot']).resolve();root=(Path(self.directory)/'shots').resolve()
                if p.is_relative_to(root) and p.is_file():self.image.setPixmap(QPixmap(str(p)).scaled(640,300,Qt.AspectRatioMode.KeepAspectRatio))
            self.info.setText(f"Эпизодов в окне: {len(self.rows)} · готовых фрагментов: {len(data['clips'])}. Данные обновляются раз в 2 с.")
        except (SchoolError,OSError,sqlite_error()) as exc:self.info.setText(str(exc))
    def play(self):
        index=self.episodes.currentRow()
        if not 0<=index<len(self.rows) or not self.directory:return
        import sqlite3
        db=Path(self.directory)/'session.db';con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:clips=[(p,json.loads(m)) for p,m in con.execute('SELECT path,metadata FROM clips JOIN episode_clips USING(path) WHERE ident=? ORDER BY path',(self.rows[index]['ident'],))]
        finally:con.close()
        if clips:ClipViewer(clips,Path(self.directory)/'clips',self).exec()
        else:QMessageBox.information(self,'Футаж','Фрагмент ещё записывается или недоступен')
    def done(self,result):self.timer.stop();super().done(result)

def sqlite_error():
    import sqlite3
    return sqlite3.Error

class Hub(QWidget):
    def __init__(self,db,preview=False):
        super().__init__();self.db=db;self.preview=preview;self.token='';self.actor=None;self.proc=None;self.attempt_id=None;self.lease=None;self.seen=set()
        self.setWindowTitle('Локальный экзамен');self.resize(1120,780);self.root=QVBoxLayout(self);self.root.setContentsMargins(32,24,32,24);self.root.setSpacing(16)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(2000);self.login_screen()
    def clear(self):
        while self.root.count():
            item=self.root.takeAt(0)
            if item.widget():item.widget().deleteLater()
    def fail(self,exc):QMessageBox.warning(self,'Не выполнено',str(exc))
    def login_screen(self):
        self.clear();self.actor=None;self.root.addStretch(1)
        heading=QLabel('Локальный экзамен');heading.setObjectName('title');self.root.addWidget(heading)
        info=QLabel('Ученики · тесты · наблюдение\nВсе записи остаются на этом компьютере.'+('\nPREVIEW: защита компьютера выключена; не для реального экзамена.' if self.preview else ''));info.setWordWrap(True);self.root.addWidget(info)
        self.login=QLineEdit();self.login.setPlaceholderText('Логин');self.login.setMaxLength(64)
        self.password=QLineEdit();self.password.setPlaceholderText('Пароль');self.password.setEchoMode(QLineEdit.EchoMode.Password);self.password.setMaxLength(256)
        self.root.addWidget(self.login);self.root.addWidget(self.password)
        enter=QPushButton('Войти');enter.clicked.connect(self.sign_in);self.password.returnPressed.connect(self.sign_in);self.root.addWidget(enter)
        register=QPushButton('Создать первого администратора' if self.db.needs_owner() else 'Регистрация ученика');register.setObjectName('secondary');register.clicked.connect(self.register);self.root.addWidget(register);self.root.addStretch(1)
    def register(self):
        owner=self.db.needs_owner();d=AccountDialog(self)
        if d.exec()!=QDialog.DialogCode.Accepted:return
        try:
            login,name,password,_=d.values()
            (self.db.setup_owner if owner else self.db.register_student)(login,name,password)
            self.login.setText(login);self.password.clear();self.login_screen()
        except SchoolError as exc:self.fail(exc)
    def sign_in(self):
        try:
            self.token=self.db.login(self.login.text(),self.password.text());self.password.clear();self.actor=self.db.actor(self.token);self.dashboard()
        except SchoolError as exc:self.fail(exc)
    def sign_out(self):
        if self.token:self.db.logout(self.token)
        self.token='';self.login_screen()
    def dashboard(self):
        self.clear();title=QLabel(self.actor['name']+' · '+ROLES[self.actor['role']]);title.setObjectName('title');self.root.addWidget(title)
        out=QPushButton('Выйти из аккаунта');out.setObjectName('secondary');out.clicked.connect(self.sign_out);self.root.addWidget(out)
        self.tabs=QTabWidget();self.root.addWidget(self.tabs,1)
        tests_page=QWidget();tl=QVBoxLayout(tests_page);self.tests_table=table(['Тест','Минут','Вопросов']);tl.addWidget(self.tests_table)
        button=QPushButton('Начать выбранный тест' if self.actor['role']=='student' else 'Создать тест');button.clicked.connect(self.begin if self.actor['role']=='student' else self.create_test);tl.addWidget(button)
        if self.actor['role']=='student':
            self.consent=QCheckBox('Согласен на локальную запись камеры и эпизодов во время экзамена');tl.addWidget(self.consent)
        self.tabs.addTab(tests_page,'Тесты')
        attempt_page=QWidget();al=QVBoxLayout(attempt_page);self.attempts_table=table(['Ученик','Тест','Статус','Правильных / всего','Источник']);al.addWidget(self.attempts_table)
        if self.actor['role']!='student':
            inspect=QPushButton('Открыть события и футаж');inspect.clicked.connect(self.inspect);al.addWidget(inspect)
            interrupted=QPushButton('Закрыть зависшую попытку');interrupted.setObjectName('secondary');interrupted.clicked.connect(self.interrupt);al.addWidget(interrupted)
        self.tabs.addTab(attempt_page,'Попытки')
        if self.actor['role']!='student':
            users_page=QWidget();ul=QVBoxLayout(users_page);self.users_table=table(['Имя','Логин','Роль','Доступ']);ul.addWidget(self.users_table)
            if self.actor['role']=='admin':
                add=QPushButton('Создать учётную запись');add.clicked.connect(self.create_user);ul.addWidget(add)
                toggle=QPushButton('Включить / отключить выбранного');toggle.setObjectName('secondary');toggle.clicked.connect(self.toggle_user);ul.addWidget(toggle)
            self.tabs.addTab(users_page,'Ученики и роли')
            help_page=QWidget();hl=QVBoxLayout(help_page);help_text=QPlainTextEdit(HELP);help_text.setReadOnly(True);hl.addWidget(help_text);self.tabs.addTab(help_page,'Инструкция')
        self.status=QLabel('');self.status.setWordWrap(True);self.root.addWidget(self.status);self.refresh()
    def refresh(self):
        if not self.actor:return
        self.test_rows=self.db.tests(self.token);fill(self.tests_table,[[r['title'],r['seconds']//60,r['count']] for r in self.test_rows])
        self.attempt_rows=self.db.attempts(self.token);fill(self.attempts_table,[[r['name'],r['title'],STATUS.get(r['status'],r['status']),f"{r['score']} / {r['total']}" if r['score'] is not None else '—',r['source'] or 'Не измерено'] for r in self.attempt_rows])
        if self.actor['role']!='student':
            self.user_rows=self.db.users(self.token);fill(self.users_table,[[r['name'],r['login'],ROLES[r['role']],'Включён' if r['active'] else 'Выключен'] for r in self.user_rows])
        self.status.setText('PREVIEW · без OS-защиты' if self.preview else 'Локально · один экзамен одновременно. CV требует реальной приёмки.')
    def create_user(self):
        d=AccountDialog(self,roles=True)
        if d.exec()==QDialog.DialogCode.Accepted:
            try:self.db.create_user(self.token,*d.values());self.refresh()
            except SchoolError as exc:self.fail(exc)
    def toggle_user(self):
        n=self.users_table.currentRow()
        if 0<=n<len(self.user_rows):
            r=self.user_rows[n]
            try:self.db.set_active(self.token,r['id'],not r['active']);self.refresh()
            except SchoolError as exc:self.fail(exc)
    def create_test(self):
        d=TestDialog(self)
        if d.exec()==QDialog.DialogCode.Accepted:
            try:self.db.create_test(self.token,d.title.text(),d.minutes.value()*60,d.questions);self.refresh()
            except SchoolError as exc:self.fail(exc)
    def inspect(self):
        n=self.attempts_table.currentRow()
        if 0<=n<len(self.attempt_rows):Inspector(self.db,self.token,self.attempt_rows[n]['id'],self).exec()
    def interrupt(self):
        n=self.attempts_table.currentRow()
        if 0<=n<len(self.attempt_rows) and QMessageBox.question(self,'Закрыть попытку','Только после фактического завершения процесса экзамена. Закрыть?')==QMessageBox.StandardButton.Yes:
            try:self.db.interrupt(self.token,self.attempt_rows[n]['id']);self.refresh()
            except SchoolError as exc:self.fail(exc)
    def begin(self):
        n=self.tests_table.currentRow()
        if not 0<=n<len(self.test_rows):return
        if not self.preview:
            import ctypes
            if os.name!='nt' or not ctypes.windll.shell32.IsUserAnAdmin():self.fail('Защищённый экзамен требует Windows/admin. Preview — отдельный незащищённый режим проверки.');return
        try:
            self.attempt_id,self.lease=self.db.new_attempt(self.token,self.test_rows[n]['id'],self.consent.isChecked())
            from .exam import exam_html
            config=yaml.safe_load(Path('proctor/config.yaml').read_text());config['profile']='dev' if self.preview else 'exam'
            config['guard']['examiner']=self.db.guard_credentials();config['store']['db']=str(self.db.path.parent/'session.db')
            directory=self.db.path.parent/'launch'/self.attempt_id;directory.mkdir(parents=True,exist_ok=True)
            # Child fills the real HTML from its one-use attempt snapshot after claiming the lease.
            config['test']['test_url']=str(directory/'exam.html');config['hash_chain']['salt_hex']=''
            config_path=directory/'config.yaml';config_path.write_text(yaml.safe_dump(config,allow_unicode=True));os.chmod(config_path,0o600)
            env=dict(os.environ,PROCTOR_SCHOOL_DB=str(self.db.path),PROCTOR_ATTEMPT_LEASE=self.lease)
            command=[sys.executable,'-m','proctor.main','--embedded-test','--config',str(config_path)]
            if self.preview:command+=['--no-guard']
            self.proc=subprocess.Popen(command,env=env);self.hide()
        except (SchoolError,OSError) as exc:self.fail(exc)
    def poll(self):
        try:
            if self.proc:
                if self.proc.poll() is None:return
                # Preserve interrupted state after startup failure/crash; never invent completion/score.
                with self.db.tx():self.db.con.execute("UPDATE attempts SET status='interrupted',ended=? WHERE id=? AND status IN ('preparing','calibrating','running')",(time.time(),self.attempt_id))
                self.proc=None;self.show();self.sign_out();return
            if self.actor:
                indexes=(self.tests_table.currentRow(),self.attempts_table.currentRow(),self.users_table.currentRow() if self.actor['role']!='student' else -1)
                self.refresh()
                for widget,index in zip((self.tests_table,self.attempts_table,getattr(self,'users_table',None)),indexes):
                    if widget and index>=0 and index<widget.rowCount():widget.selectRow(index)
        except (SchoolError,sqlite_error()) as exc:
            if self.actor:self.status.setText(str(exc))
    def closeEvent(self,event):
        if self.proc and self.proc.poll() is None:event.ignore();return
        if self.token:self.db.logout(self.token)
        event.accept()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--preview',action='store_true',help='Unprotected local workflow check, not a real exam');args=ap.parse_args()
    os.chdir(Path(__file__).resolve().parents[2]);app=QApplication(sys.argv);app.setStyleSheet(Path('proctor/ui/style.qss').read_text())
    db=SchoolDB();w=Hub(db,args.preview);w.show()
    try:return app.exec()
    finally:db.close()
