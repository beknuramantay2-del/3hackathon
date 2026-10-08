from PyQt6.QtCore import Qt, pyqtSignal, QRect
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGridLayout,
    QProgressBar,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTabWidget,
    QSplitter,
    QScrollArea,
)
from .screens import SidePanel

DIRECTIONS = {
    "CENTER": "Прямо",
    "LEFT": "Влево",
    "RIGHT": "Вправо",
    "UP": "Вверх",
    "DOWN": "Вниз",
    "UNKNOWN": "Не определяется",
}
ARROWS = {
    "CENTER": "●",
    "LEFT": "←",
    "RIGHT": "→",
    "UP": "↑",
    "DOWN": "↓",
    "UNKNOWN": "—",
}
CASES = (
    ("PHONE", "Телефон в кадре"),
    ("HAND", "Телефон в руке"),
    ("LIFT", "Подъём телефона"),
    ("RAISED", "Телефон у лица / экрана"),
    ("AIM", "Возможное наведение камеры"),
    ("HEAD", "Направление головы"),
    ("GAZE", "Направление глаз"),
    ("DOWN", "Длительный взгляд вниз"),
    ("SIDE", "Длительный взгляд в сторону"),
    ("PRESENCE", "Присутствие ученика"),
    ("MULTI", "Второе лицо"),
    ("ALT", "Alt+Tab"),
    ("COPY", "Копирование / вставка"),
    ("WIN", "Клавиша Win"),
    ("SHOT", "Снимок экрана"),
    ("TABS", "Переключение вкладок"),
    ("WINDOWS", "Посторонние окна"),
)


