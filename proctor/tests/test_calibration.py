import pytest
from proctor.core.calibration import Calibration,CalibrationError,POSES

TARGETS = {"CENTER":(0,0,.5,.5),"LEFT":(0,0,.75,.5),"RIGHT":(0,0,.25,.5),
           "UP":(0,0,.5,.2),"DOWN":(0,0,.5,.8)}

def test_five_poses_trim_outlier():
    c = Calibration(20)
    c.start(100.)
    for i,pose in enumerate(POSES):
        for n in range(20):
            c.add(*TARGETS[pose],now=100+i*4+.8+n*.1)
        c.add(70,60,.99,.99,pose=pose)
    base = c.finish()
    assert base["yaw"] == 0 and base["iris_h"] == .5
    assert set(base["gaze_targets"]) == set(POSES)-{"CENTER"}
    assert base["unresolved_targets"] == []
    assert base["gaze_targets"]["LEFT"][0] == .25


def test_transition_trim_and_reset():
    c = Calibration()
    c.start(10.)
    assert not c.add(0,0,.5,.5,now=10.2)
    assert c.add(0,0,.5,.5,now=10.8)
    assert c.phase(19.) == "DOWN"
    c.start(20.)
    assert sum(map(len,c.samples.values())) == 0


def test_empty_not_fake_success():
    with pytest.raises(CalibrationError):
        Calibration().finish()


def test_identical_targets_unresolved():
    c = Calibration()
    for pose in POSES:
        for _ in range(10):
            c.add(0,0,.5,.5,pose=pose)
    assert len(c.finish()["unresolved_targets"]) == 4


def test_same_side_labels_rejected():
    c = Calibration()
    for pose in POSES:
        target = TARGETS[pose] if pose != "RIGHT" else (0,0,.7,.5)
        for _ in range(10):
            c.add(*target,pose=pose)
    assert set(c.finish()["unresolved_targets"]) == {"LEFT","RIGHT"}
