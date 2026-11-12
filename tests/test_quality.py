"""Script check (quality.py)."""
from vtf.funscript import Action
from vtf.quality import check, summary


def _strokes(start, n, period=500, lo=10, hi=90):
    return [Action(start + k * period, hi if k % 2 else lo) for k in range(n)]


def test_clean_script_has_nothing():
    acts = _strokes(0, 60)
    assert check(acts) == [] and "nichts" in summary([])


def test_finds_each_kind():
    acts = _strokes(0, 30)                                       # 0 .. 14.5 s regular
    acts += [Action(14600, 10)]                                  # too fast: 80 positions in 100 ms
    acts += _strokes(20600, 30)                                  # nothing from 14.6 to 20.6 s
    acts += [Action(35700, 50), Action(35800, 55), Action(35900, 50), Action(36000, 56),
             Action(36100, 51), Action(36200, 55), Action(36300, 50)]          # trembling
    acts += _strokes(37000, 20)
    acts += [Action(47000, 40), Action(47500, 55)]               # one small stroke
    acts += _strokes(48000, 20)
    kinds = {i.kind for i in check(acts, lost=[(5000, 6000)])}
    assert {"fast", "gap", "jitter", "small", "lost"} <= kinds, kinds


def test_slow_stroke_is_a_beat_break_and_cuts_are_left_out():
    acts = _strokes(0, 20)
    acts += [Action(9500 + 1800, 10)]                            # one stroke 1.8 s instead of 0.5 s
    acts += _strokes(12300, 20, lo=10, hi=90)
    found = check(acts)
    assert any(i.kind == "beat" for i in found)
    assert not any(i.kind == "beat" for i in check(acts, cuts=[(9000, 12500)]))
    # a gap inside a cut is no finding either
    gap = _strokes(0, 20) + _strokes(20000, 20)
    assert any(i.kind == "gap" for i in check(gap))
    assert not any(i.kind == "gap" for i in check(gap, cuts=[(9000, 20500)]))


def test_device_speed_limit_is_used():
    acts = [Action(0, 0), Action(250, 100), Action(500, 0)]      # 400 positions per second
    assert not any(i.kind == "fast" for i in check(acts))        # default 500
    assert any(i.kind == "fast" for i in check(acts, max_speed=300))