class VideoPreview(QLabel):

    def __init__(self):
        super().__init__("Ожидаем изображение с камеры…")
        self.setObjectName("camera")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(420, 300)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._source = None
        self.image_rect = QRect()

    def set_frame(self, frame):
        self._source = SidePanel.pixmap(frame, frame.shape[1], frame.shape[0])
        self._fit()

    def _fit(self):
        if self._source is None:
            return
        area = self.contentsRect().adjusted(1, 1, -1, -1)
        if area.width() < 1 or area.height() < 1:
            return
        pixmap = self._source.scaled(
            area.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.image_rect = QRect(
            area.x() + (area.width() - pixmap.width()) // 2,
            area.y() + (area.height() - pixmap.height()) // 2,
            pixmap.width(),
            pixmap.height(),
        )
        self.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class DirectionReadout(QFrame):

    def __init__(self, title):
        super().__init__()
        self.title = title
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.state = QLabel(title + ": — Не определяется")
        self.state.setObjectName("directionValue")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.note = QLabel(self)
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.note.hide()
        self.chips = {}

    def update_state(self, state):
        state = state if state in DIRECTIONS else "UNKNOWN"
        self.state.setText(self.title + ": " + ARROWS[state] + " " + DIRECTIONS[state])
        self.state.setToolTip(self.note.text())


class MonitorWindow(QWidget):
    calibrate = pyqtSignal(str)
    finish = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Прокторинг · Камера и наблюдение")
        self.resize(1280, 800)
        self.setMinimumSize(960, 640)
        self.locked = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Локальный прокторинг")
        title.setObjectName("title")
        heading.addWidget(title)
        header.addLayout(heading, 1)
        self.mode_label = QLabel("Просмотр CV · защита выключена")
        self.mode_label.setObjectName("modeBadge")
        self.session_clock = QLabel("00:00")
        self.session_clock.setObjectName("clock")
        header.addWidget(self.session_clock)
        outer.addLayout(header)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 10, 0)
        left.setSpacing(8)
        self.banner = QLabel("Подготовка камеры и локальных моделей…")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.camera = VideoPreview()
        left.addWidget(self.camera, 1)
        presence = QHBoxLayout()
        self.face_summary = QLabel("Лицо: ожидаем измерение")
        self.phone_summary = QLabel("Телефон: ожидаем измерение")
        for label in (self.face_summary, self.phone_summary):
            label.setObjectName("observationBadge")
            label.setWordWrap(True)
            presence.addWidget(label, 1)
        cards = QHBoxLayout()
        self.head_card = DirectionReadout("ГОЛОВА")
        self.gaze_card = DirectionReadout("ГЛАЗА")
        cards.addWidget(self.head_card)
        cards.addWidget(self.gaze_card)
        left.addLayout(cards)
        self.direction_labels = {
            (kind, d): card.chips[d]
            for kind, card in (("HEAD", self.head_card), ("GAZE", self.gaze_card))
            for d in card.chips
        }
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Нет активного удержания")
        left.addWidget(self.progress)
        self.calibration_note = QLabel(
            "Обязательная настройка: 15 с — голова, 15 с — глаза. Без всех направлений экзамен не готов. Футаж хранится локально."
        )
        self.calibration_note.setObjectName("muted")
        self.calibration_note.setWordWrap(True)
        self.neutral = QPushButton("Обновить центр · 2 с")
        self.neutral.setObjectName("secondary")
        self.five = QPushButton("Настроить голову и глаза · 30 с")
        self.neutral.clicked.connect(lambda: self.calibrate.emit("center"))
        self.five.clicked.connect(lambda: self.calibrate.emit("five"))
        header.insertWidget(header.count() - 1, self.five)
        split.addWidget(left_widget)
        right_widget = QWidget()
        right_widget.setMinimumWidth(300)
        right_widget.setMaximumWidth(350)
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(self.mode_label)
        right.addWidget(self.banner)
        self.signal_badge = QLabel("Калибровка обязательна")
        self.signal_badge.setObjectName("observationBadge")
        self.signal_badge.setWordWrap(True)
        right.addWidget(self.signal_badge)
        right.addLayout(presence)
        right.addWidget(self.calibration_note)
        self.tabs = QTabWidget()
        right.addWidget(self.tabs, 1)
        events = QWidget()
        el = QVBoxLayout(events)
        el.setContentsMargins(12, 16, 12, 12)
        el.setSpacing(12)
        hint = QLabel("События наблюдения")
        hint.setObjectName("sectionTitle")
        hint.setWordWrap(True)
        el.addWidget(hint)
        note = QLabel(
            "Каждый отвод записывается. 3 с — жёлтый, 5 с — красный. Телефон — сразу после надёжного обнаружения. Двойной клик — футаж."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        el.addWidget(note)
        self.feed = QListWidget()
        self.feed.setWordWrap(True)
        self.feed.setAccessibleName("Лента событий")
        el.addWidget(self.feed, 1)
        self.empty_feed = QLabel("Событий пока нет")
        self.empty_feed.setObjectName("muted")
        el.addWidget(self.empty_feed)
        self.tabs.addTab(events, "События")
        requirements = QWidget()
        rl = QVBoxLayout(requirements)
        rl.setContentsMargins(8, 12, 8, 8)
        info = QLabel(
            "Живые статусы всех пунктов кейса. Выключенная защита не считается работающей."
        )
        info.setWordWrap(True)
        info.setObjectName("muted")
        rl.addWidget(info)
        self.checklist = QTableWidget(len(CASES), 2)
        self.checklist.setHorizontalHeaderLabels(["Контроль", "Состояние"])
        self.checklist.verticalHeader().hide()
        self.checklist.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.checklist.setWordWrap(True)
        self.checklist.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.checklist.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.checklist.setAlternatingRowColors(True)
        self.rows = {}
        for i, (key, label) in enumerate(CASES):
            self.rows[key] = i
            self.checklist.setItem(i, 0, QTableWidgetItem(label))
            self.checklist.setItem(i, 1, QTableWidgetItem("Не измерено"))
        rl.addWidget(self.checklist)
        self.tabs.addTab(requirements, "Возможности")
        diagnostic = QWidget()
        dl = QVBoxLayout(diagnostic)
        dl.setContentsMargins(12, 16, 12, 12)
        dl.setSpacing(12)
        self.measurements = QLabel("Измерения появятся после первого результата")
        self.measurements.setWordWrap(True)
        self.measurements.setObjectName("diagnosticText")
        dl.addWidget(self.measurements)
        self.performance = QLabel("Частота и возраст измерений: —")
        self.performance.setWordWrap(True)
        self.performance.setObjectName("diagnosticText")
        dl.addWidget(self.performance)
        knobs = QGridLayout()
        knobs.setVerticalSpacing(8)
        self.phone_threshold = self.spin(0.15, 0.70, 0.05, 0.25, 2)
        self.gaze_hold = self.spin(0.5, 10, 0.5, 3, 1)
        self.down_hold = self.spin(1, 20, 0.5, 5, 1)
        self.yaw_threshold = self.spin(8, 45, 1, 18, 0)
        self.pitch_threshold = self.spin(8, 35, 1, 12, 0)
        for i, (label, widget) in enumerate(
            (
                ("Порог уверенного телефона", self.phone_threshold),
                ("Жёлтый сигнал, с", self.gaze_hold),
                ("Красный сигнал, с", self.down_hold),
                ("Поворот головы, °", self.yaw_threshold),
                ("Наклон головы, °", self.pitch_threshold),
            )
        ):
            l = QLabel(label)
            l.setWordWrap(True)
            knobs.addWidget(l, i, 0)
            knobs.addWidget(widget, i, 1)
        dl.addLayout(knobs)
        self.neutral.hide()
        self.neutral.setEnabled(False)
        explanation = QLabel(
            "Confidence — оценка модели, не точность в %. Слабый телефон требует повторных наблюдений и руки либо согласия общего поиска и ROI. Изменение порогов влияет на события."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        dl.addWidget(explanation)
        dl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(diagnostic)
        self.tabs.addTab(scroll, "Настройки")
        self.stop = QPushButton("Завершить и сохранить")
        self.stop.setObjectName("secondary")
        self.stop.clicked.connect(self.finish.emit)
        right.addWidget(self.stop)
        split.addWidget(right_widget)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 0)
        split.setSizes([900, 320])
        outer.addWidget(split, 1)
        self.target = QLabel("●", self)
        self.target.setObjectName("calibrationTarget")
        self.target.resize(48, 48)
        self.target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.target.hide()
        self._pose = None
        self._warning = False

    @staticmethod
    def spin(low, high, step, value, decimals):
        w = QDoubleSpinBox()
        w.setRange(low, high)
        w.setSingleStep(step)
        w.setDecimals(decimals)
        w.setValue(value)
        w.setMinimumHeight(38)
        return w

    def show_target(self, pose):
        self._pose = pose
        w, h = self.width(), self.height()
        pad = 36
        coords = {
            "CENTER": (w / 2, h / 2),
            "LEFT": (pad, h / 2),
            "RIGHT": (w - pad, h / 2),
            "UP": (w / 2, pad),
            "DOWN": (w / 2, h - pad),
        }
        x, y = coords[pose]
        self.target.move(int(x - 24), int(y - 24))
        self.target.show()
        self.target.raise_()

    def calibration_running(self, pose, remaining, total, stage="gaze"):
        self.neutral.setEnabled(False)
        self.five.setEnabled(False)
        instruction = (
            "поверните только голову в указанную сторону"
            if stage == "head"
            else "смотрите только глазами на точку, голова прямо"
        )
        self.calibration_note.setText(
            f"{ARROWS[pose]} {DIRECTIONS[pose]}: {instruction} · осталось {remaining:.0f} с"
        )
        self.progress.setValue(int(100 * (1 - remaining / max(total, 0.01))))
        self.progress.setFormat("Настройка взгляда — следуйте точке")

    def calibration_finished(self, message):
        self.neutral.setEnabled(False)
        self.five.setEnabled(True)
        self.target.hide()
        self._pose = None
        self.calibration_note.setText(message)

    def show_directions(
        self,
        head,
        gaze,
        calibrated=True,
        preview=False,
        head_calibrated=None,
        gaze_calibrated=None,
    ):
        head_ok = calibrated if head_calibrated is None else head_calibrated
        gaze_ok = calibrated if gaze_calibrated is None else gaze_calibrated
        for card, state, qualified in (
            (self.head_card, head, head_ok),
            (self.gaze_card, gaze, gaze_ok),
        ):
            card.update_state(state if calibrated or preview else "UNKNOWN")
            card.note.setText(
                "Предварительно · без предупреждений"
                if not qualified and preview
                else "Нужна калибровка" if not calibrated else ""
            )
            card.note.setVisible(bool(card.note.text()))

    def show_frame(self, frame):
        self.camera.set_frame(frame)
        if self._pose:
            self.show_target(self._pose)

    def highlight_gaze(self, warning):
        warning = bool(warning)
        if warning == self._warning:
            return
        self._warning = warning
        for control in (self.gaze_card.state, self.progress):
            control.setProperty("warning", warning)
            control.style().unpolish(control)
            control.style().polish(control)
            control.update()

    def set_observation(self, faces, phone):
        self.face_summary.setText("Лицо: " + str(faces))
        self.phone_summary.setText("Телефон: " + phone)

    def set_case(self, key, text):
        item = self.checklist.item(self.rows[key], 1)
        if item.text() != text:
            item.setText(text)
            color = (
                "#9ba9bd"
                if any(
                    k in text
                    for k in (
                        "UNKNOWN",
                        "НЕ АКТИВНО",
                        "Нет браузера",
                        "Не измерено",
                        "Не определяется",
                        "предварительно",
                    )
                )
                else (
                    "#efbb72"
                    if any(k in text for k in ("АКТИВНО", "CANDIDATE", "эвристика"))
                    else "#dce5f2"
                )
            )
            item.setForeground(QColor(color))
            self.checklist.resizeRowToContents(self.rows[key])

    def log(self, text, level=None, episode_id=""):
        self.empty_feed.hide()
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, episode_id)
        if level is not None:
            item.setForeground(
                QColor(("#91cdaa", "#f4c36f", "#ff8c8c")[min(2, max(0, level))])
            )
        self.feed.insertItem(0, item)
        while self.feed.count() > 100:
            self.feed.takeItem(self.feed.count() - 1)

    def closeEvent(self, event):
        if self.locked:
            event.ignore()
            self.banner.setText(
                "Защищённый режим: выход Ctrl+Shift+F12 с паролем экзаменатора"
            )
        else:
            super().closeEvent(event)
