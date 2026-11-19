"""Device control: a common interface, a simulator, Intiface (Buttplug, built-in engine) and TCode."""

from .base import Device, DeviceError
from .simulated import SimulatedDevice

__all__ = ["Device", "DeviceError", "SimulatedDevice"]
