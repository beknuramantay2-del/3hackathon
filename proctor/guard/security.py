import platform

OS = platform.system()

FULL_GUARD = False
if OS == "Windows":
    try:
        import keyboard
        import ctypes
        import win32gui

        FULL_GUARD = True
    except ImportError:
        pass


def describe() -> str:
    if FULL_GUARD:
        return "Windows: зависимости доступны; активация проверяется при запуске"
    return f"monitor-only ({OS}, только логирование попыток)"


def verify_password(password, salt_hex, hash_hex, iterations):

    import hashlib
    import hmac

    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    try:
        return hmac.compare_digest(digest, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


def guard_health(
    hotkeys, focus, processes, clipboard, now, required=True, surface=None
):
    if not required:
        return True, []
    errors = []
    if hotkeys.status != "active":
        errors.append("Клавиатурная защита: " + hotkeys.status)
    if (
        not focus.enabled
        or focus.status != "active"
        or not focus.focus_ok
        or now - focus.checked_at > 1.0
    ):
        errors.append("Фокус экзамена не защищён")
    if processes.status != "active" or now - processes.checked_at > max(
        3.0, processes.interval * 2
    ):
        errors.append("Проверка процессов не активна")
    if processes.blocking:
        errors.append("Запрещённые программы: " + ", ".join(processes.blocking))
    if not clipboard._active or getattr(clipboard, "status", "active") != "active":
        errors.append("Защита буфера не активна")
    if (
        surface is None
        or surface.status != "active"
        or not surface.capture_ok
        or not surface.overlays_ok
        or now - surface.checked_at > 1.0
    ):
        errors.append("Защита снимков/посторонних окон не подтверждена")
    return not errors, errors
