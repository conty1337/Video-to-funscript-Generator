"""Body recognition in the GUI: model download and box suggestions."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from .. import pose
from ..i18n import tr


class DownloadWorker(QThread):
    progress = Signal(float, str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            pose.download(self.progress.emit, lambda: self._stop)
        except pose.DownloadCancelled:
            self.failed.emit("")
        except Exception as e:          # network, disk, checksum
            self.failed.emit(str(e))


def ensure_models(parent: QWidget | None) -> bool:
    """True when the models are there, after asking and downloading if needed."""
    if pose.installed():
        return True
    mb = pose.download_mb()
    ans = QMessageBox.question(
        parent, tr("KI-Modelle laden"),
        tr("Für die Körpererkennung werden einmalig zwei Modelle geladen (zusammen etwa {0} MB, von download.openmmlab.com, Lizenz Apache-2.0). Sie bleiben auf diesem Rechner unter\n{1}\nund laufen danach ohne Internet.\n\nJetzt laden?", mb, pose.models_dir()))
    if ans != QMessageBox.Yes:
        return False
    dlg = QProgressDialog(tr("KI-Modelle werden geladen …"), tr("Abbrechen"), 0, 1000, parent)
    dlg.setWindowTitle(tr("KI-Modelle laden"))
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    worker = DownloadWorker()
    errors: list[str] = []

    def prog(p: float, name: str):
        dlg.setValue(int(p * 1000))
        if name:
            dlg.setLabelText(tr("KI-Modelle werden geladen … {0:.0f} von {1} MB", p * mb, mb))

    worker.progress.connect(prog)
    worker.failed.connect(errors.append)
    worker.finished.connect(dlg.close)
    dlg.canceled.connect(worker.stop)
    worker.start()
    dlg.exec()
    worker.stop()
    worker.wait()
    if errors and errors[0]:
        QMessageBox.warning(parent, tr("KI-Modelle laden"), tr("Laden fehlgeschlagen: {0}", errors[0]))
    return pose.installed()


class PoseWorker(QThread):
    """Finds the people in one frame in the background."""
    done = Signal(object)            # list[Person]
    failed = Signal(str)

    _estimator: pose.PoseEstimator | None = None      # nets stay loaded between uses

    def __init__(self, frame: np.ndarray):
        super().__init__()
        self.frame = frame

    @staticmethod
    def loaded() -> bool:
        return PoseWorker._estimator is not None

    def run(self):
        try:
            if PoseWorker._estimator is None:
                PoseWorker._estimator = pose.PoseEstimator()
            self.done.emit(PoseWorker._estimator.people(self.frame))
        except Exception as e:
            self.failed.emit(str(e))
