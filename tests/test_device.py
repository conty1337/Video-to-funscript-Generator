import json
import threading
import time

import pytest

from vtf.device import SimulatedDevice
from vtf.device.base import DeviceError
from vtf.device.sync import APPROACH_MS, ScriptSync, SyncSettings
from vtf.funscript import Action


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(actions, **settings):
    clock = FakeClock()
    dev = SimulatedDevice(clock=clock)
    dev.connect()
    sync = ScriptSync(dev, SyncSettings(**settings))
    sync.set_actions(actions)
    return sync, dev, clock


def play(sync, start, end, step=20):
    t = start
    while t <= end:
        sync.tick(t, True)
        t += step


STROKES = [Action(0, 0), Action(500, 100), Action(1000, 0), Action(1500, 100), Action(2000, 0)]


def test_simulator_quantizes_and_limits_speed():
    clock = FakeClock()
    dev = SimulatedDevice(clock=clock)
    dev.connect()
    dev.move(52, 10)              # 16 positions -> 53.33; full speed limit
    target, duration = dev.commands[-1]
    assert abs(target - 8 * 100 / 15) < 1e-6
    assert duration >= int(0.5333 * 200)
    clock.t = 10.0
    assert abs(dev.current_position() - target) < 1e-6


def test_sync_follows_next_action_with_time_until_it():
    sync, dev, clock = make(STROKES, max_speed=0)
    sync.tick(0, True)            # start: glide to current position first
    assert dev.commands[-1][1] == APPROACH_MS
    n = len(dev.commands)
    play(sync, 20, 480)           # still gliding until 500 ms
    assert len(dev.commands) == n
    sync.tick(510, True)          # next action is at 1000 (pos 0)
    assert len(dev.commands) == n + 1
    pos, dur = dev.commands[-1]
    assert pos == 0 and abs(dur - 490) <= 1
    play(sync, 530, 990)          # same segment -> no new command
    assert len(dev.commands) == n + 1
    sync.tick(1010, True)
    assert dev.commands[-1][0] == 100   # heading to 1500 / pos 100


def test_offset_sends_earlier():
    sync, dev, clock = make(STROKES, max_speed=0, offset_ms=100)
    play(sync, 0, 880)                  # t = media + 100
    assert dev.commands[-1][0] == 0     # heading to action at 1000
    sync.tick(905, True)                # t = 1005 -> next segment already at 905 ms
    assert dev.commands[-1][0] == 100


def test_pause_stops_and_seek_glides():
    sync, dev, clock = make(STROKES, max_speed=0)
    play(sync, 0, 600)
    stops = []
    dev.stop = lambda: stops.append(1)
    sync.tick(650, False)
    assert stops == [1]
    n = len(dev.commands)
    sync.tick(1500, True)               # resume elsewhere -> glide first
    assert len(dev.commands) == n + 1 and dev.commands[-1][1] == APPROACH_MS


def test_range_invert_and_speed_limit():
    s = SyncSettings(pos_min=20, pos_max=80, invert=True)
    assert s.map(0) == 80 and s.map(100) == 20 and s.map(50) == 50
    sync, dev, clock = make([Action(0, 0), Action(1000, 0), Action(1050, 100)], max_speed=500)
    play(sync, 0, 980)
    clock.t = 10                        # simulator has arrived at 0
    sync.tick(1001, True)               # 49 ms for a full stroke is too fast
    pos, dur = dev.commands[-1]
    # 500 units/s * 49 ms = 24.5 -> shortened stroke, quantized to 16 steps
    assert pos <= 30


def test_no_device_or_disconnected_is_harmless():
    sync = ScriptSync(None)
    sync.set_actions(STROKES)
    sync.tick(100, True)
    sync.tick(200, False)
    dev = SimulatedDevice()
    sync.set_device(dev)                # not connected
    sync.tick(300, True)
    assert dev.commands == []


# ------------------------------------------------------------ Buttplug client

class FakeIntiface:
    """Minimal Buttplug v3 server with one Fredorch device."""

    def __init__(self):
        from websockets.sync.server import serve
        self.received = []
        self.server = serve(self.handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handler(self, ws):
        for raw in ws:
            for msg in json.loads(raw):
                name, body = next(iter(msg.items()))
                self.received.append((name, body))
                mid = body["Id"]
                if name == "RequestServerInfo":
                    out = [{"ServerInfo": {"Id": mid, "ServerName": "Fake Intiface",
                                           "MessageVersion": 3, "MaxPingTime": 0}}]
                elif name == "RequestDeviceList":
                    out = [{"DeviceList": {"Id": mid, "Devices": []}}]
                elif name == "StartScanning":
                    out = [{"Ok": {"Id": mid}},
                           {"DeviceAdded": {"Id": 0, "DeviceIndex": 3, "DeviceName": "Fredorch Device",
                                            "DeviceMessages": {"LinearCmd": [{"StepCount": 15,
                                                                              "ActuatorType": "Position"}],
                                                               "StopDeviceCmd": {}}}}]
                else:
                    out = [{"Ok": {"Id": mid}}]
                ws.send(json.dumps(out))

    def close(self):
        self.server.shutdown()


@pytest.fixture
def intiface():
    srv = FakeIntiface()
    yield srv
    srv.close()


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_buttplug_connect_scan_move_stop(intiface):
    from vtf.device.buttplug import ButtplugDevice

    dev = ButtplugDevice(f"ws://127.0.0.1:{intiface.port}")
    msgs = []
    dev.add_listener(msgs.append)
    dev.connect()
    assert dev.connected and dev.server_name == "Fake Intiface"
    dev.start_scanning()
    assert wait_for(lambda: dev.selected == 3)
    assert dev.devices[3].step_count == 15
    dev.move(75, 400)
    dev.stop()
    assert wait_for(lambda: any(n == "StopDeviceCmd" for n, _ in intiface.received))
    linear = [b for n, b in intiface.received if n == "LinearCmd"][-1]
    assert linear["DeviceIndex"] == 3
    assert linear["Vectors"] == [{"Index": 0, "Duration": 400, "Position": 0.75}]
    dev.disconnect()
    assert not dev.connected
    assert any("Fredorch Device" in m for m in msgs)


def test_buttplug_unreachable_gives_clear_error():
    from vtf.device.buttplug import ButtplugDevice

    with pytest.raises(DeviceError, match="Intiface"):
        ButtplugDevice("ws://127.0.0.1:9").connect()
