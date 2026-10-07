from pathlib import Path
import hashlib
import cv2
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QDialogButtonBox
from .screens import SidePanel


class ClipViewer(QDialog):
    def __init__(self, clips, directory, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Футаж эпизода · требуется ручная проверка")
        self.resize(640, 560)
        self.clips = clips
        self.directory = Path(directory).resolve()
        self.cap = None
        self.times = []
        self.index = 0
        lay = QVBoxLayout(self)
        self.selector = QComboBox()
        self.selector.addItems([f"Фрагмент {i+1}" for i in range(len(clips))])
        lay.addWidget(self.selector)
        self.preview = QLabel()
        self.preview.setMinimumSize(480, 360)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.preview)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)
        self.selector.currentIndexChanged.connect(self.load)
        if clips:
            self.load(0)

    def load(self, index):
        self.timer.stop()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        p, meta = self.clips[index]
        path = Path(p).resolve()
        try:
            if not path.is_relative_to(self.directory) or path.suffix != ".avi":
                raise ValueError("Недопустимый путь")
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
                raise ValueError("Файл изменён: SHA256 не совпадает")
            self.cap = cv2.VideoCapture(str(path))
            self.times = meta["capture_times"]
            self.index = 0
            if not self.cap.isOpened():
                raise ValueError("Не удалось открыть видео")
            self.info.setText(
                f"Источник: {meta['source']} · {meta['fps']} кадров/с · {meta['frames']} кадров\n"
                + ("Запись оборвана завершением сессии.\n" if meta["truncated"] else "")
                + "Сигнал для проверки, не доказательство нарушения. Без аудио/записи рабочего стола."
            )
            self.step()
        except (OSError, ValueError, KeyError) as exc:
            self.info.setText(str(exc))

    def step(self):
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            return
        self.preview.setPixmap(SidePanel.pixmap(frame, 480, 360))
        self.index += 1
        if self.index < len(self.times):
            self.timer.start(
                max(
                    1, int(1000 * (self.times[self.index] - self.times[self.index - 1]))
                )
            )

    def done(self, result):
        self.timer.stop()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        super().done(result)
