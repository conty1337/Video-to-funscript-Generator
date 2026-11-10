"""Safety timer."""
from vtf.safety import SESSION_GAP_MS, SafetyGuard, SafetySettings

MIN = 60_000


def run(g, start, end, active=True, step=1000):
    events = []
    t = start
    while t <= end:
        ev = g.update(t, active)
        if ev:
            events.append((t, ev))
        t += step
    return events


def test_off_by_default_does_nothing():
    g = SafetyGuard()
    assert not g.settings.enabled
    assert run(g, 0, 120 * MIN, step=MIN) == [] and g.status(0) == ""


def test_max_minutes_and_idle():
    g = SafetyGuard(SafetySettings(enabled=True, max_minutes=10, idle_minutes=0))
    ev = run(g, 0, 11 * MIN, step=10_000)
    assert ev[0][1][0] == "stop" and 10 * MIN <= ev[0][0] <= 10 * MIN + 10_000
    g = SafetyGuard(SafetySettings(enabled=True, max_minutes=0, idle_minutes=3))
    g.touch(0)
    ev = run(g, 0, 2 * MIN, step=10_000)
    assert ev == []
    g.touch(2 * MIN)                                 # touching keeps it going
    ev = run(g, 2 * MIN, 6 * MIN, step=10_000)
    assert ev[0][1][0] == "stop" and ev[0][0] >= 5 * MIN


def test_breaks_and_pauses_do_not_count():
    g = SafetyGuard(SafetySettings(enabled=True, max_minutes=0, idle_minutes=0, break_every=5,
                                   break_seconds=30))
    ev = run(g, 0, 5 * MIN + 1000)
    assert ev[-1][1] == ("break", 30) and g.on_break and "Pause" in g.status(5 * MIN + 2000)
    ev = run(g, 5 * MIN + 2000, 5 * MIN + 40_000, active=False)
    assert ev[0][1] == ("resume",) and not g.on_break
    # time without motion is not counted, a long gap starts a new session
    g = SafetyGuard(SafetySettings(enabled=True, max_minutes=10, idle_minutes=0))
    run(g, 0, 8 * MIN, step=10_000)
    run(g, 8 * MIN, 8 * MIN + SESSION_GAP_MS + 10_000, active=False, step=10_000)
    assert g.active_ms == 0
