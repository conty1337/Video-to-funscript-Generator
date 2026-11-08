"""OpenCL (OpenCV T-API) helpers, e.g. for the AMD RX 5700 XT via ROCm or Mesa rusticl."""

from __future__ import annotations

import cv2


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
