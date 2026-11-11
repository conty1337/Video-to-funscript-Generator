"""Measure how exactly tracking follows a known motion (fixed camera).

Renders synthetic videos with sub-pixel motion of a textured object in front of a
static textured background, runs the real analysis (mode "track") and the script
generator, and compares with the ground truth:

* track_rms   RMS error of the tracked centre, in pixels of the original video
* jitter      frame-to-frame noise of that error (what makes the script shaky)
* timing      mean |time difference| of turning points script vs truth, ms
* height      mean |difference| of stroke heights script vs truth (0..100 scale,
              truth scaled like the script: by the local range)
* missed      true turning points without a script point within 150 ms
* falsch      script points that belong to no true turning point (wobble taken for strokes)

Run:  python tools/measure_tracking.py [--width 640] [--keep DIR]
"""

from __future__ import annotations

import argparse
import itertools
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vtf.analysis import AnalysisSettings, TrackSection, analyze_motion
from vtf.generator import GeneratorSettings, generate

FPS = 30


@dataclass
class Scenario:
    name: str
    size: tuple[int, int]               # video width, height
    seconds: float
    motion: object                      # t (s, array) -> vertical offset in px (positive = up)
    contrast: float = 150.0             # grey levels of the object texture (skin: little)


def _chirp(t, a=80.0):
    """Strokes getting faster: 0.8 Hz -> 2.6 Hz."""
    f0, f1, T = 0.8, 2.6, 20.0
    phase = 2 * np.pi * (f0 * t + (f1 - f0) * t * t / (2 * T))
    return a * np.sin(phase)


def _mixed(t):
    """Big and small strokes taking turns every 4 s."""
    a = 36.0 + 24.0 * np.tanh(4 * np.sin(2 * np.pi * t / 8))       # 60 <-> 12, soft change
    return a * np.sin(2 * np.pi * 1.37 * t)


def _pauses(t):
    """Strokes for 3 s, then 2 s standing still (only image noise), and again."""
    on = np.clip(np.sin(np.pi * np.clip((t % 5) / 3, 0, 1)) * 4, 0, 1)    # soft start and stop
    on = np.where(t % 5 < 3, on, 0.0)
    return 50.0 * on * np.sin(2 * np.pi * 1.6 * t)


SCENARIOS = [
    Scenario("schneller werdend, 720p", (1280, 720), 20.0, _chirp),
    Scenario("klein (±6 px), 720p", (1280, 720), 12.0, lambda t: 6.0 * np.sin(2 * np.pi * 1.93 * t)),
    Scenario("groß/klein im Wechsel, 1080p", (1920, 1080), 16.0, _mixed),
    Scenario("wenig Muster (Haut), 1080p", (1920, 1080), 16.0, _mixed, contrast=25.0),
    Scenario("mit Pausen, 720p", (1280, 720), 20.0, _pauses),
    Scenario("schnell (3 Hz, ±150 px), 1080p", (1920, 1080), 10.0,
             lambda t: 100.0 * np.sin(2 * np.pi * 3.0 * t)),
]
SHUTTER = 0.5          # part of a frame the shutter is open: motion blur
SUB = 6                # sub-frames averaged for the blur


def _texture(rng, h, w, blur):
    tex = rng.random((h, w)).astype(np.float32)
    tex = cv2.GaussianBlur(tex, (0, 0), blur)
    tex = (tex - tex.min()) / max(1e-6, np.ptp(tex))
    return tex


def render(sc: Scenario, path: Path) -> tuple[np.ndarray, tuple]:
    """Write the video, return (truth y of the object centre per frame in px, box normalized)."""
    w, h = sc.size
    rng = np.random.default_rng(1)
    k = h / 720
    bg = (40 + 80 * _texture(rng, h, w, 3 * k)).astype(np.float32)
    ow, oh = int(150 * k), int(150 * k)
    obj = (110 + sc.contrast * _texture(rng, oh, ow, 3 * k)).astype(np.float32)
    cx, cy = w * 0.5, h * 0.5
    n = int(sc.seconds * FPS)
    t = np.arange(n) / FPS
    off = np.asarray(sc.motion(t), np.float64) * k
    ys = cy - off
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (w, h))
    mask = np.ones((oh, ow), np.float32)
    for i in range(n):
        # sub-pixel placement (bilinear) averaged over the open shutter: motion blur
        frame = np.zeros((h, w), np.float32)
        for j in range(SUB):
            tj = (i + (j / (SUB - 1) - 0.5) * SHUTTER) / FPS
            y = cy - float(np.asarray(sc.motion(np.array([tj])))[0]) * k
            m = np.float32([[1, 0, cx - ow / 2], [0, 1, y - oh / 2]])
            o = cv2.warpAffine(obj, m, (w, h), flags=cv2.INTER_LINEAR)
            a = cv2.warpAffine(mask, m, (w, h), flags=cv2.INTER_LINEAR)
            frame += bg * (1 - a) + o * a
        frame /= SUB
        frame *= 1 + 0.02 * np.sin(i * 0.7)                          # light flicker
        frame += rng.normal(0, 3.0, frame.shape)                     # sensor noise
        f8 = np.clip(frame, 0, 255).astype(np.uint8)
        vw.write(cv2.cvtColor(f8, cv2.COLOR_GRAY2BGR))
    vw.release()
    # the box covers the inner part of the object at frame 0
    bw, bh = ow * 0.8, oh * 0.8
    box = ((cx - bw / 2) / w, (ys[0] - bh / 2) / h, bw / w, bh / h)
    return ys, box


