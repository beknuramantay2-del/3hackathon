import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl
from proctor.guard.clipboard import ClipboardGuard
from proctor.ui.screens import SidePanel,PreflightScreen
from proctor.ui.test_window import TestWindow as Window

@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication(["proctor-tests"])
    yield app


def test_clipboard_no_recursive_storm(app):
    events = []
    guard = ClipboardGuard(app,on_violation=events.append)
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
    panel.show_frame(np.zeros((480,640,3),dtype=np.uint8))
    assert not panel.cam.pixmap().isNull()
    for i in range(200):
        panel.log(str(i))
    assert panel.feed.count() == 100
    panel.set_hold("RIGHT 1.5/2s",.75)
    assert panel.hold.value() == 75
    panel.debug_on = True
    panel.set_debug("FPS: 30")
    assert not panel.debug.isHidden()


def test_preflight_requires_all_checks(app):
    pre = PreflightScreen(lambda:[("Камера",True),("YOLO",False),("Мониторов: 1",True)])
    pre.fio.setText("Test User")
    pre.refresh()
    assert not pre.start_btn.isEnabled()
    pre.checks_fn = lambda:[("Камера",True),("YOLO",True)]
    pre.refresh()
    assert pre.start_btn.isEnabled()
    pre.poll.stop()


def test_navigation_policy_no_arbitrary_local_files(tmp_path):
    # Test pure policy without starting Chromium.
    class Policy:
        is_file = True
        base = QUrl.fromLocalFile(str(tmp_path/"test.html"))
    p = Policy()
    assert Window._url_ok(p,QUrl.fromLocalFile(str(tmp_path/"test.html")))
    assert not Window._url_ok(p,QUrl.fromLocalFile("/etc/passwd"))
    p.is_file = False
    p.allowed_hosts = {"exam.example"}
    assert Window._url_ok(p,QUrl("https://exam.example/test"))
    assert not Window._url_ok(p,QUrl("https://exam.example.attacker.test/"))
    assert not Window._url_ok(p,QUrl("javascript:alert(1)"))

@pytest.mark.skipif(os.getenv("PROCTOR_WEB_SMOKE") != "1",reason="opt-in native WebEngine")
def test_webengine_bridge_finish(app):
    from PyQt6.QtTest import QTest
    finished = []
    w = Window(lambda x:None,finished.append,"proctor/dev/stub.html",[])
    w.show()
    try:
        QTest.qWait(2500)
        w.page.runJavaScript("finish()")
        QTest.qWait(500)
        assert finished == ["[]"]
    finally:
        from PyQt6.QtCore import QCoreApplication,QEvent
        w.dispose()
        w.close()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        app.processEvents()
