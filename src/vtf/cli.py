"""Command line interface: ``vtf analyze``, ``vtf batch``, ``vtf info``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, gpu
from .analysis import AnalysisSettings
from .batch import process_video, run_batch
from .generator import GeneratorSettings
from .video import has_ffmpeg


def _add_settings(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("Analyse")
    g.add_argument("--width", type=int, default=320, help="Analyse-Auflösung in px (Standard 320)")
    g.add_argument("--step", type=int, default=1, help="nur jeden n-ten Frame analysieren")
    g.add_argument("--roi", type=float, nargs=4, metavar=("X", "Y", "W", "H"),
                   help="Bildausschnitt, normiert 0..1")
    g.add_argument("--no-camera-comp", action="store_true", help="keine Kamerabewegungs-Kompensation")
    g.add_argument("--opencl", action="store_true", help="GPU per OpenCL nutzen")
    g.add_argument("--hwaccel", default="auto", help="FFmpeg-Hardwaredecoding: auto, vaapi, none")
    g = p.add_argument_group("Script")
    g.add_argument("--axis", choices=["vertical", "horizontal", "auto"], default="vertical")
    g.add_argument("--invert", action="store_true")
    g.add_argument("--sensitivity", type=int, default=60, help="0..100")
    g.add_argument("--smoothing", type=int, default=100, help="Glättung in ms")
    g.add_argument("--min-interval", type=int, default=120, help="Mindestabstand in ms")
    g.add_argument("--range", type=int, nargs=2, default=[0, 100], metavar=("MIN", "MAX"))
    g.add_argument("--full-stroke", action="store_true", help="immer vollen Hub ausgeben")


def _settings(a) -> tuple[AnalysisSettings, GeneratorSettings]:
    an = AnalysisSettings(width=a.width, frame_step=a.step, roi=tuple(a.roi) if a.roi else None,
                          compensate_camera=not a.no_camera_comp, use_opencl=a.opencl,
                          hwaccel=a.hwaccel)
    gen = GeneratorSettings(axis=a.axis, invert=a.invert, sensitivity=a.sensitivity,
                            smoothing_ms=a.smoothing, min_interval_ms=a.min_interval,
                            pos_min=a.range[0], pos_max=a.range[1], full_stroke=a.full_stroke)
    return an, gen


def _progress(f: float) -> None:
    sys.stderr.write(f"\r  {f * 100:5.1f} %")
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vtf", description="Video-to-Funscript Generator")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("analyze", help="ein Video analysieren")
    pa.add_argument("video")
    pa.add_argument("-o", "--output", help="Zieldatei (Standard: <video>.funscript)")
    _add_settings(pa)

    pb = sub.add_parser("batch", help="Ordner stapelweise verarbeiten")
    pb.add_argument("folder")
    pb.add_argument("--no-recursive", action="store_true")
    pb.add_argument("--overwrite", action="store_true", help="vorhandene Funscripts neu erstellen")
    _add_settings(pb)

    sub.add_parser("info", help="System/GPU-Informationen")

    a = p.parse_args(argv)
    if a.cmd == "info":
        print(f"vtf {__version__}")
        print(f"FFmpeg: {'ja' if has_ffmpeg() else 'nein'}")
        print(f"OpenCL: {gpu.opencl_device_name() or 'nicht verfügbar'}")
        hint = gpu.opencl_hint()
        if hint:
            print(f"        {hint}")
        return 0

    an, gen = _settings(a)
    if a.cmd == "analyze":
        out = process_video(a.video, an, gen, _progress, output=a.output)
        sys.stderr.write("\n")
        print(out)
        return 0

    res = run_batch(a.folder, an, gen, recursive=not a.no_recursive, overwrite=a.overwrite,
                    on_file=lambda i, n, v: sys.stderr.write(f"\n[{i + 1}/{n}] {v.name}\n"),
                    on_progress=_progress, log=lambda m: sys.stderr.write(f"\n{m}"))
    sys.stderr.write("\n")
    print(f"Fertig: {len(res.done)}, übersprungen: {len(res.skipped)}, Fehler: {len(res.failed)}")
    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
