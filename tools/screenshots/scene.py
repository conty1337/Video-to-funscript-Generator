"""Synthetic demo scenes for the README screenshots (no real footage)."""
import cv2
import numpy as np

W, H, FPS = 960, 540, 30


def _tex(rng, h, w, base, spread=55, blur=1.6):
    t = rng.normal(0, spread, (h, w, 3))
    t = cv2.GaussianBlur(t, (0, 0), blur)
    return np.clip(t + base, 0, 255).astype(np.float32)


def _capsule_mask(h, w):
    m = np.zeros((h, w), np.uint8)
    r = h // 2
    cv2.rectangle(m, (r, 0), (w - r, h - 1), 255, -1)
    cv2.circle(m, (w - r, r), r, 255, -1)
    cv2.circle(m, (r, r), r, 255, -1)
    return cv2.GaussianBlur(m, (0, 0), 1.5).astype(np.float32) / 255


def _blob_mask(h, w, seed):
    rng = np.random.default_rng(seed)
    m = np.zeros((h, w), np.uint8)
    pts = []
    for k in range(14):
        a = 2 * np.pi * k / 14
        r = 0.42 + 0.06 * rng.random()
        pts.append((w / 2 + r * w * np.cos(a), h / 2 + r * h * np.sin(a)))
    cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
    return cv2.GaussianBlur(m, (0, 0), 6).astype(np.float32) / 255


def _paste(f, img, mask, x, y):
    h, w = mask.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sub = (slice(y0 - y, y1 - y), slice(x0 - x, x1 - x))
    a = mask[sub][..., None]
    f[y0:y1, x0:x1] = f[y0:y1, x0:x1] * (1 - a) + img[sub] * a


def scene(path, seconds=12.0, seed=0, rate=0.85, warm=True, quiet_from=None):
    """A rod (toy) thrusting from the left along a slight diagonal into a calm textured body;
    a still 'finger' lies right next to the rod's path. Deep strokes, then shorter ones.
    From `quiet_from` (s) on the rod rests (nothing happens: room for a cut)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    bg = np.zeros((H, W, 3), np.float32)
    if warm:
        bg[..., 2] = 52 + 28 * (1 - xx / W)
        bg[..., 1] = 36 + 10 * yy / H
        bg[..., 0] = 40 + 30 * xx / W
    else:
        bg[..., 2] = 34 + 14 * yy / H
        bg[..., 1] = 42 + 18 * xx / W
        bg[..., 0] = 58 + 30 * (1 - yy / H)
    bg += cv2.GaussianBlur(rng.normal(0, 14, (H, W, 3)), (0, 0), 3)
    body = _tex(rng, 380, 360, (150, 120, 92) if warm else (170, 140, 110), 45, 2.2)
    body_m = _blob_mask(380, 360, seed + 1)
    rod = _tex(rng, 64, 360, (70, 150, 220) if warm else (90, 190, 210), 60, 1.3)
    rod_m = _capsule_mask(64, 360)
    fin = _tex(rng, 46, 120, (110, 140, 190), 6, 3.0)
    fin_m = _capsule_mask(46, 120)
    ang = -14.0                                         # rod tilt (degrees), points down-right
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    phase = 0.0
    for i in range(int(seconds * FPS)):
        t = i / FPS
        moving = quiet_from is None or t < quiet_from
        if moving:
            phase += 2 * np.pi * rate / FPS
        depth = 160 if (t % 6) < 3.5 else 75
        u = (1 - np.cos(phase)) / 2                     # 0 = out, 1 = in
        f = bg.copy()
        sway = 3 * np.sin(phase + 0.6)
        _paste(f, body, body_m, int(540 + sway), 80)
        tip_x = 470 + depth * u
        tip_y = 238 + depth * u * np.tan(np.radians(-ang)) * 0.9
        M = cv2.getRotationMatrix2D((180, 32), ang, 1.0)
        big = cv2.warpAffine(rod, M, (360, 64), borderMode=cv2.BORDER_REPLICATE)
        bigm = cv2.warpAffine(rod_m, M, (360, 64))
        canvas = np.zeros((160, 400, 3), np.float32); cm = np.zeros((160, 400), np.float32)
        M2 = cv2.getRotationMatrix2D((200, 80), ang, 1.0)
        rod_pad = np.zeros((160, 400, 3), np.float32); m_pad = np.zeros((160, 400), np.float32)
        rod_pad[48:112, 20:380] = rod; m_pad[48:112, 20:380] = rod_m
        canvas = cv2.warpAffine(rod_pad, M2, (400, 160))
        cm = cv2.warpAffine(m_pad, M2, (400, 160))
        _paste(f, canvas, cm, int(tip_x - 390), int(tip_y - 80 - 40))
        _paste(f, fin, fin_m, 110, 430)                 # the still finger below the rod
        vw.write(np.clip(f, 0, 255).astype(np.uint8))
    vw.release()
