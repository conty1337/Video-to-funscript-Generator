"""Speed limit when generating, and the heatmap colours."""
from vtf.funscript import Action
from vtf.generator import GeneratorSettings, generate, limit_speed, speeds


def test_limit_speed_shortens_too_fast_strokes():
    acts = [Action(0, 0), Action(100, 100), Action(400, 0), Action(1400, 100)]
    out = limit_speed(acts, 500)                     # F21S: full stroke in 200 ms
    assert [a.at for a in out] == [0, 100, 400, 1400]   # timing kept
    assert max(speeds(out)) <= 500 + 10
    assert out[1].pos == 50                          # 100 ms * 500/s = 50 positions
    assert out[3].pos == 100                         # slow enough: unchanged
    assert limit_speed(acts, 0) == acts              # 0 = off


def test_generate_respects_max_speed(sine_video):
    from vtf.analysis import analyze_motion

    m = analyze_motion(sine_video)
    fast = generate(m, GeneratorSettings(min_interval_ms=50)).actions
    slow = generate(m, GeneratorSettings(min_interval_ms=50, max_speed=150)).actions
    assert max(speeds(fast)) > 150
    assert max(speeds(slow)) <= 150 + 10


def test_heat_colours_and_buckets():
    from vtf.gui.heatbar import bucket_speeds, heat_color

    slow, fast = heat_color(50), heat_color(800)
    assert fast.red() > fast.blue() and slow.red() < 100
    acts = [Action(0, 0), Action(1000, 10), Action(1100, 90)]   # slow, then fast
    b = bucket_speeds(acts, 2000, 20)
    assert b[2] < 50 and b[10] > 500 and b[19] == 0
