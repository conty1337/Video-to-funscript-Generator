"""Video probing and fast, downscaled grayscale frame decoding.

Decoding prefers an FFmpeg pipe (optionally with hardware decoding, e.g. VAAPI
on the RX 5700 XT) and falls back to OpenCV's VideoCapture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from .gpu import has_nvidia
from .i18n import tr


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


def system_env() -> dict[str, str]:
    """Environment for the system's own tools (ffmpeg, ffprobe).

    Inside the AppImage, PyInstaller points LD_LIBRARY_PATH at the bundled
    libraries (built on Ubuntu 22.04). The system ffmpeg must not load those:
    older libssl/glib/X11 can keep it from starting or break its VAAPI driver,
    and the app would silently fall back to decoding on the CPU."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) or "LD_LIBRARY_PATH_ORIG" in env:
        if "LD_LIBRARY_PATH_ORIG" in env:
            env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


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
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True,
                             env=system_env()).stdout
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
        raise IOError(tr("Video kann nicht geöffnet werden: {0}", path))
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


def decode_attempts(hwaccel: str, has_vaapi_device: bool, has_nvidia_gpu: bool = False) -> list[str]:
    """Decoder setups to try, best first. "vaapi-gpu"/"cuda-gpu" decode *and* scale
    on the GPU, so only the small analysis frames reach the CPU; with plain "-hwaccel"
    every frame comes back in full resolution and is scaled on the CPU."""
    if hwaccel == "vaapi":
        return ["vaapi-gpu", "vaapi", "none"]
    if hwaccel == "cuda":
        return ["cuda-gpu", "cuda", "none"]
    if hwaccel == "auto":
        return ((["cuda-gpu"] if has_nvidia_gpu else []) + (["vaapi-gpu"] if has_vaapi_device else [])
                + ["auto", "none"])
    if hwaccel in ("", "none"):
        return ["none"]
    return [hwaccel, "none"]


def ffmpeg_command(path: str, w: int, h: int, mode: str, step: int) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    select = f"select=not(mod(n\\,{step}))," if step > 1 else ""
    if mode == "vaapi-gpu":
        cmd += ["-hwaccel", "vaapi", "-hwaccel_device", VAAPI_DEVICE, "-hwaccel_output_format", "vaapi"]
        vf = f"{select}scale_vaapi=w={w}:h={h}:format=nv12,hwdownload,format=nv12,format=gray"
    elif mode == "cuda-gpu":
        cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        vf = f"{select}scale_cuda=w={w}:h={h}:format=nv12,hwdownload,format=nv12,format=gray"
    else:
        if mode and mode != "none":
            cmd += ["-hwaccel", mode]
        vf = f"{select}scale={w}:{h}:flags=area,format=gray"
    return cmd + ["-i", path, "-an", "-sn", "-dn", "-vf", vf, "-vsync", "0",
                  "-f", "rawvideo", "-pix_fmt", "gray", "-"]


def _iter_ffmpeg(info: VideoInfo, w: int, h: int, hwaccel: str, step: int) -> Iterator[np.ndarray]:
    cmd = ffmpeg_command(info.path, w, h, hwaccel, step)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=w * h * 8,
                            env=system_env())
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
        raise RuntimeError(tr("FFmpeg lieferte keine Frames: {0}", err.strip()[:500]))


def _iter_cv2(info: VideoInfo, w: int, h: int, step: int) -> Iterator[np.ndarray]:
    cap = cv2.VideoCapture(info.path)
    if not cap.isOpened():
        raise IOError(tr("Video kann nicht geöffnet werden: {0}", info.path))
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


DECODER_LABELS = {
    "cuda-gpu": tr("NVIDIA-Grafikkarte (NVDEC: dekodieren und verkleinern)"),
    "cuda": tr("NVIDIA-Grafikkarte dekodiert, CPU verkleinert (NVDEC)"),
    "vaapi-gpu": tr("Grafikkarte (VAAPI: dekodieren und verkleinern)"),
    "vaapi": tr("Grafikkarte dekodiert, CPU verkleinert (VAAPI)"),
    "auto": tr("FFmpeg mit automatischer Hardware-Beschleunigung"),
    "none": tr("CPU (FFmpeg)"),
    "opencv": tr("CPU (OpenCV, ohne FFmpeg)"),
}


def iter_gray_frames(info: VideoInfo, width: int, hwaccel: str = "auto",
                     step: int = 1, backend: str = "auto",
                     report: dict | None = None) -> Iterator[np.ndarray]:
    """Yield downscaled grayscale frames (every ``step``-th frame).

    Tries the decoder setups from :func:`decode_attempts` in order; one that
    yields no frame at all (no GPU, codec not supported by it, ...) is skipped.
    ``report`` (optional) receives the decoder used and why others failed."""
    w, h = scaled_size(info, width)
    step = max(1, int(step))
    if report is not None:
        report.setdefault("errors", [])
    if backend in ("auto", "ffmpeg") and not has_ffmpeg() and report is not None:
        report["errors"].append(("ffmpeg", tr("ffmpeg/ffprobe nicht gefunden")))
    if backend in ("auto", "ffmpeg") and has_ffmpeg():
        attempts = decode_attempts(hwaccel, os.path.exists(VAAPI_DEVICE), has_nvidia())
        for k, mode in enumerate(attempts):
            gen = _iter_ffmpeg(info, w, h, mode, step)
            try:
                first = next(gen)
            except (RuntimeError, StopIteration) as e:
                if report is not None:
                    report["errors"].append((mode, str(e) or tr("keine Bilder")))
                if backend == "ffmpeg" and k == len(attempts) - 1:
                    raise
                continue
            if report is not None:
                report["decoder"] = mode
            yield first
            yield from gen
            return
        if backend == "ffmpeg":
            raise RuntimeError(tr("FFmpeg konnte das Video nicht dekodieren"))
    if report is not None:
        report["decoder"] = "opencv"
    yield from _iter_cv2(info, w, h, step)


def test_decoders(info: VideoInfo, width: int = 640, frames: int = 90) -> list[dict]:
    """Try every decoder setup on the first frames of the video:
    [{"mode", "label", "ok", "fps", "error"}, ...] (for the help menu)."""
    w, h = scaled_size(info, width)
    gpu = (["cuda-gpu", "cuda"] if has_nvidia() else []) + ["vaapi-gpu", "vaapi"]
    modes = gpu + ["none"] if has_ffmpeg() else []
    out = []
    for mode in modes:
        t0 = time.perf_counter()
        n, err = 0, ""
        try:
            for _ in _iter_ffmpeg(info, w, h, mode, 1):
                n += 1
                if n >= frames:
                    break
        except RuntimeError as e:
            err = str(e)
        dt = max(time.perf_counter() - t0, 1e-6)
        out.append({"mode": mode, "label": DECODER_LABELS[mode], "ok": n > 0,
                    "fps": n / dt if n else 0.0, "error": err})
    if not has_ffmpeg():
        out.append({"mode": "ffmpeg", "label": "FFmpeg", "ok": False, "fps": 0.0,
                    "error": tr("ffmpeg/ffprobe nicht gefunden – sudo dnf install ffmpeg")})
    return out