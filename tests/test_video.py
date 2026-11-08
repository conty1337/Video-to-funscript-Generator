"""Decoder setups: GPU scaling first, falling back without a GPU."""
from vtf.video import decode_attempts, ffmpeg_command, iter_gray_frames, probe


def test_decode_attempts_order():
    assert decode_attempts("vaapi", False) == ["vaapi-gpu", "vaapi", "none"]
    assert decode_attempts("auto", True) == ["vaapi-gpu", "auto", "none"]
    assert decode_attempts("auto", False) == ["auto", "none"]
    assert decode_attempts("none", True) == ["none"]


def test_gpu_command_scales_on_the_gpu():
    cmd = " ".join(ffmpeg_command("v.mp4", 640, 360, "vaapi-gpu", 1))
    assert "-hwaccel_output_format vaapi" in cmd
    assert "scale_vaapi=w=640:h=360" in cmd and "hwdownload" in cmd
    soft = " ".join(ffmpeg_command("v.mp4", 640, 360, "none", 2))
    assert "-hwaccel" not in soft and "scale=640:360" in soft and "select=" in soft


def test_vaapi_falls_back_without_gpu(sine_video):
    info = probe(sine_video)
    soft = list(iter_gray_frames(info, 160, "none"))
    gpu = list(iter_gray_frames(info, 160, "vaapi"))       # no GPU in CI: falls back
    assert len(gpu) == len(soft) > 0
    assert gpu[0].shape == soft[0].shape
