"""tools/jss57p2n_modbus_scan.py: read-only Modbus RTU scan (no hardware: simulated driver)."""
import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).parent.parent / "tools"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                  # dataclasses look the module up there
    spec.loader.exec_module(mod)
    return mod


scan = _load("jss57p2n_modbus_scan")


class FakeDriver:
    """Answers like a Modbus slave, only at its own baud/parity/address; logs every frame."""

    def __init__(self, baud=38400, parity="E", slave=3, regs=None):
        self.baud, self.parity, self.slave = baud, parity, slave
        self.regs = regs if regs is not None else {(3, a): a * 10 for a in range(0x10, 0x20)}
        self.sent = []

    def port(self, baud, parity):
        return FakePort(self, baud, parity)


class FakePort:
    def __init__(self, drv, baud, parity):
        self.drv, self.baudrate, self.parity, self.buf = drv, baud, parity, b""

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data):
        d = self.drv
        d.sent.append(bytes(data))
        if (self.baudrate, self.parity) != (d.baud, d.parity) or data[0] != d.slave:
            return                                    # wrong settings: silence
        func, start, n = data[1], int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:6], "big")
        vals = [d.regs.get((func, a)) for a in range(start, start + n)]
        if None in vals:
            body = bytes((d.slave, func | 0x80, 2))
        else:
            body = bytes((d.slave, func, 2 * n)) + b"".join(v.to_bytes(2, "big") for v in vals)
        self.buf = body + scan.crc16(body)

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass


def test_only_read_requests_exist():
    assert scan.request(1, 3, 0, 1) == bytes.fromhex("010300000001840a")     # Modbus reference frame
    for func in (5, 6, 15, 16, 0x17, 0):
        with pytest.raises(ValueError):
            scan.request(1, func, 0, 1)
    with pytest.raises(ValueError):
        scan.request(1, 3, 0, 126)
    raw = _load("f21s_raw_test")
    for data in (b"\x01\x03\x00\x00\x00\x01", bytes(range(30))):
        assert scan.crc16(data) == raw.crc16(data)                           # same CRC as the BLE tool


def test_parse_checks_everything():
    body = bytes((1, 3, 4, 0x12, 0x34, 0xFF, 0xFE))
    good = body + scan.crc16(body)
    assert scan.parse(good, 1, 3, 2).values == [0x1234, 0xFFFE]
    assert "CRC" in scan.parse(good[:-1] + b"\x00", 1, 3, 2).error
    assert "Slave" in scan.parse(good, 2, 3, 2).error
    assert "Funktionscode" in scan.parse(good, 1, 4, 2).error
    assert "Länge" in scan.parse(good, 1, 3, 3).error
    assert scan.parse(b"", 1, 3, 1).error == "keine Antwort"
    exc = bytes((1, 0x83, 2))
    ok = exc + scan.crc16(exc)
    r = scan.parse(ok, 1, 3, 1)
    assert r.exception == 2 and r.answered


def test_error_reply_counts_only_when_valid():
    """Only a correct Modbus error reply counts as "found" in the search, not any bytes."""
    def answered(raw, slave=1, func=3):
        return scan.parse(raw, slave, func, 1).answered
    exc = bytes((1, 0x83, 2))
    ok = exc + scan.crc16(exc)
    assert answered(ok)
    assert not answered(ok[:-1] + bytes((ok[-1] ^ 1,)))       # CRC wrong
    assert not answered(ok + b"\x00")                         # extra byte
    assert not answered(ok[:4])                                # cut off
    assert not answered(ok, slave=2)                           # other slave
    assert not answered(ok, func=4)                            # error for another function
    other = bytes((1, 0x84, 2))
    assert not answered(other + scan.crc16(other), func=3)
    assert not answered(bytes((0xFF, 0x00, 0x13, 0x37, 0x42)))  # line noise


def test_search_scan_and_compare_read_only():
    drv = FakeDriver()
    found = scan.search(drv.port, scan.BAUDS, scan.PARITIES, scan.SLAVES, 0, log=lambda *_: None)
    assert found == [(38400, "E", 3, 3)]             # exception at register 0 still means "found"
    bus = scan.Bus(drv.port(38400, "E"), 3)
    a = scan.scan(bus, 0x0C, 0x21, (3,), 8, log=lambda *_: None)
    assert sorted(adr for _f, adr in a) == list(range(0x10, 0x20))   # rejected blocks read one by one
    drv.regs[(3, 0x15)] = 7
    b = scan.scan(bus, 0x0C, 0x21, (3,), 8, log=lambda *_: None)
    assert scan.changes(a, b) == [(3, 0x15, 210, 7)]
    assert {f[1] for f in drv.sent} <= {3, 4}        # nothing but read requests ever went out
