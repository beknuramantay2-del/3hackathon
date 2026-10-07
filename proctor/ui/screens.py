import cv2
import time
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QApplication,
    QGridLayout,
)
from PyQt6.QtGui import QImage, QPixmap, QColor
from proctor.core.strings import load_strings

T = load_strings()


class StartWindow(QWidget):
    ok = pyqtSignal()

    def __init__(self, checks_fn=None):
        super().__init__()
        self.setObjectName("root")
        self.checks_fn = checks_fn
        self.fio_text = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        box = QWidget()
        box.setObjectName("panel")
        bl = QVBoxLayout(box)
        t = QLabel(T["start_title"])
        t.setObjectName("title")
        bl.addWidget(t)
        self.fio = QLineEdit()
        self.fio.setPlaceholderText(T["start_fio"])
        self.fio.textChanged.connect(lambda s: setattr(self, "fio_text", s.strip()))
        bl.addWidget(self.fio)
        self.start_btn = QPushButton(T["start_button"])
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(lambda: self.ok.emit())
        bl.addWidget(self.start_btn)
        lay.addWidget(box)

        self.cam_status = QLabel(T["camera_not_found"])
        self.cam_status.setObjectName("status")
        lay.addWidget(self.cam_status)
        self.note = QLabel("")
        self.note.setObjectName("statusBad")
        self.note.setVisible(False)
        lay.addWidget(self.note)
        self.unlock_btn = QPushButton(T["unlock_button"])
        self.unlock_btn.setObjectName("secondary")
        self.unlock_btn.setVisible(False)
        self.unlock_btn.clicked.connect(self._unlock)
        lay.addWidget(self.unlock_btn)
        lay.addStretch(1)
        self.on_unlock = None

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(1000)
        QTimer.singleShot(0, self.refresh)

    def _unlock(self):
        if self.on_unlock is not None and self.on_unlock():
            self.refresh()

    def refresh(self):
        checks = self.checks_fn() if self.checks_fn else [("Готово", True)]
        self.cam_status.setText(
            "\n".join(("✓ " if good else "… ") + name for name, good in checks)
        )
        self.cam_status.setWordWrap(True)
        mon_blocked = any("Мониторов" in name and not good for name, good in checks)
        self.note.setText(T["second_monitor_msg"] if mon_blocked else "")
        self.note.setVisible(mon_blocked)
        self.unlock_btn.setVisible(mon_blocked)
        self.start_btn.setEnabled(
            all(good for _, good in checks) and bool(self.fio_text)
        )

    def closeEvent(self, e):
        try:
            self.poll.stop()
        except Exception:
            pass
        super().closeEvent(e)


PreflightScreen = StartWindow


class CalibrationView(QWidget):
    done = pyqtSignal()
    canceled = pyqtSignal()
    prompts = (
        "Смотрите прямо. Голова неподвижна",
        "Глазами влево. Голова прямо",
        "Глазами вправо. Голова прямо",
        "Глазами вверх. Голова прямо",
        "Глазами вниз. Голова прямо",
    )

    def __init__(self, seconds=20.0, external=False):
        super().__init__()
        self.setObjectName("root")
        self.seconds = seconds
        self.external = external
        self.started_at = 0.0
        self.last_phase = -1
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        self.title = QLabel("Калибровка: 5 поз взгляда")
        self.title.setObjectName("title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)
        self.prompt = QLabel("")
        self.prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.prompt)
        self.counter = QLabel("")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.counter)
        self.preview = QLabel()
        self.preview.setFixedSize(320, 240)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignCenter)
        self.directions = QLabel("ГОЛОВА: — · ГЛАЗА: —")
        self.directions.setWordWrap(True)
        self.directions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.directions)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        self.retry = QPushButton("Повторить калибровку")
        self.retry.setVisible(False)
        lay.addWidget(self.retry)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(lambda: (self.t.stop(), self.canceled.emit()))
        lay.addWidget(cancel)
        lay.addStretch(1)
        self.t = QTimer(self)
        self.t.timeout.connect(self.step)

    def start(self):
        self.started_at = time.monotonic()
        self.last_phase = -1
        self.message.setText("")
        self.retry.setVisible(False)
        self.t.start(100)
        self.step()

    def failed(self, text):
        self.t.stop()
        self.message.setText(text)
        self.retry.setVisible(True)

    def step(self):
        if self.external:
            return
        elapsed = time.monotonic() - self.started_at
        phase = min(4, int(elapsed / (self.seconds / 5)))
        if phase != self.last_phase:
            self.last_phase = phase
            self.prompt.setText(self.prompts[phase])
            QApplication.beep()
        left = max(0, self.seconds - elapsed)
        self.counter.setText(f"Поза {phase+1}/5 · осталось {left:.1f} с")
        if elapsed >= self.seconds:
            self.t.stop()
            QApplication.beep()
            self.done.emit()


class StatusTiles(QWidget):
    def __init__(self):
        super().__init__()
        self.labels = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        for i in range(4):
            label = QLabel()
            label.setObjectName("tile")
            label.setWordWrap(True)
            self.labels.append(label)
            grid.addWidget(label, i // 2, i % 2)
        self._text = None

    def setText(self, text):
        if text == self._text:
            return
        self._text = text
        for label, line in zip(self.labels, text.splitlines()):
            label.setText(line.replace(": ", "\n", 1))


class SidePanel(QWidget):

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setFixedWidth(350)
        lay = QVBoxLayout(self)
        self.timer = QLabel("00:00")
        self.timer.setObjectName("title")
        lay.addWidget(self.timer)
        self.cam = QLabel(T["preview_empty"])
        self.cam.setObjectName("camera")
        self.cam.setFixedSize(320, 240)
        self.cam.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.cam)
        self.states = StatusTiles()
        self.states.setText(
            "HEAD: UNKNOWN\nGAZE: UNKNOWN\nFACE: UNKNOWN\nPHONE: UNKNOWN"
        )
        lay.addWidget(self.states)
        self.hold_label = QLabel("Удержание: 0.0 с")
        lay.addWidget(self.hold_label)
        self.hold = QProgressBar()
        self.hold.setRange(0, 100)
        lay.addWidget(self.hold)
        self.status = QLabel(T["watch_active"])
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.debug = QLabel("")
        self.debug.setWordWrap(True)
        self.debug.setVisible(False)
        lay.addWidget(self.debug)
        self.feed = QListWidget()
        lay.addWidget(self.feed)
        self.debug_on = False
        lay.addStretch(1)

    @staticmethod
    def pixmap(frame, width=320, height=240):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        q = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(q).scaled(
            width, height, Qt.AspectRatioMode.KeepAspectRatio
        )

    def show_frame(self, frame):
        self.cam.setPixmap(self.pixmap(frame))

    def set_status(self, text):
        self.status.setText(text)

    def log(self, text, level=None, episode_id=""):
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, episode_id)
        if level is not None:
            item.setForeground(
                QColor(("#91cdaa", "#f4c36f", "#ff8c8c")[min(2, max(0, level))])
            )
        self.feed.insertItem(0, item)
        while self.feed.count() > 100:
            self.feed.takeItem(self.feed.count() - 1)

    def set_hold(self, name, frac):
        self.hold_label.setText(name)
        self.hold.setValue(min(100, max(0, int(frac * 100))))

    def mark_fired(self, vtype):
        self.log(vtype)

    def set_debug(self, text):
        self.debug.setVisible(self.debug_on)
        self.debug.setText(text)
