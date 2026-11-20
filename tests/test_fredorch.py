"""Fredorch F21S timing: the driver's speed table tells when the device arrives."""
from vtf.device import fredorch


def test_table_falls_with_speed():
    assert len(fredorch.SPEED_MATRIX) == 15
    for row in fredorch.SPEED_MATRIX:
        assert len(row) == 20 and all(a > b for a, b in zip(row, row[1:]))


def test_travel_time_never_longer_than_asked():
    """The driver takes the slowest step that is still faster: never late, so the
    device cannot fall behind; the sync goes on when it arrives."""
    for dist in range(1, 16):
        row = fredorch.SPEED_MATRIX[dist - 1]
        for asked in range(row[-1] + 1, row[0] + 2000, 37):
            assert fredorch.travel_ms(dist, asked) < asked
    assert fredorch.travel_ms(15, 2200) == 1930 and fredorch.travel_ms(15, 2500) == 1930
    assert fredorch.travel_ms(0, 500) == 0
    assert fredorch.step(100) == 15 and fredorch.step(52) == 8
