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
