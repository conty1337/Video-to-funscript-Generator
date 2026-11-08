"""Editing operations on selections."""
from vtf import editing
from vtf.funscript import Action


def A(*pairs):
    return [Action(t, p) for t, p in pairs]


def test_move_keeps_order_and_range():
    acts = A((0, 0), (100, 50), (200, 100), (300, 50))
    out = editing.move(acts, {1, 2}, 30, -20, 1000)
    assert [(a.at, a.pos) for a in out] == [(0, 0), (130, 30), (230, 80), (300, 50)]
    # cannot pass the next unselected point, cannot leave 0..100
    out = editing.move(acts, {1, 2}, 500, 50, 1000)
    assert out[2].at == 299 and out[2].pos == 100 and out[1].pos == 50
    out = editing.move(acts, {0}, -50, 0, 1000)
    assert out[0].at == 0
    # an unselected point between selected ones bounds the shift too
    out = editing.move(acts, {0, 2}, 150, 0, 1000)
    assert [a.at for a in out] == sorted(a.at for a in out)
    assert out[0].at < 100


def test_copy_paste_replaces_span():
    acts = A((0, 0), (100, 100), (200, 0), (1000, 0), (1050, 60), (1100, 0))
    clip = editing.copy(acts, {0, 1, 2})
    assert clip == [(0, 0), (100, 100), (200, 0)]
    out, sel = editing.paste(acts, clip, 1000, 5000)
    assert [(a.at, a.pos) for a in out] == [(0, 0), (100, 100), (200, 0), (1000, 0), (1100, 100), (1200, 0)]
    assert sel == {3, 4, 5}
    out, sel = editing.paste(acts, clip, 4900, 5000)    # cut at the end of the video
    assert max(a.at for a in out) <= 5000


def test_smooth_and_scale():
    acts = A((0, 50), (100, 90), (200, 50))
    out = editing.smooth(acts, {1})
    assert out[1].pos == 70
    acts = A((0, 40), (100, 60), (200, 40))
    out = editing.scale(acts, {0, 1, 2}, 2.0)
    assert [a.pos for a in out] == [30, 70, 30]
    out = editing.scale(A((0, 0), (100, 100)), {0, 1}, 3.0)
    assert [a.pos for a in out] == [0, 100]                # clamped


def test_delete_and_rect():
    acts = A((0, 0), (100, 50), (200, 100))
    assert editing.in_rect(acts, 50, 250, 40, 100) == {1, 2}
    assert [a.at for a in editing.delete(acts, {0, 2})] == [100]
