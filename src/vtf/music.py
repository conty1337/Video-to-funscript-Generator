"""Music: find the beats, live from the system sound or from a video's sound track.

Beats come from the onset strength (spectral flux, weighted to bass and mids) with
an adaptive threshold. Live sound is captured with the system's own tools
(`parec` from PulseAudio/PipeWire or `pw-record`), so no audio library is needed.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from .funscript import Action, position_at  # noqa: F401  (position_at used by the GUI)

RATE = 22050
FRAME = 1024
HOP = 512
HOP_S = HOP / RATE
MIN_GAP_S = 0.22            # never two beats closer than this (≈ 270 per minute)


def _weights() -> np.ndarray:
    freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
    w = np.where(freqs < 200, 2.0, np.where(freqs < 4000, 1.0, 0.25))   # bass counts most
    return w.astype(np.float32)


WEIGHTS = _weights()
WINDOW = np.hanning(FRAME).astype(np.float32)


def onset_envelope(samples: np.ndarray) -> np.ndarray:
    """Onset strength per hop (positive spectral flux on a log spectrum)."""
    x = np.asarray(samples, np.float32)
    if len(x) < FRAME:
        return np.zeros(0, np.float32)
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    out = np.empty(n, np.float32)
    prev = None
    for k in range(0, n, 2048):                   # in blocks: long sound tracks stay small in memory
        frames = x[idx[k:k + 2048]] * WINDOW
        mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10).astype(np.float32)
        if prev is not None:
            mag_prev = np.vstack([prev[None, :], mag[:-1]])
        else:
            mag_prev = np.vstack([mag[:1], mag[:-1]])
        out[k:k + len(mag)] = (np.maximum(mag - mag_prev, 0) * WEIGHTS).sum(axis=1)
        prev = mag[-1]
    return out


def pick_beats(env: np.ndarray, sensitivity: float = 0.5, hop_s: float = HOP_S,
               min_gap_s: float = MIN_GAP_S) -> list[float]:
    """Times (s) of the beats: peaks above a moving threshold. Higher sensitivity
    finds quieter beats too."""
    if len(env) < 3:
        return []
    k = 1.6 - 1.2 * max(0.0, min(1.0, sensitivity))
    win = max(3, int(1.0 / hop_s))               # ±0.5 s
    kernel = np.ones(win, np.float32) / win
    mean = np.convolve(env, kernel, mode="same")
    sq = np.convolve(env * env, kernel, mode="same")
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    thr = mean + k * std + 1e-3
    gap = max(1, int(min_gap_s / hop_s))
    beats, last = [], -gap
    for i in range(1, len(env) - 1):
        if env[i] > thr[i] and env[i] >= env[i - 1] and env[i] >= env[i + 1] and i - last >= gap:
            lo, hi = max(0, i - gap), min(len(env), i + gap + 1)
            if env[i] >= env[lo:hi].max():
                beats.append(i * hop_s)
                last = i
    return beats


def tempo(beats: list[float]) -> float | None:
    """Beats per minute from the typical distance between beats."""
    if len(beats) < 3:
        return None
    d = np.diff(beats)
    d = d[(d > MIN_GAP_S) & (d < 2.0)]
    return float(60.0 / np.median(d)) if len(d) else None


def script_from_beats(beats_s: list[float], every: int = 1, lo: int = 0, hi: int = 100,
                      offset_ms: float = 0.0) -> list[Action]:
    """Down on every (or every 2nd, …) beat, up halfway to the next. Long gaps
    (breaks in the music) stay up."""
    times = [b * 1000 + offset_ms for b in beats_s[::max(1, every)]]
    if not times:
        return []
    gaps = np.diff(times) if len(times) > 1 else np.array([500.0])
    typical = float(np.median(gaps)) if len(gaps) else 500.0
    acts = [Action(int(round(max(0.0, times[0] - min(250.0, typical / 2)))), hi)]
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else t + typical
        gap = nxt - t
        up = t + (gap / 2 if gap <= 2 * typical else typical / 2)
        if t > acts[-1].at:
            acts.append(Action(int(round(t)), lo))
        if up > acts[-1].at:
            acts.append(Action(int(round(up)), hi))
    return acts


def decode_audio(path: str, start_s: float = 0.0, duration_s: float | None = None) -> np.ndarray:
    """Mono float samples at RATE from any file ffmpeg can read."""
    from .video import system_env

    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", path]
    if duration_s is not None:
        cmd += ["-t", f"{duration_s:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=600, env=system_env())
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError(out.stderr.decode(errors="replace").strip() or "ffmpeg-Fehler")
    return np.frombuffer(out.stdout, np.int16).astype(np.float32) / 32768


def beats_from_file(path: str, sensitivity: float = 0.5, start_s: float = 0.0,
                    duration_s: float | None = None) -> list[float]:
    """Beat times (s, from the start of the file)."""
    x = decode_audio(path, start_s, duration_s)
    return [start_s + b for b in pick_beats(onset_envelope(x), sensitivity)]


# ------------------------------------------------------------ live
class BeatTracker:
    """Beats from a stream of samples, frame by frame (for live sound)."""

    HISTORY_S = 3.0

    def __init__(self, sensitivity: float = 0.5):
        self.sensitivity = sensitivity
        self.buf = np.zeros(0, np.float32)
        self.prev_mag: np.ndarray | None = None
        self.flux: deque = deque(maxlen=int(self.HISTORY_S / HOP_S))
        self.frames = 0                  # hops processed
        self.last_beat = -1e9
        self.level = 0.0                 # loudness 0..1 of the last frame (for a meter)
        self.beats: deque = deque(maxlen=32)

    def feed(self, samples: np.ndarray) -> list[float]:
        """Add samples (float -1..1); returns beat times (s since the stream started)."""
        self.buf = np.concatenate([self.buf, np.asarray(samples, np.float32)])
        found = []
        while len(self.buf) >= FRAME:
            frame = self.buf[:FRAME]
            self.buf = self.buf[HOP:]
            mag = np.log1p(np.abs(np.fft.rfft(frame * WINDOW)) * 10)
            f = 0.0 if self.prev_mag is None else float((np.maximum(mag - self.prev_mag, 0) * WEIGHTS).sum())
            self.prev_mag = mag
            self.level = min(1.0, float(np.sqrt(np.mean(frame * frame))) * 4)
            self.flux.append(f)
            self.frames += 1
            if len(self.flux) >= 4:
                hist = np.fromiter(self.flux, np.float32)
                k = 1.6 - 1.2 * max(0.0, min(1.0, self.sensitivity))
                thr = hist.mean() + k * hist.std() + 1e-3
                a, b, c = hist[-3], hist[-2], hist[-1]
                t = (self.frames - 2) * HOP_S
                if b > thr and b >= a and b >= c and t - self.last_beat >= MIN_GAP_S:
                    self.last_beat = t
                    self.beats.append(t)
                    found.append(t)
        return found

    def tempo(self) -> float | None:
        return tempo(list(self.beats))


SOURCES = (("system", "Systemton"), ("mic", "Mikrofon"))


def capture_command(source: str = "system") -> list[str] | None:
    """A command that writes raw mono s16le samples at RATE to stdout, or None."""
    dev = "@DEFAULT_MONITOR@" if source == "system" else "@DEFAULT_SOURCE@"
    if shutil.which("parec"):
        return ["parec", f"--device={dev}", "--format=s16le", f"--rate={RATE}", "--channels=1", "--raw",
                "--latency-msec=20"]
    if shutil.which("pw-record"):
        cmd = ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "--latency", "20ms"]
        if source == "system":
            cmd += ["-P", "{ stream.capture.sink=true }"]
        return cmd + ["-"]
    return None


class AudioCapture:
    """Runs the capture command and hands chunks of samples to `on_samples`
    (from a background thread)."""

    def __init__(self, on_samples: Callable[[np.ndarray, float], None], command: list[str] | None = None,
                 on_error: Callable[[str], None] | None = None):
        self.on_samples = on_samples
        self.on_error = on_error or (lambda _m: None)
        self.command = command
        self.proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None

    def start(self) -> None:
        if self.proc is not None:
            return
        if not self.command:
            raise RuntimeError("Kein Aufnahme-Programm gefunden (parec oder pw-record). "
                               "Unter Fedora/Nobara: sudo dnf install pulseaudio-utils")
        from .video import system_env

        self.proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=system_env(), bufsize=0)
        self._thread = threading.Thread(target=self._run, args=(self.proc,), name="audio", daemon=True)
        self._thread.start()

    def _run(self, proc: subprocess.Popen) -> None:
        chunk = HOP * 2 * 2                      # two hops of int16
        rest = b""
        while self.proc is proc:
            data = proc.stdout.read(chunk)
            if not data:
                break
            data = rest + data
            cut = len(data) - len(data) % 2
            rest = data[cut:]
            samples = np.frombuffer(data[:cut], np.int16).astype(np.float32) / 32768
            self.on_samples(samples, time.monotonic() * 1000)
        if self.proc is proc:
            err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            self.proc = None
            self.on_error(err or "Aufnahme beendet")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                proc.kill()


class MusicDriver:
    """Turns live beats into device moves through a ScriptSync: down on the beat,
    up halfway. Once the tempo is known, the next beats are predicted, so the
    device can arrive on time despite the delay of sound and Bluetooth."""

    def __init__(self, sync):
        self.sync = sync
        self.every = 1
        self.lo, self.hi = 0, 100
        self.follow_level = False
        self.period = None                # ms between used beats
        self.last = None                  # ms of the last used beat
        self.count = 0
        self.level = 0.0
        self.acts: list[Action] = []
        self.running = False

    def start(self) -> None:
        self.running = True
        self.period = self.last = None
        self.count = 0
        self.acts = []
        self.sync.stop()

    def stop(self) -> None:
        self.running = False
        self.acts = []
        self.sync.stop()

    def bpm(self) -> float | None:
        return 60_000 / self.period if self.period else None

    def beat(self, t_ms: float, level: float = 1.0) -> None:
        if not self.running:
            return
        self.count += 1
        if self.every > 1 and self.count % self.every:
            return
        self.level = level
        if self.last is not None:
            d = t_ms - self.last
            if 200 < d < 3000:
                self.period = d if self.period is None else 0.7 * self.period + 0.3 * d
            elif d >= 3000:
                self.period = None        # music stopped for a while: start over
        self.last = t_ms
        self._plan(t_ms)

    def _plan(self, now: float) -> None:
        depth = 1.0 if not self.follow_level else 0.45 + 0.55 * max(0.0, min(1.0, self.level))
        lo = self.hi - (self.hi - self.lo) * depth
        p = self.period or 600.0
        acts = [Action(int(now), int(round(lo)))]
        for k in range(4):                # up halfway, down on the predicted beats
            acts.append(Action(int(now + p * (k + 0.5)), self.hi))
            acts.append(Action(int(now + p * (k + 1)), int(round(lo))))
        if self.period is None:           # no tempo yet: one stroke, then wait at the top
            acts = acts[:2]
        self.acts = acts
        self.sync.set_actions(acts)

    def tick(self, now: float) -> None:
        if not self.running or not self.acts:
            return
        if self.last is not None and self.period and now - self.last > 3 * self.period:
            self.acts = [Action(int(now), self.hi)]       # music paused: rest at the top
            self.sync.set_actions(self.acts)
            self.period = None
        self.sync.tick(now, True)
