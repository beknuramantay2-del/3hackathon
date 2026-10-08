import os
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from proctor.core.monitoring import (
    MonitorPublisher,
    read_snapshot,
    write_session,
    session_list,
    read_events,
)
from proctor.core.store import Store


def test_session_discovery_does_not_need_accounts_or_questions(tmp_path):
    run = tmp_path / "run"
    write_session(run, "Learner", "running")
    rows = session_list(tmp_path)
    assert len(rows) == 1 and rows[0]["name"] == "Learner"
    assert rows[0]["state"] == "running"
    (tmp_path / "junk").mkdir()
    assert len(session_list(tmp_path)) == 1
    write_session(run, "Learner", "finished")
    assert session_list(tmp_path)[0]["state"] == "finished"


def test_observer_frame_is_fresh_and_finished_session_not_live(tmp_path):
    publisher = MonitorPublisher(tmp_path)
    try:
        assert publisher.offer(
            np.zeros((480, 640, 3), np.uint8),
            5,
            time.monotonic(),
            dict(head="LEFT", gaze="DOWN", calibrated=True),
        )
        deadline = time.monotonic() + 3
        while publisher.published == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = read_snapshot(tmp_path, "running")
        assert snapshot["live"] and snapshot["status"]["gaze"] == "DOWN"
        assert not read_snapshot(tmp_path, "finished")["live"]
        data = json.loads((tmp_path / "monitor.json").read_text())
        data["captured_at"] -= 10
        (tmp_path / "monitor.json").write_text(json.dumps(data))
        assert not read_snapshot(tmp_path, "running")["live"]
    finally:
        assert publisher.stop()


def test_observer_database_is_read_only(tmp_path):
    store = Store(str(tmp_path / "session.db"), str(tmp_path / "shots"))
    try:
        assert read_events(tmp_path) == []
        assert not (tmp_path / "school.db").exists()
    finally:
        store.close()


def test_default_entry_has_no_embedded_quiz_mode():
    result = subprocess.run(
        [sys.executable, "-m", "proctor", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "--name" in result.stdout and "--protected" in result.stdout
    assert "--embedded-test" not in result.stdout and "--student" not in result.stdout


@pytest.mark.skipif(
    os.getenv("PROCTOR_MODEL_SMOKE") != "1",
    reason="opt-in native standalone camera replay",
)
def test_native_proctoring_process_publishes_without_school_module(tmp_path):
    image = cv2.imread(os.environ["PROCTOR_FACE_FIXTURE"])
    h, w = image.shape[:2]
    video = tmp_path / "portrait.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(600):
        writer.write(image)
    writer.release()
    cfg = yaml.safe_load(Path("proctor/config.yaml").read_text())
    cfg["hash_chain"]["enabled"] = False
    conf = tmp_path / "config.yaml"
    conf.write_text(yaml.safe_dump(cfg))
    before = {str(row["directory"]) for row in session_list("data/sessions")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "proctor",
            "--preview",
            "--video",
            str(video),
            "--config",
            str(conf),
            "--name",
            "Native Observer",
            "--perf",
            "weak",
            "--run-seconds",
            "8",
        ],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    run = None
    try:
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            fresh = [
                r
                for r in session_list("data/sessions")
                if str(r["directory"]) not in before
            ]
            if fresh:
                run = fresh[0]["directory"]
                snapshot = read_snapshot(run, fresh[0]["state"])
                if snapshot["live"]:
                    assert snapshot["status"]["name"] == "Native Observer"
                    assert snapshot["status"]["source"] == "video replay"
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("No live standalone snapshot")
        _, errors = process.communicate(timeout=15)
        assert process.returncode == 0, errors.decode()[-2000:]
        assert run is not None and (run / "runtime.json").is_file()
        assert json.loads((run / "session.json").read_text())["state"] == "finished"
        assert not read_snapshot(run, "finished")["live"]
        assert json.loads((run / "runtime.json").read_text())["frames_yolo"] > 0
        assert read_events(run) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_observer_widget_displays_states_and_clears_finished_frame(tmp_path):
    from PyQt6.QtWidgets import QApplication
    from proctor.monitor import Observer

    app = QApplication.instance() or QApplication([])
    run = tmp_path / "run"
    write_session(run, "Learner", "running")
    publisher = MonitorPublisher(run)
    window = None
    try:
        publisher.offer(
            np.zeros((480, 640, 3), np.uint8),
            1,
            time.monotonic(),
            dict(
                head="LEFT", gaze="DOWN", level=2, faces=1, phone=True, calibrated=True
            ),
        )
        deadline = time.monotonic() + 3
        while not publisher.published and time.monotonic() < deadline:
            time.sleep(0.01)
        window = Observer(tmp_path)
        window.show()
        app.processEvents()
        window.refresh()
        assert "Влево" in window.status.text() and "Вниз" in window.status.text()
        assert "КРАСНЫЙ" in window.status.text()
        assert not window.image.pixmap().isNull()
        write_session(run, "Learner", "finished")
        window.refresh()
        assert window.image.pixmap().isNull()
    finally:
        publisher.stop()
        if window:
            window.close()
