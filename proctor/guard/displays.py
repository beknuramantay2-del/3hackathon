"""Displays + clipboard."""
def monitor_count():
    try:
        from screeninfo import get_monitors
        return len(get_monitors())
    except Exception as e:
        print("[displays]", e)
        return 1

def clear_clipboard_qt(app):
    try:
        app.clipboard().clear()
    except Exception:
        pass
