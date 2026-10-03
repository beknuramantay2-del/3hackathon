"""OS gate for Guard: full block on Windows, monitor-only elsewhere."""
import platform

OS = platform.system()

if OS == "Windows":
    FULL_GUARD = True
    try:
        import keyboard  # noqa: F401
        import ctypes  # noqa: F401
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
        return "full (Windows, блокировка активна)"
    return f"monitor-only ({OS}, только логирование попыток)"


def verify_password(password, salt_hex, hash_hex, iterations):
    """Проверка пароля экзаменатора (PBKDF2-HMAC-SHA256)."""
    import hashlib
    import hmac
    try:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    try:
        return hmac.compare_digest(digest, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False
