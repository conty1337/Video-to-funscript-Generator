"""Scene changes (scenes.py), their place in the check and in the preparation file."""
import numpy as np

from vtf.funscript import Action
from vtf.quality import check
from vtf.scenes import cuts_in, find_cuts, needs_new_boxes


def _frames(n, level, rng):
    return [np.clip(rng.normal(level, 12, (90, 160)), 0, 255).astype(np.uint8) for _ in range(n)]


def test_a_hard_cut_is_found_noise_is_not():
    rng = np.random.default_rng(3)
    frames = _frames(30, 60, rng) + _frames(30, 190, rng)        # dark room, then bright
    assert cuts_in(frames, 100.0) == [3000]
    assert cuts_in(_frames(60, 100, rng), 100.0) == []            # the same scene, only noise


def test_cuts_inside_a_section_need_new_boxes_and_show_in_the_check():
    ranges = [(0, 0.0, 20000.0), (1, 30000.0, 60000.0)]
    # 10 s: inside section 1 -> needs boxes; 25 s: between sections; 30.5 s: a section starts there
    assert needs_new_boxes([10000, 25000, 30500], ranges) == [10000]
    acts = [Action(k * 500, 90 if k % 2 else 10) for k in range(60)]
    assert [i.kind for i in check(acts, scenes=[10000])] == ["scene"]
    assert check(acts, scenes=[10000], cuts=[(9000, 11000)]) == []   # cut away anyway


def test_find_cuts_runs_on_a_video(sine_video):
    assert find_cuts(sine_video, hwaccel="none") == []           # one moving pattern, no cut


def test_scenes_are_kept_with_the_cuts(tmp_path):
    from vtf.prep import load_cuts, load_scenes, save_cuts, save_scenes
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    assert load_scenes(v) is None                                # not looked for yet
    save_scenes(v, [])
    assert load_scenes(v) == []                                  # looked, none
    save_cuts(v, [(0, 1000)])
    save_scenes(v, [5000, 2000])
    assert load_scenes(v) == [2000, 5000] and load_cuts(v) == [(0, 1000)]
