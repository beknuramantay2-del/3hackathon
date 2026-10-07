from proctor.core.models import Box
from proctor.core.tracking import LiteTracker, iou


def box(x=0, confidence=0.8):
    return Box(confidence, x, 0, x + 40, 80)


def test_id_stable_and_confirmed():
    t = LiteTracker()
    for i in range(10):
        b = t.update([box(i * 2)], i * 0.125)[0]
        assert b.track_id == 1
    assert b.confirmed and b.observed
    assert abs(b.x1 - 18) < 4


def test_weak_only_recovers():
    t = LiteTracker()
    assert t.update([box(confidence=0.2)], 0) == []
    t.update([box()], 0.1)
    b = t.update([box(2, 0.2)], 0.2)[0]
    assert b.track_id == 1 and b.observed


def test_dropout_prediction_not_evidence_and_expiry():
    t = LiteTracker()
    for i in range(3):
        t.update([box()], i * 0.1)
    b = t.update([], 0.3)[0]
    assert b.confirmed and not b.observed
    assert t.update([], 0.8) == []
    assert t.update([box()], 0.9)[0].track_id == 2


def test_two_objects_one_to_one():
    t = LiteTracker()
    ids = [b.track_id for b in t.update([box(0), box(100)], 0)]
    recovered = t.update([box(102), box(2)], 0.1)
    assert [b.track_id for b in recovered] == ids
    assert len(t.tracks) == 2


def test_iou_zero_and_identity():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0


def test_fast_motion_strong_recovery_without_overlap():
    t = LiteTracker()
    t.update([box(0)], 0)
    assert t.update([box(50)], 0.125)[0].track_id == 1
    assert t.update([box(95)], 0.25)[0].track_id == 1


def test_far_false_detection_cannot_steal_id():
    t = LiteTracker()
    t.update([box(0)], 0)
    result = t.update([box(400)], 0.1)
    observed = [b for b in result if b.observed]
    assert len(observed) == 1 and observed[0].track_id == 2
