"""Tracking preparation (<video>.vtf.json) and the batch using it."""
from vtf.analysis import AnalysisSettings, TrackSection
from vtf.batch import run_batch
from vtf.funscript import Funscript, funscript_path_for
from vtf.generator import GeneratorSettings
from vtf.prep import is_prepared, load_prep, prep_path, save_prep


def test_roundtrip_and_removal(tmp_path):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    secs = [TrackSection(0, ((0.1, 0.1, 0.2, 0.2), (0.5, 0.5, 0.2, 0.2, 15.0)), end_ms=4000,
                         calib_in_ms=250, calib_out_ms=750, keys=((2000, 1, (0.52, 0.5, 0.2, 0.2, 20.0)),)),
            TrackSection(5000, ((0.3, 0.3, 0.1, 0.1),))]
    assert save_prep(v, secs) == prep_path(v) == tmp_path / "a.vtf.json"
    back = load_prep(v)
    assert back == secs and is_prepared(v)
    prep_path(v).write_text("kaputt")
    assert load_prep(v) == [] and not is_prepared(v)
    save_prep(v, secs)
    save_prep(v, [])                                  # nothing left: file removed
    assert not prep_path(v).exists()


def test_batch_uses_prepared_tracking(tmp_path):
    import shutil

    from test_tracking import _video

    folder = tmp_path / "videos"
    folder.mkdir()
    path, a, b = _video(folder / "prepared.avi", seconds=3.0, pan=False)
    shutil.copy(path, folder / "plain.avi")
    save_prep(path, [TrackSection(0, (a, b), end_ms=1500)])
    logs = []
    res = run_batch(folder, AnalysisSettings(width=480, hwaccel="none"), GeneratorSettings(),
                    log=logs.append)
    assert len(res.done) == 2 and not res.failed
    prepared = Funscript.load(funscript_path_for(path)).actions
    plain = Funscript.load(funscript_path_for(folder / "plain.avi")).actions
    assert prepared and all(x.at < 1500 for x in prepared)    # only inside the tracked section
    assert plain and max(x.at for x in plain) > 1500          # the other one: whole video
    assert any("vorbereitet" in m for m in logs)
