"""Decoder setups: GPU scaling first, falling back without a GPU."""
from vtf.video import decode_attempts, ffmpeg_command, iter_gray_frames, probe


def test_decode_attempts_order():
    assert decode_attempts("vaapi", False) == ["vaapi-gpu", "vaapi", "none"]
    assert decode_attempts("auto", True) == ["vaapi-gpu", "auto", "none"]
    assert decode_attempts("auto", False) == ["auto", "none"]
    assert decode_attempts("none", True) == ["none"]
    assert decode_attempts("cuda", False) == ["cuda-gpu", "cuda", "none"]
    assert decode_attempts("auto", True, True) == ["cuda-gpu", "vaapi-gpu", "auto", "none"]
    assert decode_attempts("auto", False, True) == ["cuda-gpu", "auto", "none"]


def test_gpu_command_scales_on_the_gpu():
    cmd = " ".join(ffmpeg_command("v.mp4", 640, 360, "vaapi-gpu", 1))
    assert "-hwaccel_output_format vaapi" in cmd
    assert "scale_vaapi=w=640:h=360" in cmd and "hwdownload" in cmd
    nv = " ".join(ffmpeg_command("v.mp4", 640, 360, "cuda-gpu", 1))
    assert "-hwaccel cuda -hwaccel_output_format cuda" in nv
    assert "scale_cuda=w=640:h=360" in nv and "hwdownload" in nv
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


def test_decoder_test_lists_every_setup(sine_video, monkeypatch):
    from vtf import video
    from vtf.video import test_decoders
    monkeypatch.setattr(video, "has_nvidia", lambda: False)
    res = test_decoders(probe(sine_video), 160, frames=10)
    assert [r["mode"] for r in res] == ["vaapi-gpu", "vaapi", "none"]
    monkeypatch.setattr(video, "has_nvidia", lambda: True)
    assert [r["mode"] for r in test_decoders(probe(sine_video), 160, frames=10)][:2] == ["cuda-gpu", "cuda"]
    soft = res[-1]
    assert soft["ok"] and soft["fps"] > 0


def test_opencl_hint_for_nvidia(tmp_path, monkeypatch):
    from vtf import gpu
    loader = tmp_path / "libOpenCL.so.1"
    loader.touch()
    (tmp_path / "vendors").mkdir()
    monkeypatch.setattr(gpu, "opencl_device_name", lambda: None)
    monkeypatch.setattr(gpu, "_LOADER_PATHS", (str(loader),))
    monkeypatch.setattr(gpu, "_ICD_DIR", str(tmp_path / "vendors"))
    monkeypatch.setattr(gpu, "has_nvidia", lambda: True)
    assert "nvidia-cuda-libs" in gpu.opencl_hint()
    monkeypatch.setattr(gpu, "has_nvidia", lambda: False)
    assert "AMD" in gpu.opencl_hint()
