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


def test_outward_rounding_and_exact_steps():
    import math
    assert fredorch.step(3, 2) == 0 and fredorch.step(4, 0) == 1          # down: floor, up: ceil
    assert fredorch.step(37, 5) == 6 and fredorch.step(96, 15) == 14
    assert fredorch.step(4, 0, hold_pct=5) == 0 and fredorch.step(1, 0, hold_pct=0) == 1   # adjustable
    assert fredorch.step(35, 5) == 5                                      # < 0.4 steps: stays put
    assert fredorch.step(11 * 100 / 15, 0) == 11                          # exact step, no float slip
    for k in range(16):                                                   # Intiface: ceil(pos * 15)
        assert math.ceil(round(fredorch.wire(k), 4) * 15) == k


def test_tempo_limit_shortens_too_fast_strokes():
    assert fredorch.min_ms(15, {}) == 200 and fredorch.fit(15, 200, {}) == 15
    assert fredorch.fit(15, 125, {}) == 8                                 # 240/min: 8 steps fit (119 ms)
    assert fredorch.fit(15, 29, {}) == 1 and fredorch.fit(15, 10, {}) == 0
    limits = {15: 300.0}                                                  # measured: 100/min at full depth
    assert fredorch.min_ms(15, limits) == 300 and fredorch.min_ms(8, limits) == 119 * 1.5
    assert fredorch.min_ms(15, {15: 100.0}) == 200                        # never faster than the table
    assert fredorch.min_ms(11, {15: 400.0, 8: 119.0}) == 155 * (1 + 3 / 7)


def test_buttplug_limits_and_rounds(monkeypatch):
    from vtf.device.buttplug import ButtplugDevice, RemoteDevice
    dev = ButtplugDevice()
    sent = []
    monkeypatch.setattr(dev, "_device", lambda: RemoteDevice(3, "Fredorch Device", 1, 15))
    monkeypatch.setattr(dev, "_send", sent.append)
    dev.move(100, 125)                                         # too fast for a full stroke
    assert dev._fred_step == 8 and sent[-1]["LinearCmd"]["Vectors"][0]["Position"] == round(7.5 / 15, 4)
    dev.fred_limits = None                                     # tempo test: no limit
    dev.move(0, 50)
    assert dev._fred_step == 0
