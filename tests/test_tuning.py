"""Profiles (profiles.py) and suggested settings (tuning.py)."""
import numpy as np

from vtf.analysis import MotionData
from vtf.generator import GeneratorSettings


def test_profiles_save_load_delete(tmp_path, monkeypatch):
    from vtf import profiles
    monkeypatch.setattr(profiles, "profiles_file", lambda: tmp_path / "p" / "profiles.json")
    assert profiles.load() == {}
    profiles.save("Reiten", {"sensitivity": 70})
    profiles.save("POV", {"sensitivity": 50})
    assert profiles.load() == {"Reiten": {"sensitivity": 70}, "POV": {"sensitivity": 50}}
    profiles.delete("Reiten")
    assert list(profiles.load()) == ["POV"]
    (tmp_path / "p" / "profiles.json").write_text("kaputt")
    assert profiles.load() == {}


def test_suggestion_finds_the_strokes_through_the_noise():
    """Noisy strokes at 1.2 per second: the best suggestion makes about 72 strokes a minute,
    with fewer findings than a twitchy setting, and is rated at least as well as it."""
    from vtf.tuning import suggest
    rng = np.random.default_rng(1)
    n = 3 * 60 * 30
    t = np.arange(n) / 30
    pos = np.sin(2 * np.pi * 1.2 * t) * 20
    dy = -np.diff(pos, prepend=0) + rng.normal(0, 3, n)
    m = MotionData("x", 30.0, np.zeros(n, np.float32), dy.astype(np.float32))
    twitchy = GeneratorSettings(sensitivity=100, smoothing_ms=0, min_interval_ms=0)
    best, current = suggest(m, twitchy)
    assert len(best) == 3 and best[0][0] >= best[1][0] >= best[2][0]
    assert abs(best[0][3] - 72) < 6
    assert best[0][0] > current[0] and best[0][2] <= current[2]
