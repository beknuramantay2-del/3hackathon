"""Мок-нарушения: потолок типа, повторы 0.3x, разбивка. Без камеры."""
import sys, os
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proctor.core.trust_score import TrustCalculator

WEIGHTS = {"PHONE_DETECTED": 15, "GAZE_DOWN": 5}


def v(t, sev, ts):
    return SimpleNamespace(type=t, severity=sev, t_start=ts)


def test_single_loss():
    c = TrustCalculator(WEIGHTS)
    c.apply_violation(v("PHONE_DETECTED", 2, 1000.0))
    s = c.get_score()
    assert s["trust_score"] == 70, s
    assert s["by_type"]["PHONE_DETECTED"]["count"] == 1


def test_repeat_factor():
    c = TrustCalculator(WEIGHTS)
    c.apply_violation(v("GAZE_DOWN", 1, 1000.0))
    c.apply_violation(v("GAZE_DOWN", 1, 1002.0))
    s = c.get_score()
    assert s["trust_score"] == 100 - 5 - 5 * 0.3, s
    c.apply_violation(v("GAZE_DOWN", 1, 1010.0))
    s = c.get_score()
    assert s["trust_score"] == 100 - 5 - 5 * 0.3 - 5, s


def test_type_cap():
    c = TrustCalculator({"PHONE_DETECTED": 15})
    for i in range(10):
        c.apply_violation(v("PHONE_DETECTED", 2, 1000.0 + i * 10))
    s = c.get_score()
    assert s["by_type"]["PHONE_DETECTED"]["total_loss"] == 50.0, s
    assert s["trust_score"] == 50, s


def test_breakdown():
    c = TrustCalculator(WEIGHTS)
    c.apply_violation(v("PHONE_DETECTED", 2, 1000.0))
    c.apply_violation(v("GAZE_DOWN", 1, 1020.0))
    s = c.get_score({"PHONE_DETECTED": "телефон", "GAZE_DOWN": "взгляд вниз"})
    assert "телефон" in s["breakdown_text"] and "взгляд вниз" in s["breakdown_text"], s


def test_empty():
    c = TrustCalculator(WEIGHTS)
    s = c.get_score()
    assert s == {"trust_score": 100, "by_type": {}, "breakdown_text": ""}, s


if __name__ == "__main__":
    for fn in (test_single_loss, test_repeat_factor, test_type_cap, test_breakdown, test_empty):
        fn()
        print(f"OK {fn.__name__}")
    print("Все тесты прошли (синтетика, без камеры).")
