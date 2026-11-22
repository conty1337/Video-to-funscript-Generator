"""tools/f21s_raw_test.py: F21S frames like Intiface's driver (Modbus RTU + CRC16)."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("f21s_raw", Path(__file__).parent.parent / "tools" / "f21s_raw_test.py")
raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raw)


def test_frames():
    assert raw.crc16(bytes.fromhex("010300000001")) == bytes.fromhex("840a")   # Modbus reference frame
    m = raw.move(75, 5)
    assert m[:17] == bytes([1, 0x10, 0, 0x6B, 0, 5, 10, 0, 5, 0, 5, 0, 75, 0, 75, 0, 1])
    assert raw.crc16(m) == b"\x00\x00"                                        # CRC over frame + CRC is 0
    assert raw.read(0x6B, 5) == raw.frame([1, 3, 0, 0x6B, 0, 5])
    with pytest.raises(ValueError):
        raw.move(151, 5)
