import os
from urllib.parse import urlparse
from PyQt6.QtCore import QObject, pyqtSlot, QUrl, Qt, QCoreApplication

if QCoreApplication.instance() is None:
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QApplication
from .screens import SidePanel, T
from .browser_keys import BrowserKeyFilter


class Bridge(QObject):
    def __init__(self, on_violation, on_finish):
        super().__init__()
        self.on_violation, self.on_finish = on_violation, on_finish

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
        self.base = (
            QUrl.fromLocalFile(os.path.abspath(test_url))
            if self.is_file
            else QUrl(test_url)
        )
        host = urlparse(test_url).hostname or ""
        self.allowed_hosts = {h.lower() for h in allowed_domains} or {host.lower()}
        self.bridge = Bridge(on_violation, on_finish)
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebEngineCore import (
                QWebEnginePage,
                QWebEngineProfile,
                QWebEngineUrlRequestInterceptor,
                QWebEngineSettings,
            )
            from PyQt6.QtWebChannel import QWebChannel

            owner = self

            class Page(QWebEnginePage):
                def acceptNavigationRequest(self, url, kind, main):
                    if owner._url_ok(url):
                        return True
                    owner.bridge.on_violation("NAV_BLOCKED")
                    return False

                def createWindow(self, kind):
                    owner.bridge.on_violation("NAV_BLOCKED")
                    return None

            class Requests(QWebEngineUrlRequestInterceptor):
                def interceptRequest(self, info):
                    url = info.requestUrl()
                    if url.scheme() in ("qrc", "data", "blob"):
                        return
                    if not owner._url_ok(url):
                        info.block(True)

            self.profile = QWebEngineProfile(QApplication.instance())
            self.interceptor = Requests(self.profile)
            self.profile.setUrlRequestInterceptor(self.interceptor)
            self.profile.downloadRequested.connect(lambda download: download.cancel())
            self.view = QWebEngineView()
            self.page = Page(self.profile, self.view)
            self.view.setPage(self.page)
            self.key_filter = BrowserKeyFilter(self.view, on_violation)
            QApplication.instance().installEventFilter(self.key_filter)
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False
            )
            self.channel = QWebChannel(self.page)
            self.channel.registerObject("bridge", self.bridge)
            self.page.setWebChannel(self.channel)
            self.view.loadFinished.connect(self._install_bridge)
            self.view.load(self.base)
        except ImportError as e:
            raise RuntimeError(
                f"Нужен PyQt6-WebEngine и системные библиотеки Qt: {e}"
            ) from e
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.panel, 0)
        self.setWindowTitle(T["test_title"])

    def dispose(self):

        QApplication.instance().removeEventFilter(self.key_filter)
        self.page.setWebChannel(None)
        self.page.deleteLater()
        self.view.deleteLater()

    def _install_bridge(self, ok):
        if not ok:
            self.panel.set_status("Страница теста не загружена")
            return
        self.page.runJavaScript("""
          (function(){
            function init(){new QWebChannel(qt.webChannelTransport,function(ch){
              window.bridge=ch.objects.bridge;
              document.addEventListener('visibilitychange',function(){
                if(document.hidden) window.bridge.violation('TAB_SWITCH');
              });
              window.addEventListener('blur',function(){window.bridge.violation('TAB_SWITCH');});
            });}
            if(typeof QWebChannel!=='undefined') init();
            else {let s=document.createElement('script');
              s.src='qrc:///qtwebchannel/qwebchannel.js';s.onload=init;document.head.appendChild(s);}
          })();
        """)

    def _url_ok(self, url):
        if self.is_file:
            return url.scheme() == "file" and os.path.realpath(
                url.toLocalFile()
            ) == os.path.realpath(self.base.toLocalFile())
        return (
            url.scheme() in ("http", "https")
            and url.host().lower() in self.allowed_hosts
        )
