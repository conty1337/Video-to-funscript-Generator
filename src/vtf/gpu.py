"""OpenCL (OpenCV T-API) helpers, e.g. for the AMD RX 5700 XT via ROCm or Mesa rusticl."""

from __future__ import annotations

import cv2

from .i18n import tr


def opencl_available() -> bool:
    try:
        return bool(cv2.ocl.haveOpenCL())
    except cv2.error:
        return False


def set_opencl(enabled: bool) -> bool:
    """Enable/disable OpenCL. Returns whether OpenCL is actually in use."""
    if not opencl_available():
        cv2.ocl.setUseOpenCL(False)
        return False
    cv2.ocl.setUseOpenCL(bool(enabled))
    return bool(cv2.ocl.useOpenCL())


def opencl_device_name() -> str | None:
    if not opencl_available():
        return None
    try:
        prev = cv2.ocl.useOpenCL()
        cv2.ocl.setUseOpenCL(True)
        dev = cv2.ocl.Device.getDefault()
        name = f"{dev.name()} ({dev.vendorName()})" if dev.available() else None
        cv2.ocl.setUseOpenCL(prev)
        return name
    except (cv2.error, AttributeError):
        return None


NVIDIA_PATHS = ("/dev/nvidia0", "/proc/driver/nvidia")


def has_nvidia() -> bool:
    """An NVIDIA card with the proprietary driver is present."""
    import os

    return any(os.path.exists(p) for p in NVIDIA_PATHS)


_LOADER_PATHS = ("/usr/lib64/libOpenCL.so.1", "/usr/lib/x86_64-linux-gnu/libOpenCL.so.1",
                 "/usr/lib/libOpenCL.so.1", "/usr/lib64/libOpenCL.so", "/usr/lib/libOpenCL.so")
_ICD_DIR = "/etc/OpenCL/vendors"


def opencl_hint() -> str:
    """Why OpenCL is unavailable, with the fix for Fedora/Nobara (empty if it works)."""
    import os

    if opencl_device_name():
        return ""
    if not any(os.path.exists(p) for p in _LOADER_PATHS):
        return tr("Kein OpenCL-Loader gefunden. Installieren: sudo dnf install ocl-icd mesa-libOpenCL")
    icds = os.listdir(_ICD_DIR) if os.path.isdir(_ICD_DIR) else []
    if has_nvidia() and not any("nvidia" in n for n in icds):
        return tr("Kein OpenCL-Treiber für NVIDIA installiert. Unter Nobara: sudo dnf install xorg-x11-drv-nvidia-cuda-libs")
    if not icds:
        return tr("Kein OpenCL-Treiber installiert. Für AMD: sudo dnf install mesa-libOpenCL")
    if any("rusticl" in n for n in icds) and not os.environ.get("RUSTICL_ENABLE"):
        return tr("Mesa rusticl ist installiert, aber nicht aktiv: mit RUSTICL_ENABLE=radeonsi starten")
    return (tr("OpenCL-Treiber vorhanden (") + ", ".join(sorted(icds)) + tr("), aber kein Gerät gefunden. Prüfen mit: RUSTICL_ENABLE=radeonsi clinfo -l"))
