import json
import os
import subprocess
import sys
import time
from pathlib import Path
import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from proctor.school.database import SchoolDB, SchoolError
from proctor.school.monitoring import (
    MonitorPublisher,
    read_snapshot,
    MAX_SNAPSHOT_BYTES,
)


@pytest.fixture
def connected(tmp_path):
    db = SchoolDB(tmp_path / "school.db")
    db.setup_owner("owner", "Administrator", "Owner-password-123")
    admin = db.login("owner", "Owner-password-123")
    db.register_student("student", "Student", "Student-password-123")
    student = db.login("student", "Student-password-123")
    quiz = db.create_test(
        admin,
        "Prepared test",
        60,
        [dict(text="Question?", options=["A", "B"], correct=0)],
    )
    ident, lease = db.new_attempt(student, quiz, True)
    directory = tmp_path / "sessions" / "run"
    directory.mkdir(parents=True)
    db.claim(lease)
    db.bind_run(lease, directory)
    yield db, admin, student, ident, lease, directory
    db.close()


def publish(directory):
    worker = MonitorPublisher(directory)
    worker.offer(
        np.zeros((480, 640, 3), np.uint8),
        10,
        time.monotonic(),
        dict(
            head="LEFT",
            gaze="DOWN",
            faces=1,
            phone="Подтверждён",
            level=2,
            calibrated=True,
        ),
    )
    deadline = time.monotonic() + 4
    while not worker.published and not worker.error and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker.published == 1, worker.error
    assert worker.stop()
    return directory / "monitor.json"


