"""Video probing and fast, downscaled grayscale frame decoding.

Decoding prefers an FFmpeg pipe (optionally with hardware decoding, e.g. VAAPI
on the RX 5700 XT) and falls back to OpenCV's VideoCapture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _parse_rate(rate: str) -> float:
    try:
        num, _, den = rate.partition("/")
        value = float(num) / float(den or 1)
        return value if value > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_ffprobe(path: str) -> VideoInfo | None:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration"
        ":stream_side_data=rotation:stream_tags=rotate:format=duration",
        "-of", "json", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True).stdout
        data = json.loads(out)
        stream = data["streams"][0]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return None
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    fps = _parse_rate(stream.get("avg_frame_rate", "0/0")) or _parse_rate(stream.get("r_frame_rate", "0/0"))
    rotation = 0
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation and "rotate" in (stream.get("tags") or {}):
        rotation = int(float(stream["tags"]["rotate"]))
    if abs(rotation) % 180 == 90:  # ffmpeg auto-rotates on decode
        width, height = height, width
    frames = int(stream.get("nb_frames", 0) or 0)
    if frames <= 0:
        duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
        frames = int(round(duration * fps))
    if width <= 0 or height <= 0 or fps <= 0:
        return None
    return VideoInfo(path, width, height, fps, frames)


def _probe_cv2(path: str) -> VideoInfo:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise IOError(f"Video kann nicht geöffnet werden: {path}")
    try:
        info = VideoInfo(
            path,
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            float(cap.get(cv2.CAP_PROP_FPS)) or 30.0,
            int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()
    return info


def probe(path: str | Path) -> VideoInfo:
    path = str(path)
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    info = _probe_ffprobe(path) if has_ffmpeg() else None
    return info or _probe_cv2(path)


def scaled_size(info: VideoInfo, width: int) -> tuple[int, int]:
    width = max(16, min(width, info.width)) // 2 * 2
    height = max(2, int(round(info.height * width / info.width / 2)) * 2)
    return width, height


VAAPI_DEVICE = "/dev/dri/renderD128"


def decode_attempts(hwaccel: str, has_vaapi_device: bool) -> list[str]:
    """Decoder setups to try, best first. "vaapi-gpu" decodes *and* scales on the
    GPU, so only the small analysis frames reach the CPU; with plain "-hwaccel"
    every frame comes back in full resolution and is scaled on the CPU."""
    if hwaccel == "vaapi":
        return ["vaapi-gpu", "vaapi", "none"]
    if hwaccel == "auto":
        return (["vaapi-gpu"] if has_vaapi_device else []) + ["auto", "none"]
    if hwaccel in ("", "none"):
        return ["none"]
    return [hwaccel, "none"]


def ffmpeg_command(path: str, w: int, h: int, mode: str, step: int) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    select = f"select=not(mod(n\\,{step}))," if step > 1 else ""
    if mode == "vaapi-gpu":
        cmd += ["-hwaccel", "vaapi", "-hwaccel_device", VAAPI_DEVICE, "-hwaccel_output_format", "vaapi"]
        vf = f"{select}scale_vaapi=w={w}:h={h}:format=nv12,hwdownload,format=nv12,format=gray"
    else:
        if mode and mode != "none":
            cmd += ["-hwaccel", mode]
        vf = f"{select}scale={w}:{h}:flags=area,format=gray"
    return cmd + ["-i", path, "-an", "-sn", "-dn", "-vf", vf, "-vsync", "0",
                  "-f", "rawvideo", "-pix_fmt", "gray", "-"]


def _iter_ffmpeg(info: VideoInfo, w: int, h: int, hwaccel: str, step: int) -> Iterator[np.ndarray]:
    cmd = ffmpeg_command(info.path, w, h, hwaccel, step)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=w * h * 8)
    frame_size = w * h
    produced = 0
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_size)
            if len(buf) < frame_size:
                break
            produced += 1
            yield np.frombuffer(buf, np.uint8).reshape(h, w)
    finally:
        proc.stdout.close()  # type: ignore[union-attr]
        proc.kill()
        proc.wait()
    if produced == 0:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"FFmpeg lieferte keine Frames: {err.strip()[:500]}")


def _iter_cv2(info: VideoInfo, w: int, h: int, step: int) -> Iterator[np.ndarray]:
    cap = cv2.VideoCapture(info.path)
    if not cap.isOpened():
        raise IOError(f"Video kann nicht geöffnet werden: {info.path}")
    try:
        idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                yield cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
            idx += 1
    finally:
        cap.release()


def iter_gray_frames(info: VideoInfo, width: int, hwaccel: str = "auto",
                     step: int = 1, backend: str = "auto") -> Iterator[np.ndarray]:
    """Yield downscaled grayscale frames (every ``step``-th frame).

    Tries the decoder setups from :func:`decode_attempts` in order; one that
    yields no frame at all (no GPU, codec not supported by it, ...) is skipped."""
    w, h = scaled_size(info, width)
    step = max(1, int(step))
    if backend in ("auto", "ffmpeg") and has_ffmpeg():
        attempts = decode_attempts(hwaccel, os.path.exists(VAAPI_DEVICE))
        for k, mode in enumerate(attempts):
            gen = _iter_ffmpeg(info, w, h, mode, step)
            try:
                first = next(gen)
            except (RuntimeError, StopIteration):
                if backend == "ffmpeg" and k == len(attempts) - 1:
                    raise
                continue
            yield first
            yield from gen
            return
        if backend == "ffmpeg":
            raise RuntimeError("FFmpeg konnte das Video nicht dekodieren")
    yield from _iter_cv2(info, w, h, step)