def _extrema(y: np.ndarray) -> list[int]:
    d = np.sign(np.diff(y))
    return [i for i in range(1, len(y) - 1) if d[i - 1] != d[i] and d[i - 1] != 0 and d[i] != 0]


def _true_turns(ts: np.ndarray, y: np.ndarray) -> list[tuple[float, float]]:
    """Turning points of the truth with sub-frame time (parabola through 3 samples)."""
    out = []
    for i in _extrema(y):
        a, b, c = y[i - 1], y[i], y[i + 1]
        den = a - 2 * b + c
        f = 0.5 * (a - c) / den if den != 0 else 0.0
        out.append((ts[i] + f * (ts[1] - ts[0]), b))
    return out


def measure(sc: Scenario, workdir: Path, width: int, gen: GeneratorSettings, shake: float = 0.0) -> dict:
    """shake: extra random jitter (px of the analysis frame) on the tracked motion, like a
    tracker that wobbles on a real video."""
    path = workdir / f"scene{SCENARIOS.index(sc)}_{sc.size[1]}p.avi"
    ys, box = render(sc, path)
    h = sc.size[1]
    sec = TrackSection(0, (box,))
    s = AnalysisSettings(mode="track", track_sections=(sec,), width=width, hwaccel="none")
    t0 = time.perf_counter()
    m = analyze_motion(path, s)
    secs = time.perf_counter() - t0
    if shake:
        rng = np.random.default_rng(7)
        m.dy = (m.dy + np.diff(rng.normal(0, shake, len(m.dy) + 1))).astype(np.float32)
    tracked = m.tracks[:, 0, 1].astype(np.float64) * h
    n = min(len(tracked), len(ys))
    err = tracked[:n] - ys[:n]
    err -= np.nanmean(err)
    rms = float(np.sqrt(np.nanmean(err ** 2)))
    jitter = float(np.sqrt(np.nanmean(np.diff(err) ** 2)))

    acts = generate(m, gen, (sec,)).actions
    ts = np.arange(len(ys)) / FPS * 1000
    truth_pos = -ys                                     # up = higher position
    turns = _true_turns(ts, truth_pos)
    a_t = np.array([a.at for a in acts], np.float64)
    timing, missed, pairs = [], 0, []
    for tt, v in turns:
        if not len(a_t):
            missed += 1
            continue
        j = int(np.argmin(np.abs(a_t - tt)))
        if abs(a_t[j] - tt) <= 150:
            timing.append(abs(a_t[j] - tt))
            pairs.append((j, tt, v))
        else:
            missed += 1
    # stroke heights: neighbouring matched turning points
    heights = []
    win = gen.normalize_window_ms
    for (j0, t0_, v0), (j1, t1_, v1) in itertools.pairwise(pairs):
        if j1 != j0 + 1:
            continue
        sel = (ts > t0_ - win / 2) & (ts < t1_ + win / 2)
        span = np.ptp(truth_pos[sel]) or 1.0
        heights.append(abs(abs(acts[j1].pos - acts[j0].pos) - 100 * abs(v1 - v0) / span))
    used = {j for j, _t, _v in pairs}
    extra = len(acts) - len(used)
    return {"extra": extra, "track_rms": rms, "jitter": jitter, "timing": float(np.mean(timing)) if timing else float("nan"),
            "height": float(np.mean(heights)) if heights else float("nan"), "missed": missed,
            "turns": len(turns), "secs": secs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=640, help="analysis width (as in the app)")
    ap.add_argument("--keep", type=Path, help="keep the rendered videos here")
    ap.add_argument("--shake", type=float, default=0.0, help="extra tracker jitter in px (analysis frame)")
    args = ap.parse_args(argv)
    gen = GeneratorSettings(axis="vertical")
    with tempfile.TemporaryDirectory() as tmp:
        wd = args.keep or Path(tmp)
        wd.mkdir(parents=True, exist_ok=True)
        print(f"{'Szenario':32} {'Track px':>9} {'Zittern':>8} {'Timing ms':>10} {'Höhe':>6} "
              f"{'verpasst':>9} {'falsch':>7} {'Zeit s':>7}")
        for sc in SCENARIOS:
            r = measure(sc, wd, args.width, gen, args.shake)
            print(f"{sc.name:32} {r['track_rms']:9.2f} {r['jitter']:8.2f} {r['timing']:10.1f} "
                  f"{r['height']:6.1f} {r['missed']:4d}/{r['turns']:<4d} {r['extra']:7d} {r['secs']:7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
