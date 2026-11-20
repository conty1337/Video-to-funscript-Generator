"""Fredorch F21S timing: ask for the speed step closest to the real stroke time."""
from vtf.device import fredorch


def test_table_falls_with_speed():
    assert len(fredorch.SPEED_MATRIX) == 15
    for row in fredorch.SPEED_MATRIX:
        assert len(row) == 20 and all(a > b for a, b in zip(row, row[1:]))


def test_driver_gets_the_closest_speed():
    for dist in range(1, 16):
        row = fredorch.SPEED_MATRIX[dist - 1]
        for real in range(row[-1], row[0], 37):
            speed = fredorch.driver_speed(dist, fredorch.duration_for(dist, real))
            travel = row[speed - 1]
            assert abs(travel - real) == min(abs(t - real) for t in row), (dist, real)


def test_slow_stroke_no_longer_early():
    # full stroke (15 steps) in 2.2 s: the driver alone picks 1930 ms (270 ms early);
    # the closest step is 2668 ms (468 late) or 1930 (270 early) -> 1930, sent as 1931
    assert fredorch.driver_speed(15, 2200) == 6
    assert fredorch.duration_for(15, 2200) == 1931
    # 2.5 s: alone 1930 (570 early), closest 2668 (168 late)
    assert fredorch.driver_speed(15, fredorch.duration_for(15, 2500)) == 5
    assert fredorch.duration_for(0, 300) == 300 and fredorch.step(100) == 15 and fredorch.step(52) == 8
