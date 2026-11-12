"""Finding toy, reference and stroke direction by themselves (autofind.py)."""
import numpy as np

from vtf.autofind import find, find_in_video


def test_still_picture_finds_nothing(tmp_path):
    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, (180, 320)).astype(np.uint8)
    assert find([img.copy() for _ in range(100)], 25.0) is None


def test_finger_video(tmp_path):
    from test_tracking import _finger_video

    path = tmp_path / "f.avi"
    truth, (W, H) = _finger_video(path, n=150)
    f = find_in_video(str(path), 0)
    assert f is not None and f.sure > 0.5 and abs(f.rate - 1.5) < 0.3
    x, y, w, h = f.toy
    assert x * W < truth[0][0] < (x + w) * W and y * H < truth[0][1] < (y + h) * H
    assert f.body is not None and len(f.outline) >= 3


def _machine_video(path, n=150, W=640, H=360):
    """A patterned machine rod pushing from the left along a slight diagonal into a patterned
    body on the right, which is pushed back a little. Returns the rod tip per frame."""
    import cv2

    rng = np.random.default_rng(9)
    bg = cv2.GaussianBlur(rng.integers(20, 60, (H, W)).astype(np.uint8), (0, 0), 4)
    body = cv2.GaussianBlur(rng.integers(120, 230, (200, 220)).astype(np.uint8), (0, 0), 2.0)
    rod = cv2.GaussianBlur(rng.integers(60, 200, (24, 260)).astype(np.uint8), (0, 0), 1.0)
    m = cv2.getRotationMatrix2D((0, 0), -np.degrees(np.arctan2(15, 60)), 1.0)
    rod_r = cv2.warpAffine(rod, m, (280, 90), borderValue=0)
    mask = cv2.warpAffine(np.full_like(rod, 255), m, (280, 90), borderValue=0) > 0
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (W, H))
    tips = []
    for i in range(n):
        ph = np.sin(2 * np.pi * 1.6 * i / 30)
        f = bg.copy()
        bx = int(400 + 4 * np.sin(2 * np.pi * 1.6 * i / 30 + 0.5))
        f[90:290, bx:bx + 220] = body
        tip = np.array([330 + 60 * ph, 190 + 15 * ph])
        x0, y0 = int(tip[0] - 260), int(tip[1] - 10)
        roi = f[y0:y0 + 90, x0:x0 + 280]
        mk = mask[:roi.shape[0], :roi.shape[1]]
        roi[mk] = rod_r[:roi.shape[0], :roi.shape[1]][mk]
        vw.write(cv2.cvtColor(f, cv2.COLOR_GRAY2BGR))
        tips.append(tip)
    vw.release()
    return np.array(tips), (W, H)


def test_machine_rod_body_and_arrow_out(tmp_path):
    path = tmp_path / "m.avi"
    tips, (W, H) = _machine_video(path)
    f = find_in_video(str(path), 0)
    assert f is not None
    x, y, w, h = f.toy
    assert x * W < tips[0][0] < (x + w) * W + 10                     # the rod up to its tip
    bx, by, bw, bh = f.body
    assert bx * W >= 380                                              # the reference is on the body
    x0, y0, x1, y1 = f.stroke
    dx, dy = (x1 - x0) * W, (y1 - y0) * H
    assert dx < 0 and abs(dy / dx - 0.25) < 0.1                       # out = towards the machine, along the rod
