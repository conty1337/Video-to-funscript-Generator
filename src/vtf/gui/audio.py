"""Sound for the preview.

The picture keeps coming from our own decoder, driven by the wall clock
(see MainWindow._next_frame). Only the audio track goes through Qt
Multimedia (QMediaPlayer without a video output). The clock stays the
master: the audio follows it and is re-synced when it drifts, so the device
timing does not depend on the audio backend.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QUrl

try:  # Qt Multimedia is part of PySide6-Essentials, but its backend may be missing
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # pragma: no cover - depends on the installation
    QAudioOutput = QMediaPlayer = None

#: re-sync the audio when it is further than this from the video clock
MAX_DRIFT_MS = 120
#: check the drift at most this often (QMediaPlayer.position() updates coarsely)
CHECK_EVERY_MS = 500


class AudioPlayer(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._out = None
        self._volume = 0.8
        self._muted = False
        self._last_check = -CHECK_EVERY_MS
        self.resyncs = 0
        if QMediaPlayer is None:
            return
        try:
            self._out = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._out)
            self._player.mediaStatusChanged.connect(self._on_status)
        except Exception:  # pragma: no cover - broken multimedia backend
            self._player = self._out = None
            return
        self._apply_volume()

    # ------------------------------------------------------------ state
    @property
    def available(self) -> bool:
        return self._player is not None

    @property
    def has_audio(self) -> bool:
        return self.available and bool(self._player.hasAudio())

    @property
    def playing(self) -> bool:
        return self.available and self._player.playbackState() == QMediaPlayer.PlayingState

    def position_ms(self) -> int:
        return int(self._player.position()) if self.available else 0

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, float(v)))
        self._apply_volume()

    def set_muted(self, m: bool) -> None:
        self._muted = bool(m)
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self._out is not None:
            self._out.setVolume(self._volume)
            self._out.setMuted(self._muted)

    def _on_status(self, status) -> None:
        # the picture comes from our own decoder: don't decode the video track twice
        if status == QMediaPlayer.LoadedMedia and hasattr(self._player, "setActiveVideoTrack"):
            self._player.setActiveVideoTrack(-1)

    # ------------------------------------------------------------ transport
    def load(self, path: str | None) -> None:
        if not self.available:
            return
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path) if path else QUrl())

    def play(self, ms: float) -> None:
        if not self.available:
            return
        self._player.setPosition(int(ms))
        self._player.play()
        self._last_check = ms

    def pause(self) -> None:
        if self.available:
            self._player.pause()

    def seek(self, ms: float) -> None:
        if self.available:
            self._player.setPosition(int(ms))
            self._last_check = ms

    def sync(self, media_ms: float) -> None:
        """Called on every playback tick with the video clock."""
        if not self.playing or abs(media_ms - self._last_check) < CHECK_EVERY_MS:
            return
        self._last_check = media_ms
        if abs(self._player.position() - media_ms) > MAX_DRIFT_MS:
            self._player.setPosition(int(media_ms))
            self.resyncs += 1

    def shutdown(self) -> None:
        if self.available:
            self._player.stop()
            self._player.setSource(QUrl())
