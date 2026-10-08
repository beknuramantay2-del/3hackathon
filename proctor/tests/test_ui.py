import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
import numpy as np
from PyQt6.QtWidgets import QApplication
from proctor.guard.clipboard import ClipboardGuard
from proctor.ui.screens import SidePanel


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication(["proctor-tests"])
    yield app


def test_clipboard_no_recursive_storm(app):
    events = []
    guard = ClipboardGuard(app, on_violation=events.append)
    guard.start()
    try:
        for i in range(30):
            app.clipboard().setText(str(i))
            app.processEvents()
        assert len(events) == 1
        assert app.clipboard().text() == ""
    finally:
        guard.stop()
    app.clipboard().setText("after exam")
    assert app.clipboard().text() == "after exam"
    app.clipboard().clear()


def test_dashboard_and_feed_bounded(app):
    panel = SidePanel()
    panel.show_frame(np.zeros((480, 640, 3), dtype=np.uint8))
    assert not panel.cam.pixmap().isNull()
    for i in range(200):
        panel.log(str(i))
    assert panel.feed.count() == 100
    panel.set_hold("RIGHT 1.5/2s", 0.75)
    assert panel.hold.value() == 75
    panel.debug_on = True
    panel.set_debug("FPS: 30")
    assert not panel.debug.isHidden()
