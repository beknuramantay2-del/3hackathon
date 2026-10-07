from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import QWidget


def blocked_key(key, modifiers):
    ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    meta = bool(modifiers & Qt.KeyboardModifier.MetaModifier)
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    if meta or key == Qt.Key.Key_Print:
        return True
    if alt and key in (
        Qt.Key.Key_Tab,
        Qt.Key.Key_F4,
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
    ):
        return True
    if ctrl and key in (
        Qt.Key.Key_C,
        Qt.Key.Key_V,
        Qt.Key.Key_X,
        Qt.Key.Key_Tab,
        Qt.Key.Key_T,
        Qt.Key.Key_N,
        Qt.Key.Key_W,
        Qt.Key.Key_L,
        Qt.Key.Key_R,
        Qt.Key.Key_U,
    ):
        return True
    if ctrl and shift and key in (Qt.Key.Key_I, Qt.Key.Key_J):
        return True
    return key in (Qt.Key.Key_F5, Qt.Key.Key_F12)


class BrowserKeyFilter(QObject):
    def __init__(self, view, notify):
        super().__init__(view)
        self.view = view
        self.notify = notify

    def eventFilter(self, obj, event):
        if (
            event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
            and isinstance(obj, QWidget)
            and (obj is self.view or self.view.isAncestorOf(obj))
        ):
            if blocked_key(event.key(), event.modifiers()):
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    self.notify("HOTKEY_BLOCKED")
                return True
        return False
