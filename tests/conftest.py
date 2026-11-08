import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def make_video(path: Path, seconds: float = 6.0, fps: int = 30, freq: float = 1.0,
               amp: int = 50, size=(320, 240)) -> Path:
    """Textured square moving up and down sinusoidally (peak = top at t=0.25/freq)."""
    w, h = size
    rng = np.random.default_rng(0)
    bg = (rng.random((h, w)) * 60).astype(np.uint8)
    tex = (rng.random((60, 60)) * 255).astype(np.uint8)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    for i in range(int(seconds * fps)):
        t = i / fps
        y = int(h / 2 - 30 - amp * np.sin(2 * np.pi * freq * t))
        frame = bg.copy()
        frame[y:y + 60, w // 2 - 30:w // 2 + 30] = tex
        vw.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    vw.release()
    return path


@pytest.fixture
def sine_video(tmp_path):
    return make_video(tmp_path / "sine.avi")
