"""Music: beats from sound, live tracker, script, driver."""
import shutil
import sys
import time
import wave

import numpy as np
import pytest

from vtf import music as M
from vtf.device.simulated import SimulatedDevice
from vtf.device.sync import ScriptSync, SyncSettings


def clicks(bpm=120, seconds=8.0, start=0.3, noise=0.01, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(seconds * M.RATE)).astype(np.float32)
    n = int(0.03 * M.RATE)
    hit = np.sin(2 * np.pi * 80 * np.arange(n) / M.RATE) * np.exp(-np.arange(n) / (0.008 * M.RATE))
    t = start
    times = []
    while t < seconds - 0.1:
        i = int(t * M.RATE)
        x[i:i + n] += 0.8 * hit[: len(x[i:i + n])]
        times.append(t)
        t += 60 / bpm
    return x, times


def test_offline_beats_and_tempo():
    x, truth = clicks(120)
    beats = M.pick_beats(M.onset_envelope(x))
    assert abs(len(beats) - len(truth)) <= 1
    assert all(min(abs(b - t) for t in truth) < 0.05 for b in beats)
    assert M.tempo(beats) == pytest.approx(120, abs=3)


def test_live_tracker_matches():
    x, truth = clicks(100, seconds=10)
    tr = M.BeatTracker(0.5)
    found = []
    for i in range(0, len(x), 700):               # odd chunk sizes like a real stream
        found += tr.feed(x[i:i + 700])
    assert len(found) >= len(truth) - 2
    assert tr.tempo() == pytest.approx(100, abs=4)


def test_script_from_beats():
    acts = M.script_from_beats([1.0, 1.5, 2.0, 2.5, 6.0, 6.5], every=1, lo=10, hi=90)
    downs = [a.at for a in acts if a.pos == 10]
    assert downs == [1000, 1500, 2000, 2500, 6000, 6500]
    assert acts[0].pos == 90 and acts[0].at < 1000
    ups = [a.at for a in acts if a.pos == 90]
    assert 2750 in ups                             # the break stays up, it does not crawl down for 3.5 s
    assert all(b.at > a.at for a, b in zip(acts, acts[1:]))
    half = M.script_from_beats([1.0, 1.5, 2.0, 2.5], every=2)
    assert [a.at for a in half if a.pos == 0] == [1000, 2000]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_beats_from_file(tmp_path):
    x, truth = clicks(90, seconds=6)
    p = tmp_path / "c.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(M.RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    beats = M.beats_from_file(str(p))
    assert M.tempo(beats) == pytest.approx(90, abs=3)
    part = M.beats_from_file(str(p), start_s=2.0, duration_s=2.0)
    assert all(2.0 <= b <= 4.05 for b in part) and len(part) >= 2


def test_driver_predicts_beats():
    dev = SimulatedDevice()
    dev.connect()
    d = M.MusicDriver(ScriptSync(dev, SyncSettings(max_speed=0)))
    d.lo, d.hi = 20, 100
    d.start()
    for k in range(4):
        d.beat(1000 + 500 * k)
    assert d.bpm() == pytest.approx(120, abs=1)
    downs = [a.at for a in d.acts if a.pos == 20]
    assert 2500 + 500 in downs and 2500 + 1000 in downs      # next beats predicted
    d.tick(2600)
    d.tick(2500 + 3 * 500 + 10)                    # no beat for a while: rests at the top
    assert d.acts[-1].pos == 100 and d.period is None
    d.every = 2
    d.count = 0
    d.beat(9000)
    d.beat(9500)
    assert d.last == 9500 and d.count == 2
    d.stop()
    assert not d.running


def test_capture_with_fake_recorder(tmp_path):
    x, _truth = clicks(120, seconds=3)
    raw = tmp_path / "s.raw"
    raw.write_bytes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    script = f"import sys; sys.stdout.buffer.write(open({str(raw)!r}, 'rb').read()); sys.stdout.flush()"
    got, errors = [], []
    cap = M.AudioCapture(lambda s, t: got.append(len(s)), [sys.executable, "-c", script], errors.append)
    cap.start()
    for _ in range(100):
        if errors:
            break
        time.sleep(0.02)
    cap.stop()
    assert sum(got) == len(x) and errors
    with pytest.raises(RuntimeError):
        M.AudioCapture(lambda s, t: None, None).start()
