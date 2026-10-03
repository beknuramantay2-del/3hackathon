"""Эвристика виртуальной камеры: совпадения имени, неизвестное имя не блокирует."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proctor.core.preflight import is_virtual, get_camera_name

KW = ["virtual", "obs", "droidcam", "manycam", "splitcam", "screen", "capture"]


def test_virtual_hits():
    assert is_virtual("OBS Virtual Camera", KW)
    assert is_virtual("DroidCam Video", KW)
    assert is_virtual("USB2.0 Screen Capture", KW)


def test_real_passes():
    assert not is_virtual("Integrated Webcam", KW)
    assert not is_virtual("USB Camera 720p", KW)


def test_unknown_passes():
    assert not is_virtual("", KW)
    assert not is_virtual(None, KW)


def test_get_name_returns_str():
    assert isinstance(get_camera_name(0), str)


if __name__ == "__main__":
    for fn in (test_virtual_hits, test_real_passes, test_unknown_passes, test_get_name_returns_str):
        fn()
        print(f"OK {fn.__name__}")
    print("Все тесты прошли (синтетика, без камеры).")
