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


def test_system_env_drops_bundled_libraries(monkeypatch):
    import sys

    from vtf.video import system_env
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_x/usr/lib-vtf/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" not in system_env()
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/mine")
    assert system_env()["LD_LIBRARY_PATH"] == "/opt/mine"
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")          # not frozen: untouched
    assert system_env()["LD_LIBRARY_PATH"] == "/usr/local/lib"


def test_report_names_decoder_and_failures(sine_video):
    info = probe(sine_video)
    rep = {}
    frames = list(iter_gray_frames(info, 160, "vaapi", report=rep))
    assert frames and rep["decoder"] in ("vaapi-gpu", "vaapi", "none")
    if rep["decoder"] == "none":                    # no GPU here: both GPU setups are reported
        assert [m for m, _e in rep["errors"]] == ["vaapi-gpu", "vaapi"]


def test_decoder_test_lists_every_setup(sine_video):
    from vtf.video import test_decoders
    res = test_decoders(probe(sine_video), 160, frames=10)
    assert [r["mode"] for r in res] == ["vaapi-gpu", "vaapi", "none"]
    soft = res[-1]
    assert soft["ok"] and soft["fps"] > 0
