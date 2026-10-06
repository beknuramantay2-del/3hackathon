import cv2
import numpy as np
import pytest
from proctor.core.geometry import MODEL_3D,PNP_IDX,head_pose,eye_gaze,TimeEMA

@pytest.mark.parametrize("yaw,pitch,roll",[(0,0,0),(25,0,0),(-25,0,0),(0,20,0),(0,-20,0),(0,0,25),(15,10,12)])
def test_pose_axes_projected(yaw,pitch,roll):
    x,y,z = np.radians((pitch,yaw,roll))
    rx = np.array([[1,0,0],[0,np.cos(x),-np.sin(x)],[0,np.sin(x),np.cos(x)]])
    ry = np.array([[np.cos(y),0,np.sin(y)],[0,1,0],[-np.sin(y),0,np.cos(y)]])
    rz = np.array([[np.cos(z),-np.sin(z),0],[np.sin(z),np.cos(z),0],[0,0,1]])
    rv,_ = cv2.Rodrigues(rz@ry@rx)
    cam = np.array([[640.,0,320],[0,640.,240],[0,0,1]])
    image,_ = cv2.projectPoints(MODEL_3D,rv,np.array([0.,0.,1800.]),cam,None)
    pts = np.zeros((478,2))
    pts[PNP_IDX] = image.reshape(-1,2)
    actual = head_pose(pts,640,480)
    assert actual is not None
    np.testing.assert_allclose(actual,(yaw,pitch,roll),atol=.01)


def eyes(rh=.5,rv=.5,lh=.5,lv=.5):
    p = np.zeros((478,2))
    for a,b,top,bot,iris,x,h,v in [(33,133,159,145,468,100,rh,rv),(362,263,386,374,473,200,lh,lv)]:
        p[a],p[b] = (x,100),(x+40,100)
        p[top],p[bot] = (x+20,94),(x+20,106)
        p[iris] = (x+40*h,94+12*v)
    return p


def test_eyes_separate_not_shared_midpoint():
    left,right,avg = eye_gaze(eyes(.25,.7,.75,.3))
    assert right == pytest.approx((.25,.7))
    assert left == pytest.approx((.75,.3))
    assert avg is None  # inconsistent eyes rejected


def test_eye_local_axes_roll_invariant():
    p = eyes(.7,.6,.7,.6)
    z = np.radians(27)
    rot = np.array([[np.cos(z),-np.sin(z)],[np.sin(z),np.cos(z)]])
    rolled = p@rot.T+[40,50]
    assert eye_gaze(rolled)[2] == pytest.approx((.7,.6))


def test_blink_unknown_and_one_eye_fallback():
    p = eyes()
    p[386],p[374] = (220,100),(220,100)
    left,right,avg = eye_gaze(p)
    assert left is None and avg == pytest.approx((.5,.5))
    p[159],p[145] = (120,100),(120,100)
    assert eye_gaze(p)[2] is None


def test_ema_reset_after_loss():
    ema = TimeEMA(.08)
    assert ema.update((0,0),0) == (0,0)
    assert 0 < ema.update((1,1),.05)[0] < 1
    assert ema.update((2,2),1) == (2,2)


def test_invalid_pnp_unknown():
    assert head_pose(np.zeros((478,2)),640,480) is None
