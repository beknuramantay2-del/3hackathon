import glob
import os
import platform
import subprocess


def get_camera_name(index=0):

    system = platform.system()
    if system == "Linux":
        path = f"/sys/class/video4linux/video{index}/name"
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            names = sorted(glob.glob("/sys/class/video4linux/video*/name"))
            if names:
                try:
                    with open(names[0], encoding="utf-8", errors="replace") as f:
                        return f.read().strip()
                except OSError:
                    return ""
            return ""
    if system == "Windows":
        try:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-PnpDevice -Class Camera -Status OK).FriendlyName -join '; '",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    return ""


def is_virtual(name, keywords):
    low = (name or "").lower()
    return any(k.lower() in low for k in keywords) if low else False


def check_camera(index, keywords, enabled=True):

    if not enabled:
        return "", False
    name = get_camera_name(index)
    return name, is_virtual(name, keywords)
