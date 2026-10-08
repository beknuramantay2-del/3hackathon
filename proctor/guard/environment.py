import os
import sys
from .security import FULL_GUARD, OS
from .processes import forbidden_processes

REQUIRED_PROCESSES = (
    "chrome",
    "msedge",
    "firefox",
    "opera",
    "brave",
    "vivaldi",
    "telegram",
    "discord",
    "anydesk",
    "teamviewer",
    "rustdesk",
    "snippingtool",
    "screenclippinghost",
    "sharex",
    "lightshot",
    "obs64",
    "obs32",
)


def protected_mode(args):
    preview = bool(getattr(args, "preview", False))
    if preview and (
        getattr(args, "protected", False) or getattr(args, "profile", None) == "exam"
    ):
        raise ValueError("Нельзя совмещать --preview и --protected")
    if not preview and (
        getattr(args, "video", None)
        or getattr(args, "run_seconds", None) is not None
        or getattr(args, "profile", None) == "dev"
    ):
        raise ValueError(
            "Видео, автоматический выход и dev доступны только с явным --preview; это не защищённая сессия"
        )
    return not preview


def required_processes(configured):
    return list(dict.fromkeys([*configured, *REQUIRED_PROCESSES]))


def runtime_errors(system=None, capable=None, admin=None, build=None, monitors=None):
    system = OS if system is None else system
    capable = FULL_GUARD if capable is None else capable
    if system != "Windows":
        return [
            "Защищённый запуск требует Windows. --preview предназначен только для технической проверки без защиты"
        ]
    if not capable:
        return ["Недоступны Windows-зависимости keyboard/pywin32; защита не включена"]
    import ctypes

    admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if admin is None else admin
    build = sys.getwindowsversion().build if build is None else build
    monitors = (
        ctypes.windll.user32.GetSystemMetrics(80) if monitors is None else monitors
    )
    errors = []
    if not admin:
        errors.append("Запустите терминал или start.bat с правами администратора")
    if build < 19041:
        errors.append(
            "Нужна Windows 10 2004 или новее: исключение окна из захвата недоступно"
        )
    if monitors != 1:
        errors.append("Для защищённой сессии оставьте один активный монитор")
    return errors


def admission_errors(cfg):
    errors = runtime_errors()
    if errors:
        return errors
    credentials = cfg["guard"]["examiner"]
    try:
        valid = (
            len(bytes.fromhex(credentials["salt_hex"])) >= 16
            and len(bytes.fromhex(credentials["hash_hex"])) == 32
            and credentials["iterations"] >= 100000
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        errors.append("Настройте пароль выхода: python -m proctor.tools.set_password")
    if cfg["guard"]["exit_combo"].lower().strip() != "ctrl+shift+f12":
        errors.append("Парольный выход должен использовать Ctrl+Shift+F12")
    found = forbidden_processes(required_processes(cfg["guard"]["forbidden_processes"]))
    if found:
        errors.append(
            "Сохраните работу и самостоятельно закройте: "
            + ", ".join(sorted({p.info["name"] for p in found}))
        )
    return errors
