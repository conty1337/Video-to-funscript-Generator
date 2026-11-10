"""Other devices: TCode (USB/UDP, all axes) and vibrators through Intiface."""
import os
import socket

import pytest

from vtf.device.buttplug import ButtplugDevice, RemoteDevice, _parse_device, vibe_strength
from vtf.device.tcode import AxisProxy, TCodeDevice, command


def test_tcode_command():
    assert command("L0", 100, 500) == "L09999I500"
    assert command("R1", 0, 0) == "R10000I0"
    assert command("L0", 50, 250) == "L05000I250"
    assert command("L0", 150, -5) == "L09999I0"


def _read_all(fd):
    out = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except (BlockingIOError, OSError):
            break
        if not chunk:
            break
        out += chunk
        if len(chunk) < 4096:
            break
    return out.decode()


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="kein pty")
def test_tcode_usb_and_axes():
    master, slave = os.openpty()
    path = os.ttyname(slave)
    dev = TCodeDevice(path)
    dev.connect()
    assert dev.connected and "USB" in dev.name
    dev.move(100, 400)
    AxisProxy(dev, "R0").move(25, 300)
    dev.move_axis("X9", 50, 100)                      # unknown axis: ignored
    dev.stop()
    text = _read_all(master)
    assert "L09999I400\n" in text and "R02500I300\n" in text and "DSTOP\n" in text and "X9" not in text
    dev.disconnect()
    assert not dev.connected
    os.close(master)
    os.close(slave)


def test_tcode_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    dev = TCodeDevice(f"udp://127.0.0.1:{rx.getsockname()[1]}")
    dev.connect()
    dev.move(0, 200)
    assert rx.recv(100) == b"L00000I200\n"
    dev.disconnect()
    rx.close()
    from vtf.device import DeviceError
    with pytest.raises(DeviceError):
        TCodeDevice("/dev/gibt-es-nicht").connect()


def test_vibrator_parse_and_strength():
    d = _parse_device({"DeviceIndex": 3, "DeviceName": "Lush", "DeviceMessages": {
        "ScalarCmd": [{"StepCount": 20, "ActuatorType": "Vibrate"}, {"StepCount": 10, "ActuatorType": "Rotate"}]}})
    assert d.is_vibe and d.usable and not d.is_linear and d.scalars == ["Vibrate", "Rotate"]
    assert vibe_strength("depth", None, 0, 300) == 1.0 and vibe_strength("depth", None, 100, 300) == 0.0
    assert vibe_strength("speed", 0, 100, 250) == 1.0              # 400 /s = full
    assert vibe_strength("speed", 0, 50, 250) == pytest.approx(0.5)


def test_vibrator_commands(monkeypatch):
    dev = ButtplugDevice()
    sent = []
    monkeypatch.setattr(dev, "_send", lambda m: sent.append(m) or 1)
    monkeypatch.setattr(type(dev), "connected", property(lambda self: True))
    vib = RemoteDevice(3, "Lush", scalars=["Vibrate"])
    dev._add_device({"DeviceIndex": 3, "DeviceName": "Lush",
                     "DeviceMessages": {"ScalarCmd": [{"ActuatorType": "Vibrate"}]}})
    assert dev.selected == 3                           # a vibrator is used when nothing else is there
    dev.move(100, 500)
    dev.move(0, 250)
    cmd = sent[-1]["ScalarCmd"]
    assert cmd["DeviceIndex"] == 3 and cmd["Scalars"][0]["Scalar"] == 1.0
    n = len(sent)
    dev.move(100, 250)                                 # same strength: nothing new sent
    assert len(sent) == n
    dev._add_device({"DeviceIndex": 4, "DeviceName": "F21S",
                     "DeviceMessages": {"LinearCmd": [{"StepCount": 16}]}})
    assert dev.selected == 4 and vib.is_vibe            # a stroker wins
