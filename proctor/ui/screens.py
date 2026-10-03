"""Строгие экраны: старт, калибровка, окно теста с превью 240x180 и одной строкой статуса."""
import cv2
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QLineEdit)
from PyQt6.QtGui import QImage, QPixmap
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
        found = False
        blocked = False
        mon_blocked = False
        if self.checks_fn:
            try:
                for name, good in self.checks_fn():
                    if "Камера" in name and good:
                        found = True
                    if T["vc_label"] in name and not good:
                        blocked = True
                    if "Монитор" in name and not good:
                        mon_blocked = True
            except Exception:
                found = False
        else:
            found = True
        if blocked:
            self.cam_status.setText(T["vc_msg"])
            self.cam_status.setObjectName("statusBad")
        else:
            self.cam_status.setText(T["camera_found"] if found else T["camera_not_found"])
            self.cam_status.setObjectName("status" if found else "statusBad")
        self.cam_status.setStyle(self.cam_status.style())
        self.note.setText(T["second_monitor_msg"] if mon_blocked else "")
        self.note.setVisible(mon_blocked)
        self.unlock_btn.setVisible(mon_blocked)
        self.start_btn.setEnabled(found and not blocked and not mon_blocked and bool(self.fio_text))

    def closeEvent(self, e):
        try:
            self.poll.stop()
        except Exception:
            pass
        super().closeEvent(e)


# обратная совместимость со старым main.py
PreflightScreen = StartWindow


class CalibrationView(QWidget):
    done = pyqtSignal()
    canceled = pyqtSignal()

    def __init__(self, seconds=5.0):
        super().__init__()
        self.setObjectName("root")
        self.seconds = seconds
        self.left = int(seconds)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.addStretch(1)

        self.dot = QLabel()
        self.dot.setObjectName("dot")
        self.dot.setFixedSize(16, 16)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.dot)
        row.addStretch(1)
        lay.addLayout(row)

        t = QLabel(T["calib_text"])
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        self.counter = QLabel(f"{T['calib_left']}: {self.left} с")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.counter)

        brow = QHBoxLayout()
        brow.addStretch(1)
        cancel = QPushButton(T["calib_cancel"])
        cancel.setObjectName("secondary")
        cancel.clicked.connect(lambda: (self.t.stop(), self.canceled.emit()))
        brow.addWidget(cancel)
        brow.addStretch(1)
        lay.addLayout(brow)
        lay.addStretch(1)

        self.t = QTimer(self)
        self.t.timeout.connect(self.step)

    def start(self):
        self.left = int(self.seconds)
        self.counter.setText(f"{T['calib_left']}: {self.left} с")
        self.t.start(1000)

    def step(self):
        self.left -= 1
        if self.left <= 0:
            self.t.stop()
            self.done.emit()
        else:
            self.counter.setText(f"{T['calib_left']}: {self.left} с")


class SidePanel(QWidget):
    """Только превью 240x180 и одна строка статуса. Остальные методы — no-op для совместимости."""

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        self.cam = QLabel(T["preview_empty"])
        self.cam.setObjectName("camera")
        self.cam.setFixedSize(240, 180)
        self.cam.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.cam)
        self.status = QLabel(T["watch_active"])
        self.status.setObjectName("status")
        lay.addWidget(self.status)
        self.debug_on = False

    def show_frame(self, frame):
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            q = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            self.cam.setPixmap(QPixmap.fromImage(q).scaled(
                240, 180, Qt.AspectRatioMode.KeepAspectRatio))
        except Exception:
            pass

    def set_status(self, text):
        self.status.setText(text)

    def log(self, text):
        self.set_status(text)

    def set_hold(self, name, frac):
        pass

    def mark_fired(self, vtype):
        pass

    def set_debug(self, text):
        pass
