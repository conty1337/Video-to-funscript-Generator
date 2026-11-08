"""Device control: a common interface, a simulator and Intiface Central (Buttplug)."""

from .base import Device, DeviceError
from .simulated import SimulatedDevice

__all__ = ["Device", "DeviceError", "SimulatedDevice"]
