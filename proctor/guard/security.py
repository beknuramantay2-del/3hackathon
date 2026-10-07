import platform

OS = platform.system()

if OS == "Windows":
    FULL_GUARD = True
    try:
        import keyboard
        import ctypes
        import win32gui
    except ImportError as e:
        FULL_GUARD = False
        print(f"[guard] Windows, но нет зависимостей ({e}). Режим мониторинга.")
elif OS in ("Linux", "Darwin"):
    FULL_GUARD = False
    print("WARNING: Полная блокировка клавиш недоступна. Работаю в режиме мониторинга.")
else:
    FULL_GUARD = False
    print(f"WARNING: ОС {OS} не поддерживается. Режим мониторинга.")


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


def guard_health(hotkeys, focus, processes, clipboard, now, required=True):
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
    if not clipboard._active:
        errors.append("Защита буфера не активна")
    return not errors, errors
