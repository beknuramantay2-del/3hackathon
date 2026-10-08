import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from proctor.core.monitoring import session_list, read_snapshot, read_events
from proctor.core.presentation import event_label
from proctor.ui.monitor_window import DIRECTIONS


class Observer(QWidget):
    def __init__(self, root):
        super().__init__()
        self.root = Path(root)
        self.setWindowTitle("Прокторинг · Панель наблюдения")
        self.resize(1100, 760)
        outer = QVBoxLayout(self)
        title = QLabel("Наблюдение за учениками")
        title.setObjectName("title")
        outer.addWidget(title)
        instructions = QLabel(
            "Запустите прокторинг в другом окне на этом компьютере. Выберите сессию слева. Панель не включает вторую камеру и не запускает модели."
        )
        instructions.setWordWrap(True)
        outer.addWidget(instructions)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        self.sessions = QListWidget()
        self.sessions.setMaximumWidth(300)
        body.addWidget(self.sessions)
        right = QVBoxLayout()
        body.addLayout(right, 1)
        self.status = QLabel("Ожидаем приложение прокторинга…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        self.image = QLabel("Нового кадра пока нет")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(280)
        right.addWidget(self.image, 1)
        self.events = QTableWidget(0, 4)
        self.events.setHorizontalHeaderLabels(
            ["Время", "Событие", "Сигнал", "Длительность"]
        )
        self.events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        right.addWidget(self.events)
        footer = QLabel(
            "Двойной щелчок по событию в основном окне открывает футаж. Все данные остаются в data/sessions. Панель локальная, без сетевого подключения и разграничения учётных записей ОС."
        )
        footer.setWordWrap(True)
        outer.addWidget(footer)
        self.sessions.currentItemChanged.connect(lambda *_: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        current = self.sessions.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        entries = session_list(self.root)
        existing = [
            self.sessions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.sessions.count())
        ]
        desired = [str(row["directory"]) for row in entries]
        if existing != desired:
            self.sessions.blockSignals(True)
            self.sessions.clear()
            for row in entries:
                item = QListWidgetItem(row["name"] + "\n" + row["directory"].name)
                item.setData(Qt.ItemDataRole.UserRole, str(row["directory"]))
                self.sessions.addItem(item)
                if str(row["directory"]) == selected:
                    self.sessions.setCurrentItem(item)
            if self.sessions.currentRow() < 0 and entries:
                self.sessions.setCurrentRow(0)
            self.sessions.blockSignals(False)
        item = self.sessions.currentItem()
        if not item:
            self.image.clear()
            self.image.setText("Запустите python -m proctor")
            self.events.setRowCount(0)
            return
        directory = Path(item.data(Qt.ItemDataRole.UserRole))
        row = next((r for r in entries if r["directory"] == directory), None)
        if row is None:
            return
        snapshot = read_snapshot(directory, row["state"])
        self.image.clear()
        if snapshot["live"]:
            pixmap = QPixmap()
            if pixmap.loadFromData(snapshot["image"], "JPEG"):
                self.image.setPixmap(
                    pixmap.scaled(
                        self.image.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )
                )
            state = snapshot["status"]
            levels = ("ЗЕЛЁНЫЙ", "ЖЁЛТЫЙ", "КРАСНЫЙ")
            level = state.get("level", 0)
            level = level if isinstance(level, int) and 0 <= level <= 2 else 0
            self.status.setText(
                f'{row["name"]} · {levels[level]}\nГолова: {DIRECTIONS.get(state.get("head"), "Не определяется")} · Глаза: {DIRECTIONS.get(state.get("gaze"), "Не определяется")} · Лиц: {state.get("faces", "—")} · Телефон: {"сигнал" if state.get("phone") else "нет сигнала" if state.get("phone") is False else "нет свежих данных"}\n'
                + (
                    "Калибровка завершена"
                    if state.get("calibrated")
                    else "Калибровка не завершена"
                )
            )
        else:
            self.image.setText(snapshot["reason"])
            self.status.setText(row["name"] + " · " + snapshot["reason"])
        try:
            rows = read_events(directory)
        except (sqlite3.Error, OSError):
            rows = []
        self.events.setRowCount(len(rows))
        for i, (kind, severity, stamp, duration) in enumerate(rows):
            level = max(0, min(2, int(severity or 0)))
            values = (
                time.strftime("%H:%M:%S", time.localtime(stamp)),
                event_label(kind),
                ("Запись", "Жёлтый", "Красный")[level],
                f"{duration or 0:.1f} с",
            )
            for j, value in enumerate(values):
                self.events.setItem(i, j, QTableWidgetItem(value))

    def closeEvent(self, event):
        self.timer.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(
        description="Локальная панель наблюдения; без вопросов и учебных функций"
    )
    parser.add_argument("--sessions", default="data/sessions")
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parent.parent)
    app = QApplication(sys.argv)
    app.setStyleSheet(Path("proctor/ui/style.qss").read_text(encoding="utf8"))
    window = Observer(args.sessions)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
