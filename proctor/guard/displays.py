"""Unknown monitor enumeration is not a successful one-monitor security check."""
_warned = False

def monitor_count():
    global _warned
    try:
        from screeninfo import get_monitors
        return len(get_monitors())
    except Exception as e:
        if not _warned:
            print("[displays] число мониторов неизвестно:",e)
            _warned = True
        return 0

def clear_clipboard_qt(app):
    app.clipboard().clear()
