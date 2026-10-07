import argparse, json, os, subprocess, sys, time
from pathlib import Path
import yaml
from PyQt6.QtCore import Qt, QTimer, QLockFile
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QFormLayout,
    QComboBox,
    QPlainTextEdit,
    QCheckBox,
    QMessageBox,
)
from PyQt6.QtGui import QPixmap
from .database import SchoolDB, SchoolError
from ..ui.clip_viewer import ClipViewer

STATUS = {
    "preparing": "Подготовка",
    "calibrating": "Калибровка",
    "running": "Экзамен",
    "finished": "Завершён",
    "interrupted": "Прерван",
    "aborted": "Отменён",
}
ROLES = {"student": "Ученик", "examiner": "Экзаменатор", "admin": "Администратор"}
HELP = """Наблюдение за учениками
Панель показывает имена, статус текущей сессии, кадр камеры и события. Здесь нет редактора тестов. Подготовку аккаунтов и импорт вопросов выполняют отдельно: python -m proctor.school.manage --help.

Подключение
Запустите панель: python -m proctor.school --monitor
В другом процессе на этом же компьютере: python -m proctor.school --student
Оба процесса используют одну data/school.db; с --db укажите один и тот же абсолютный путь. Администратор/экзаменатор входит в панель, ученик — в клиент. Панель не запускает модели и не открывает камеру повторно. При отсутствии связи кадр помечается несвежим, а не выдаётся за live. Одна активная попытка на компьютере; сети класса здесь нет.

Порядок
До экзамена установите веса, создайте учётные записи и импортируйте вопросы. Ученик выбирает тест, подтверждает запись и проходит обязательные 30 секунд настройки головы/глаз. Вопросы открываются только после успешной настройки.
Телефон: первый надёжный результат — красный сигнал. Голова/глаза: 3 с — жёлтый, 5 с — красный; короткие отводы сохраняются без тревоги. Сигнал требует ручной проверки, не доказывает списывание.
Выберите сессию и откройте события/готовый футаж. Свежий кадр обновляется с частотой до 2 Гц, панель опрашивает данные раз в секунду. Это не непрерывная видеозапись.

Защита и хранение
Для защищённого экзамена нужны Windows/admin. --preview проверяет интерфейс и данные без OS-защиты. Не переключайтесь на панель из защищённого сеанса ученика: потеря фокуса должна фиксироваться. Для отдельного просмотра используйте проверочный режим или кабинет экзаменатора по паролю внутри сеанса.
Выход Ctrl+Shift+F12 — по паролю экзаменатора. После сбоя закрывайте зависшую попытку только когда процесс действительно остановлен.
Записи остаются на компьютере: общая SQLite и data/sessions. Установите права ОС, согласие, сроки удаления и резервное копирование. Один OS-пользователь с доступом к файлам может читать/менять их независимо от роли UI. PHONE_AIMED — эвристика; точность CV и блокировки Windows требуют проверки на целевой машине."""


def table(headers):
    w = QTableWidget(0, len(headers))
    w.setHorizontalHeaderLabels(headers)
    w.verticalHeader().hide()
    w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    w.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    w.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    w.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    return w


def fill(widget, rows):
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            widget.setItem(i, j, QTableWidgetItem(str(val)))
    widget.resizeRowsToContents()


