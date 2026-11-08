"""Background threads for analysis and batch processing."""

from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from ..analysis import AnalysisCancelled, AnalysisSettings, analyze_motion_cached
from ..batch import run_batch
from ..generator import GeneratorSettings


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


class BatchWorker(QThread):
    fileStarted = Signal(int, int, str)
    progress = Signal(float)
    log = Signal(str)
    done = Signal(object)          # BatchResult

    def __init__(self, folder: str, an: AnalysisSettings, gen: GeneratorSettings,
                 recursive: bool, overwrite: bool):
        super().__init__()
        self.args = (folder, an, gen, recursive, overwrite)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        folder, an, gen, recursive, overwrite = self.args
        try:
            res = run_batch(folder, an, gen, recursive, overwrite,
                            on_file=lambda i, n, p: self.fileStarted.emit(i, n, str(p)),
                            on_progress=self.progress.emit, log=self.log.emit,
                            cancel=lambda: self._stop)
        except Exception as e:
            self.log.emit(f"Fehler: {e}")
            res = None
        self.done.emit(res)