def test_separate_client_process_is_visible_to_privileged_connection(connected):
    db, admin, student, ident, lease, directory = connected
    command = """
import sys,time,numpy as np
from proctor.school.monitoring import MonitorPublisher
p=MonitorPublisher(sys.argv[1])
p.offer(np.zeros((480,640,3),np.uint8),17,time.monotonic(),dict(head='RIGHT',gaze='DOWN',faces=1,phone='Подтверждён',level=2,calibrated=True))
limit=time.monotonic()+3
while not p.published and not p.error and time.monotonic()<limit:time.sleep(.01)
assert p.published==1,p.error
assert p.stop()
"""
    subprocess.run(
        [sys.executable, "-c", command, str(directory)], check=True, timeout=8
    )
    monitor = SchoolDB(db.path)
    try:
        data = monitor.live_snapshot(admin, ident)
        assert data["live"] and data["seq"] == 17
        assert data["status"]["head"] == "RIGHT" and data["status"]["gaze"] == "DOWN"
        decoded = cv2.imdecode(np.frombuffer(data["image"], np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape[:2] == (360, 480)
        with pytest.raises(SchoolError):
            monitor.live_snapshot(student, ident)
        db.start(lease)
        db.finish(lease, {})
        assert not monitor.live_snapshot(admin, ident)["live"]
    finally:
        monitor.close()


def test_stale_future_corrupt_and_oversize_frames_not_live(connected):
    db, admin, student, ident, lease, directory = connected
    path = publish(directory)
    original = json.loads(path.read_text())
    for age in (10, -100):
        data = dict(original, captured_at=time.time() - age)
        path.write_text(json.dumps(data))
        snap = db.live_snapshot(admin, ident)
        assert not snap["live"] and snap["image"] is None
    path.write_text("not json")
    assert not db.live_snapshot(admin, ident)["live"]
    path.write_bytes(b"x" * (MAX_SNAPSHOT_BYTES + 1))
    assert not db.live_snapshot(admin, ident)["live"]


def test_role_and_run_directory_validation(connected, tmp_path):
    db, admin, student, ident, lease, directory = connected
    with pytest.raises(SchoolError):
        db.live_snapshot("fake", ident)
    with pytest.raises(SchoolError):
        db.live_snapshot(admin, "unknown")
    with db.tx():
        db.con.execute(
            "UPDATE attempts SET run_dir=? WHERE id=?",
            (str(tmp_path / "outside"), ident),
        )
    with pytest.raises(SchoolError):
        db.live_snapshot(admin, ident)


def test_publisher_bounded_and_rate_limited(tmp_path):
    worker = MonitorPublisher(tmp_path)
    try:
        frame = np.zeros((360, 480, 3), np.uint8)
        assert worker.offer(frame, 1, time.monotonic(), {})
        assert not worker.offer(frame, 2, time.monotonic(), {})
        deadline = time.monotonic() + 3
        while not worker.published and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.published == 1
    finally:
        assert worker.stop()
    assert not worker.offer(frame, 3, time.monotonic(), {})


def test_admin_ui_monitor_only_and_live_state_visible(connected):
    from PyQt6.QtWidgets import QApplication, QPushButton
    from proctor.school.hub import Hub

    db, admin, student, ident, lease, directory = connected
    publish(directory)
    app = QApplication.instance() or QApplication([])
    w = Hub(db, preview=True, mode="monitor")
    w.token = admin
    w.actor = db.actor(admin)
    w.dashboard()
    w.show()
    app.processEvents()
    w.poll()
    app.processEvents()
    assert w.tests_table is None and w.tabs.count() == 3
    assert not any(
        "Создать" in b.text() and b.isVisible() for b in w.findChildren(QPushButton)
    )
    assert (
        "Голова: Влево" in w.live_status.text()
        and "глаза: Вниз" in w.live_status.text()
    )
    assert not w.live_image.pixmap().isNull()
    w.timer.stop()
    w.close()
    app.processEvents()


def test_modes_reject_wrong_role(connected, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from proctor.school.hub import Hub

    db, admin, student, ident, lease, directory = connected
    app = QApplication.instance() or QApplication([])
    for mode, login, password in [
        ("monitor", "student", "Student-password-123"),
        ("student", "owner", "Owner-password-123"),
    ]:
        w = Hub(db, True, mode)
        messages = []
        monkeypatch.setattr(w, "fail", messages.append)
        w.login.setText(login)
        w.password.setText(password)
        w.sign_in()
        assert messages and w.actor is None and not w.token
        w.timer.stop()
        w.close()
        app.processEvents()


def test_prepare_cli_import_and_password_not_in_arguments(tmp_path, monkeypatch):
    from proctor.school.manage import main

    answers = iter(
        [
            "Owner-password-123",
            "Owner-password-123",
            "Owner-password-123",
            "Student-password-123",
            "Student-password-123",
            "Owner-password-123",
        ]
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    database = str(tmp_path / "school.db")
    assert (
        main(["--db", database, "init", "--login", "owner", "--name", "Administrator"])
        == 0
    )
    assert (
        main(
            [
                "--db",
                database,
                "account",
                "--login",
                "owner",
                "--new-login",
                "student",
                "--name",
                "Student",
            ]
        )
        == 0
    )
    quiz = tmp_path / "test.json"
    quiz.write_text(
        json.dumps(
            dict(
                title="Prepared test",
                minutes=1,
                questions=[dict(text="Question?", options=["A", "B"], correct=1)],
            )
        )
    )
    assert main(["--db", database, "import-test", "--login", "owner", str(quiz)]) == 0
    db = SchoolDB(database)
    try:
        student = db.login("student", "Student-password-123")
        assert len(db.tests(student)) == 1
    finally:
        db.close()


def test_local_locks_allow_panel_and_client_but_not_duplicate(tmp_path):
    from PyQt6.QtCore import QLockFile

    monitor = QLockFile(str(tmp_path / "school.monitor.lock"))
    student = QLockFile(str(tmp_path / "school.client.lock"))
    duplicate = QLockFile(str(tmp_path / "school.client.lock"))
    assert monitor.tryLock(100) and student.tryLock(100)
    assert not duplicate.tryLock(100)
    monitor.unlock()
    student.unlock()
