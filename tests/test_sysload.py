"""Status bar load (sysload.py): reading /proc/stat and DRM fdinfo."""
from vtf.sysload import Load, cpu_times, decode_ns


def test_cpu_times_and_decoder_counter():
    busy, total = cpu_times("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4\n")
    assert (busy, total) == (150, 1000)
    info = ("pos:\t0\ndrm-driver:\tamdgpu\ndrm-pdev:\t0000:03:00.0\ndrm-client-id:\t42\n"
            "drm-engine-gfx:\t9000 ns\ndrm-engine-dec:\t1500 ns\ndrm-engine-enc:\t7 ns\n")
    assert decode_ns(info) == ("0000:03:00.0/42", 1500)
    assert decode_ns("pos:\t0\nflags:\t02\n") is None             # no DRM file


def test_sample_gives_cpu_load_here():
    load = Load()
    sum(i * i for i in range(200000))
    cpu, _gpu, _dec = load.sample()
    assert cpu is None or 0 <= cpu <= 100
