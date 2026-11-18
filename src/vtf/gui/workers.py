"""Background threads for analysis and batch processing."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from ..analysis import AnalysisCancelled, AnalysisSettings, analyze_motion_cached
from ..batch import run_batch
from ..generator import GeneratorSettings
from ..i18n import tr


class AnalysisWorker(QThread):
    progress = Signal(float)
    finishedOk = Signal(object)    # MotionData
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path: str, settings: AnalysisSettings, use_cache: bool = True):
        super().__init__()
        self.path, self.settings, self.use_cache = path, settings, use_cache
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            data = analyze_motion_cached(self.path, self.settings, self.progress.emit,
                                         lambda: self._stop, self.use_cache)
            self.finishedOk.emit(data)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


class ScenesWorker(QThread):
    """Looks for scene changes in a video (scenes.find_cuts)."""
    progress = Signal(float)
    done = Signal(object)          # list of ms
    failed = Signal(str)

    def __init__(self, path: str, hwaccel: str = "auto"):
        super().__init__()
        self.path, self.hwaccel = path, hwaccel
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from ..scenes import find_cuts
        try:
            cuts = find_cuts(self.path, self.hwaccel, self.progress.emit, lambda: self._stop)
            if not self._stop:
                self.done.emit(cuts)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


class SuggestWorker(QThread):
    """Tries script settings on the finished analysis (tuning.suggest)."""
    progress = Signal(float)
    done = Signal(object)          # (best list, current)
    failed = Signal(str)

    def __init__(self, motion, base: GeneratorSettings, sections, cuts):
        super().__init__()
        self.args = (motion, base, tuple(sections), tuple(cuts))
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from ..tuning import suggest
        try:
            motion, base, sections, cuts = self.args
            res = suggest(motion, base, sections, cuts, stop=lambda: self._stop, progress=self.progress.emit)
            if not self._stop:
                self.done.emit(res)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))


class BatchWorker(QThread):
    fileStarted = Signal(int, int, str)
    progress = Signal(float)
    log = Signal(str)
    done = Signal(object)          # BatchResult

    def __init__(self, folder: str, an: AnalysisSettings, gen: GeneratorSettings,
                 recursive: bool, overwrite: bool, tune: bool = False):
        super().__init__()
        self.args = (folder, an, gen, recursive, overwrite)
        self.tune = tune
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        folder, an, gen, recursive, overwrite = self.args
        try:
            res = run_batch(folder, an, gen, recursive, overwrite,
                            on_file=lambda i, n, p: self.fileStarted.emit(i, n, str(p)),
                            on_progress=self.progress.emit, log=self.log.emit,
                            cancel=lambda: self._stop, tune=self.tune)
        except Exception as e:
            self.log.emit(tr("Fehler: {0}", e))
            res = None
        self.done.emit(res)


class UpdateWorker(QThread):
    """mode 'check': emits checked(Release|None); mode 'install': installs ``release``."""
    checked = Signal(object)
    progress = Signal(float)
    installed = Signal(str)
    failed = Signal(str)

    def __init__(self, mode: str, release=None):
        super().__init__()
        self.mode, self.release = mode, release
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from .. import updater
        try:
            if self.mode == "check":
                self.checked.emit(updater.check_for_update())
            else:
                path = updater.download_and_install(self.release, progress=self.progress.emit,
                                                    cancel=lambda: self._stop)
                self.installed.emit(str(path))
        except updater.UpdateError as e:
            self.failed.emit(str(e))
        except Exception as e:  # never crash the app because of an update
            traceback.print_exc()
            self.failed.emit(str(e))
