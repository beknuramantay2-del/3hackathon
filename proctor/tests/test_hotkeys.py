import sys
import types
from proctor.guard import hotkeys


def test_exit_key_not_suppressed_by_bare_f12(monkeypatch):
    registered = []
    kb = types.SimpleNamespace(
        add_hotkey=lambda key, callback, **kw: registered.append((key, kw)),
        unhook_all=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "keyboard", kb)
    monkeypatch.setattr(hotkeys, "FULL_GUARD", True)
    guard = hotkeys.HotkeyGuard(["alt+tab", "f12", "ctrl+c"], enabled=True)
    assert guard.start()
    keys = [key for key, _ in registered]
    assert "f12" not in keys and "ctrl+shift+f12" in keys
    assert "alt+tab" in keys and guard.status == "active"
    guard.stop()


def test_hook_failure_not_full_guard(monkeypatch):
    def bad(*args, **kw):
        raise OSError("hook failed")

    monkeypatch.setitem(
        sys.modules,
        "keyboard",
        types.SimpleNamespace(add_hotkey=bad, unhook_all=lambda: None),
    )
    monkeypatch.setattr(hotkeys, "FULL_GUARD", True)
    guard = hotkeys.HotkeyGuard(["alt+tab"])
    assert not guard.start()
    assert guard.status == "error" and guard.errors
