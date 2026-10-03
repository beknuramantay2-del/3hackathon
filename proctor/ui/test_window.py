"""Окно теста: URL из конфига, белый список доменов, новые окна блокируются."""
import os
from urllib.parse import urlparse
from PyQt6.QtCore import QObject, pyqtSlot, QUrl, Qt
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from .screens import SidePanel, T


class Bridge(QObject):
    def __init__(self, on_violation, on_finish):
        super().__init__()
        self.on_violation = on_violation
        self.on_finish = on_finish

    @pyqtSlot(str)
    def violation(self, t):
        self.on_violation(t)

    @pyqtSlot(str)
    def finish(self, answers):
        self.on_finish(answers)


class TestWindow(QWidget):
    def __init__(self, on_violation, on_finish, test_url, allowed_domains):
        super().__init__()
        self.setObjectName("root")
        self.panel = SidePanel()
        self.is_file = os.path.isfile(test_url)
        if self.is_file:
            self.base = QUrl.fromLocalFile(os.path.abspath(test_url))
            self.allowed_hosts = set()
        else:
            self.base = QUrl(test_url)
            host = urlparse(test_url).hostname or ""
            self.allowed_hosts = set(allowed_domains) or ({host.lower()} if host else set())
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebChannel import QWebChannel
            self.view = QWebEngineView()
            self.view.page().createWindow = self._block_window
            self.bridge = Bridge(on_violation, on_finish)
            ch = QWebChannel(self.view.page())
            ch.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(ch)
            self.view.load(self.base)
            self.view.urlChanged.connect(self._check_url)
            self.view.loadFinished.connect(lambda: self.view.page().runJavaScript(
                "if(typeof qt!=='undefined'){new QWebChannel(qt.webChannelTransport,function(ch){window.bridge=ch.objects.bridge;});}"))
        except Exception as e:
            print("[web]", e)
            self.view = QLabel(T["test_unavailable"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.panel, 0, Qt.AlignmentFlag.AlignBottom)
        self.setWindowTitle(T["test_title"])

    def _url_ok(self, qurl):
        if self.is_file:
            return qurl.scheme() == "file"
        return qurl.scheme() in ("http", "https") and (qurl.host() or "").lower() in self.allowed_hosts

    def _check_url(self, qurl):
        if not self._url_ok(qurl):
            self.bridge.on_violation(f"NAV_BLOCKED:{qurl.toString()}")
            self.view.setUrl(self.base)

    def _block_window(self, *args, **kwargs):
        self.bridge.on_violation("NAV_BLOCKED:new-window")
        return None