def show_snapshot(image, label, data):
    image.clear()
    if not data["live"]:
        image.setText("Нет свежего кадра")
        label.setText(data["reason"])
        return
    pixmap = QPixmap()
    if not pixmap.loadFromData(data["image"], "JPG"):
        image.setText("Кадр не читается")
        label.setText("Проверьте приложение ученика")
        return
    image.setPixmap(
        pixmap.scaled(
            480,
            360,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    s = data["status"]
    direction = {
        "CENTER": "Прямо",
        "LEFT": "Влево",
        "RIGHT": "Вправо",
        "UP": "Вверх",
        "DOWN": "Вниз",
        "UNKNOWN": "Не измерено",
    }
    text = (
        "Голова: "
        + direction.get(s.get("head"), "Не измерено")
        + " · глаза: "
        + direction.get(s.get("gaze"), "Не измерено")
    )
    text += (
        " · телефон: "
        + str(s.get("phone", "Не измерено"))
        + " · лиц: "
        + str(s.get("faces", "Не измерено"))
    )
    text += (
        "\n"
        + str(s.get("phase", ""))
        + " · "
        + (
            "Предварительно, без направленных тревог"
            if not s.get("calibrated")
            else ("Зелёный", "Жёлтый · проверка", "Красный · проверка")[
                min(2, max(0, int(s.get("level", 0))))
            ]
        )
    )
    text += f" · возраст кадра {data['age']:.1f} с"
    if s.get("error"):
        text += "\n" + str(s["error"])
    label.setText(text)


class AccountDialog(QDialog):
    def __init__(self, parent=None, roles=False):
        super().__init__(parent)
        self.setWindowTitle("Учётная запись")
        self.resize(480, 340)
        f = QFormLayout(self)
        self.login = QLineEdit()
        self.login.setMaxLength(64)
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(256)
        f.addRow("Логин", self.login)
        f.addRow("Полное имя", self.name)
        f.addRow("Пароль (от 10 символов)", self.password)
        self.role = QComboBox()
        self.role.addItems(list(ROLES.values()))
        if roles:
            f.addRow("Роль", self.role)
        self.message = QLabel("Без демонстрационных паролей.")
        self.message.setWordWrap(True)
        f.addRow(self.message)
        save = QPushButton("Создать")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        f.addRow(save, cancel)

    def values(self):
        return (
            self.login.text(),
            self.name.text(),
            self.password.text(),
            list(ROLES)[self.role.currentIndex()],
        )


class Inspector(QDialog):
    def __init__(self, db, token, ident, parent=None):
        super().__init__(parent)
        self.db, self.token, self.ident = db, token, ident
        self.rows = []
        self.directory = None
        self.setWindowTitle("Эпизоды · ручная проверка")
        self.resize(1000, 760)
        lay = QVBoxLayout(self)
        note = QLabel(
            "Жёлтый/красный — сигнал для ручной проверки. Камера обновляется до 2 Гц; для завершённых сессий доступны снимки событий и футаж."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        self.episodes = table(["Событие", "Длительность", "Уровень", "Завершено"])
        lay.addWidget(self.episodes, 1)
        self.image = QLabel("Снимка события пока нет")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(240)
        lay.addWidget(self.image)
        self.connection = QLabel("")
        self.connection.setWordWrap(True)
        self.connection.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.connection)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        footage = QPushButton("Открыть футаж выбранного эпизода")
        footage.clicked.connect(self.play)
        lay.addWidget(footage)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        lay.addWidget(close)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        try:
            data = self.db.inspection(self.token, self.ident)
            self.rows = data["episodes"]
            self.directory = data.get("directory")
            current = {(e["ident"], e["level"]) for e in self.rows if e["level"] > 0}
            if current - getattr(self, "seen", set()):
                QApplication.beep()
            self.seen = current
            selected = self.episodes.currentRow()
            fill(
                self.episodes,
                [
                    [
                        e["kind"],
                        f"{e['duration']:.1f} с",
                        ("Зелёный · запись", "Жёлтый", "Красный")[min(2, e["level"])],
                        "Да" if e["closed"] else "Нет",
                    ]
                    for e in self.rows
                ],
            )
            if 0 <= selected < len(self.rows):
                self.episodes.selectRow(selected)
            live = self.db.live_snapshot(self.token, self.ident)
            show_snapshot(self.image, self.connection, live)
            if not live["live"] and data["events"] and self.directory:
                p = Path(data["events"][0]["screenshot"]).resolve()
                root = (Path(self.directory) / "shots").resolve()
                if p.is_relative_to(root) and p.is_file():
                    self.image.setPixmap(
                        QPixmap(str(p)).scaled(
                            640, 300, Qt.AspectRatioMode.KeepAspectRatio
                        )
                    )
                    self.connection.setText(
                        live["reason"] + " · ниже сохранённый снимок события, не live"
                    )
            self.info.setText(
                f"Эпизодов в окне: {len(self.rows)} · красных: {sum(e['level']==2 for e in self.rows)} · жёлтых: {sum(e['level']==1 for e in self.rows)} · готовых фрагментов: {len(data['clips'])}. Данные обновляются раз в секунду."
            )
        except (SchoolError, OSError, sqlite_error()) as exc:
            self.rows = []
            self.episodes.setRowCount(0)
            self.image.clear()
            self.info.setText(str(exc))

    def play(self):
        try:
            self.db.actor(self.token, ("admin", "examiner"))
        except SchoolError as exc:
            self.info.setText(str(exc))
            return
        index = self.episodes.currentRow()
        if not 0 <= index < len(self.rows) or not self.directory:
            return
        import sqlite3

        db = Path(self.directory) / "session.db"
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        try:
            clips = [
                (p, json.loads(m))
                for p, m in con.execute(
                    "SELECT path,metadata FROM clips JOIN episode_clips USING(path) WHERE ident=? ORDER BY path",
                    (self.rows[index]["ident"],),
                )
            ]
        finally:
            con.close()
        if clips:
            ClipViewer(clips, Path(self.directory) / "clips", self).exec()
        else:
            QMessageBox.information(
                self, "Футаж", "Фрагмент ещё записывается или недоступен"
            )

    def done(self, result):
        self.timer.stop()
        super().done(result)


def sqlite_error():
    import sqlite3

    return sqlite3.Error


class Hub(QWidget):
    def __init__(self, db, preview=False, mode="auto", perf="auto"):
        super().__init__()
        self.db = db
        self.preview = preview
        self.mode = mode
        self.perf = perf
        self.token = ""
        self.actor = None
        self.proc = None
        self.attempt_id = None
        self.lease = None
        self.seen = set()
        self.setWindowTitle("Локальный экзамен")
        self.resize(1120, 780)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(32, 24, 32, 24)
        self.root.setSpacing(16)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.login_screen()

    def clear(self):
        while self.root.count():
            item = self.root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def fail(self, exc):
        QMessageBox.warning(self, "Не выполнено", str(exc))

    def login_screen(self):
        self.clear()
        self.actor = None
        self.root.addStretch(1)
        card = QWidget()
        card.setObjectName("panel")
        card.setMaximumWidth(560)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(16)
        heading = QLabel("Локальный экзамен")
        heading.setObjectName("title")
        box.addWidget(heading)
        info = QLabel(
            "Ученики · тесты · наблюдение\nВсе записи остаются на этом компьютере."
            + (
                "\nPREVIEW: защита компьютера выключена; не для реального экзамена."
                if self.preview
                else ""
            )
        )
        info.setWordWrap(True)
        box.addWidget(info)
        self.login = QLineEdit()
        self.login.setPlaceholderText("Логин")
        self.login.setMaxLength(64)
        self.login.setMinimumHeight(44)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Пароль")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(256)
        self.password.setMinimumHeight(44)
        box.addWidget(QLabel("Логин"))
        box.addWidget(self.login)
        box.addWidget(QLabel("Пароль"))
        box.addWidget(self.password)
        enter = QPushButton("Войти")
        enter.clicked.connect(self.sign_in)
        self.password.returnPressed.connect(self.sign_in)
        box.addWidget(enter)
        register = QPushButton(
            "Создать первого администратора"
            if self.db.needs_owner()
            else "Регистрация ученика"
        )
        register.setObjectName("secondary")
        register.clicked.connect(self.register)
        box.addWidget(register)
        register.setVisible(
            self.mode != "monitor"
            and (self.mode != "student" or not self.db.needs_owner())
        )
        self.root.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        self.root.addStretch(1)

    def register(self):
        owner = self.db.needs_owner()
        d = AccountDialog(self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            login, name, password, _ = d.values()
            (self.db.setup_owner if owner else self.db.register_student)(
                login, name, password
            )
            self.login_screen()
            self.login.setText(login)
        except SchoolError as exc:
            self.fail(exc)

    def sign_in(self):
        try:
            self.token = self.db.login(self.login.text(), self.password.text())
            self.password.clear()
            self.actor = self.db.actor(self.token)
            if (
                self.mode == "monitor"
                and self.actor["role"] == "student"
                or self.mode == "student"
                and self.actor["role"] != "student"
            ):
                self.db.logout(self.token)
                self.token = ""
                self.actor = None
                raise SchoolError(
                    "Для панели нужен экзаменатор/администратор, для клиента — аккаунт ученика"
                )
            self.dashboard()
        except SchoolError as exc:
            self.fail(exc)

    def sign_out(self):
        if self.token:
            self.db.logout(self.token)
        self.token = ""
        self.login_screen()

    def dashboard(self):
        self.clear()
        title = QLabel(self.actor["name"] + " · " + ROLES[self.actor["role"]])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("title")
        self.root.addWidget(title)
        out = QPushButton("Выйти из аккаунта")
        out.setObjectName("secondary")
        out.clicked.connect(self.sign_out)
        self.root.addWidget(out)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.tests_table = None
        if self.actor["role"] == "student":
            tests_page = QWidget()
            tl = QVBoxLayout(tests_page)
            self.tests_table = table(["Тест", "Минут", "Вопросов"])
            tl.addWidget(self.tests_table)
            button = QPushButton("Начать выбранный тест")
            button.clicked.connect(self.begin)
            tl.addWidget(button)
            self.consent = QCheckBox(
                "Согласен на локальную запись камеры и эпизодов во время экзамена"
            )
            tl.addWidget(self.consent)
            self.tabs.addTab(tests_page, "Тесты")
        attempt_page = QWidget()
        al = QVBoxLayout(attempt_page)
        self.attempts_table = table(
            ["Ученик", "Тест", "Статус", "Баллы", "Источник"]
            + (["Жёлтых", "Красных"] if self.actor["role"] != "student" else [])
        )
        al.addWidget(self.attempts_table)
        if self.actor["role"] != "student":
            self.live_image = QLabel("Выберите сессию ученика")
            self.live_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.live_image.setMinimumHeight(220)
            al.addWidget(self.live_image)
            self.live_status = QLabel("")
            self.live_status.setWordWrap(True)
            self.live_status.setTextFormat(Qt.TextFormat.PlainText)
            al.addWidget(self.live_status)
            self.attempts_table.itemSelectionChanged.connect(self.show_live)
            inspect = QPushButton("Открыть события и футаж")
            inspect.clicked.connect(self.inspect)
            al.addWidget(inspect)
            interrupted = QPushButton("Закрыть зависшую попытку")
            interrupted.setObjectName("secondary")
            interrupted.clicked.connect(self.interrupt)
            al.addWidget(interrupted)
        self.tabs.addTab(
            attempt_page,
            "Мои результаты" if self.actor["role"] == "student" else "Наблюдение",
        )
        if self.actor["role"] != "student":
            users_page = QWidget()
            ul = QVBoxLayout(users_page)
            self.users_table = table(["Имя", "Логин", "Роль", "Доступ"])
            ul.addWidget(self.users_table)
            self.tabs.addTab(users_page, "Ученики и роли")
            help_page = QWidget()
            hl = QVBoxLayout(help_page)
            help_text = QPlainTextEdit(HELP)
            help_text.setReadOnly(True)
            hl.addWidget(help_text)
            self.tabs.addTab(help_page, "Инструкция")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.root.addWidget(self.status)
        self.refresh()

    def refresh(self):
        if not self.actor:
            return
        if self.tests_table is not None:
            self.test_rows = self.db.tests(self.token)
            fill(
                self.tests_table,
                [[r["title"], r["seconds"] // 60, r["count"]] for r in self.test_rows],
            )
        self.attempt_rows = self.db.attempts(self.token)
        if self.actor["role"] != "student":
            for r in self.attempt_rows:
                if r["status"] in ("calibrating", "running") and r["run_dir"]:
                    try:
                        live = self.db.inspection(self.token, r["id"])["episodes"]
                        r["yellow"] = sum(e["level"] == 1 for e in live)
                        r["red"] = sum(e["level"] == 2 for e in live)
                    except (SchoolError, sqlite_error()):
                        pass
        self.attempts_table.blockSignals(True)
        fill(
            self.attempts_table,
            [
                [
                    r["name"],
                    r["title"],
                    STATUS.get(r["status"], r["status"]),
                    f"{r['score']} / {r['total']}" if r["score"] is not None else "—",
                    r["source"] or "Не измерено",
                ]
                + ([r["yellow"], r["red"]] if self.actor["role"] != "student" else [])
                for r in self.attempt_rows
            ],
        )
        self.attempts_table.blockSignals(False)
        if self.actor["role"] != "student":
            self.user_rows = self.db.users(self.token)
            fill(
                self.users_table,
                [
                    [
                        r["name"],
                        r["login"],
                        ROLES[r["role"]],
                        "Включён" if r["active"] else "Выключен",
                    ]
                    for r in self.user_rows
                ],
            )
        self.status.setText(
            ("PREVIEW · без OS-защиты · " if self.preview else "")
            + "База: "
            + str(self.db.path)
            + " · один ученический сеанс одновременно"
        )

    def show_live(self):
        if not self.actor or self.actor["role"] == "student":
            return
        n = self.attempts_table.currentRow()
        if not 0 <= n < len(self.attempt_rows):
            self.live_image.clear()
            self.live_image.setText("Выберите сессию ученика")
            self.live_status.clear()
            return
        try:
            data = self.db.live_snapshot(self.token, self.attempt_rows[n]["id"])
            show_snapshot(self.live_image, self.live_status, data)
        except (SchoolError, sqlite_error()) as exc:
            self.live_image.clear()
            self.live_status.setText(str(exc))

    def inspect(self):
        n = self.attempts_table.currentRow()
        if 0 <= n < len(self.attempt_rows):
            Inspector(self.db, self.token, self.attempt_rows[n]["id"], self).exec()

    def interrupt(self):
        n = self.attempts_table.currentRow()
        if (
            0 <= n < len(self.attempt_rows)
            and QMessageBox.question(
                self,
                "Закрыть попытку",
                "Только после фактического завершения процесса экзамена. Закрыть?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            try:
                self.db.interrupt(self.token, self.attempt_rows[n]["id"])
                self.refresh()
            except SchoolError as exc:
                self.fail(exc)

    def begin(self):
        n = self.tests_table.currentRow()
        if not 0 <= n < len(self.test_rows):
            return
        if not self.preview:
            import ctypes

            if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
                self.fail(
                    "Защищённый экзамен требует Windows/admin. Preview — отдельный незащищённый режим проверки."
                )
                return
        try:
            self.attempt_id, self.lease = self.db.new_attempt(
                self.token, self.test_rows[n]["id"], self.consent.isChecked()
            )
            from .exam import exam_html

            config = yaml.safe_load(Path("proctor/config.yaml").read_text())
            config["profile"] = "dev" if self.preview else "exam"
            config["guard"]["examiner"] = self.db.guard_credentials()
            config["guard"]["process_mode"] = "log" if self.preview else "close"
            config["store"]["db"] = str(self.db.path.parent / "session.db")
            directory = self.db.path.parent / "launch" / self.attempt_id
            directory.mkdir(parents=True, exist_ok=True)

            config["test"]["test_url"] = str(directory / "exam.html")
            config["hash_chain"]["salt_hex"] = ""
            config_path = directory / "config.yaml"
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True))
            os.chmod(config_path, 0o600)
            env = dict(
                os.environ,
                PROCTOR_SCHOOL_DB=str(self.db.path),
                PROCTOR_ATTEMPT_LEASE=self.lease,
            )
            command = [
                sys.executable,
                "-m",
                "proctor.main",
                "--embedded-test",
                "--config",
                str(config_path),
                "--perf",
                self.perf,
            ]
            if self.preview:
                command += ["--no-guard"]
            self.proc = subprocess.Popen(command, env=env)
            self.hide()
        except (SchoolError, OSError) as exc:
            if self.attempt_id:
                with self.db.tx():
                    self.db.con.execute(
                        "UPDATE attempts SET status='interrupted',ended=? WHERE id=? AND status='preparing'",
                        (time.time(), self.attempt_id),
                    )
            self.fail(exc)

    def poll(self):
        try:
            if self.proc:
                if self.proc.poll() is None:
                    return

                with self.db.tx():
                    self.db.con.execute(
                        "UPDATE attempts SET status='interrupted',ended=? WHERE id=? AND status IN ('preparing','calibrating','running')",
                        (time.time(), self.attempt_id),
                    )
                self.proc = None
                self.show()
                self.sign_out()
                return
            if self.actor:
                try:
                    self.actor = self.db.actor(self.token)
                except SchoolError:
                    self.sign_out()
                    return
                selected_attempt = (
                    self.attempt_rows[self.attempts_table.currentRow()]["id"]
                    if 0 <= self.attempts_table.currentRow() < len(self.attempt_rows)
                    else None
                )
                selected_test = (
                    self.test_rows[self.tests_table.currentRow()]["id"]
                    if self.tests_table is not None
                    and 0 <= self.tests_table.currentRow() < len(self.test_rows)
                    else None
                )
                self.refresh()
                for i, r in enumerate(self.attempt_rows):
                    if r["id"] == selected_attempt:
                        self.attempts_table.selectRow(i)
                        break
                if (
                    selected_attempt is None
                    and self.actor["role"] != "student"
                    and self.attempt_rows
                ):
                    self.attempts_table.selectRow(0)
                if self.tests_table is not None:
                    for i, r in enumerate(self.test_rows):
                        if r["id"] == selected_test:
                            self.tests_table.selectRow(i)
                            break
                self.show_live()
        except (SchoolError, sqlite_error()) as exc:
            if self.actor:
                self.status.setText(str(exc))

    def closeEvent(self, event):
        if self.proc and self.proc.poll() is None:
            event.ignore()
            return
        if self.token:
            self.db.logout(self.token)
        event.accept()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Проверка без OS-защиты, не защищённый экзамен",
    )
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument(
        "--monitor",
        action="store_true",
        help="Отдельная панель экзаменатора, без захвата камеры",
    )
    modes.add_argument("--student", action="store_true", help="Вход только для ученика")
    ap.add_argument(
        "--db",
        default="data/school.db",
        help="Общая локальная SQLite; одинаковый путь для обоих процессов",
    )
    ap.add_argument("--perf", choices=("auto", "weak", "balanced"), default="auto")
    args = ap.parse_args()
    database = Path(args.db).resolve()
    os.chdir(Path(__file__).resolve().parents[2])
    app = QApplication(sys.argv)
    app.setStyleSheet(Path("proctor/ui/style.qss").read_text())
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(
        str(database.with_suffix(".monitor.lock" if args.monitor else ".client.lock"))
    )
    if not lock.tryLock(100):
        raise SystemExit("Это окно уже открыто для выбранной базы")
    db = SchoolDB(database)
    w = Hub(
        db,
        args.preview,
        "monitor" if args.monitor else "student" if args.student else "auto",
        args.perf,
    )
    w.show()
    try:
        return app.exec()
    finally:
        db.close()
        lock.unlock()
