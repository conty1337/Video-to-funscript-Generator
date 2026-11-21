"""F21S correction from drive test measurements."""
from vtf.device import fredorch


def test_table_falls_with_speed():
    assert len(fredorch.SPEED_MATRIX) == 15
    for row in fredorch.SPEED_MATRIX:
        assert len(row) == 20 and all(a > b for a, b in zip(row, row[1:]))


def test_factors_take_off_the_reaction_time():
    # reference steps 10/9 measured 200 ms longer than the table: that is the reaction
    measured = {10: [818, 818], 9: [1078], 6: [3100, 3060], 5: [4202]}
    reaction, factors = fredorch.factors_from(measured)
    assert reaction == 200
    assert abs(factors[6] - (3080 - 200) / 1930) < 1e-9 and abs(factors[5] - 4002 / 2668) < 1e-9
    assert 10 not in factors and 9 not in factors
    assert fredorch.factors_from({6: [2500]}) == (0.0, {6: 2500 / 1930})


def test_pick_never_late_and_driver_takes_that_step():
    factors = {s: 1.5 for s in range(1, 9)}                 # slow steps 50 % slower
    for dist in range(1, 16):
        row = fredorch.SPEED_MATRIX[dist - 1]
        for d in range(200, 9000, 97):
            speed, sent = fredorch.pick(dist, d, factors)
            if speed:
                assert fredorch.real_ms(dist, speed, factors) <= d
                assert next(s + 1 for s, t in enumerate(row) if t < sent) == speed
    # full stroke in 2 s: step 6 really takes 2895 ms -> step 8 (1520 * 1.5 = 2280) too slow -> step 9
    assert fredorch.pick(15, 2000, factors) == (9, 879)
    assert fredorch.pick(15, 3000, factors) == (6, 1931)    # 1930 * 1.5 = 2895 fits
    assert fredorch.pick(15, 2000, {}) == (0, 2000)          # no correction: as before
    assert fredorch.pick(0, 500, factors) == (0, 500)
    assert fredorch.step(100) == 15 and fredorch.step(52) == 8


def test_buttplug_sends_the_picked_duration(monkeypatch):
    from vtf.device.buttplug import ButtplugDevice, RemoteDevice
    dev = ButtplugDevice()
    sent = []
    monkeypatch.setattr(dev, "_device", lambda: RemoteDevice(3, "Fredorch Device", 1, 15))
    monkeypatch.setattr(dev, "_send", sent.append)
    dev.move(100, 2000)                                       # no correction: unchanged
    assert sent[-1]["LinearCmd"]["Vectors"][0]["Duration"] == 2000
    dev.fred_factors = {s: 1.5 for s in range(1, 9)}
    dev.move(0, 2000)                                         # 15 steps back down
    assert sent[-1]["LinearCmd"]["Vectors"][0]["Duration"] == 879
