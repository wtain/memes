import random

import pytest

from batch.utils.comics_compose import ComposeError, plan_layout


def _pl(layout):
    return [(p.x, p.y, p.width, p.height) for p in layout.placements]


def test_single():
    lo = plan_layout([(100, 100)])
    assert (lo.canvas_width, lo.canvas_height, lo.gutter) == (108, 108, 4)
    assert _pl(lo) == [(4, 4, 100, 100)]


def test_two_equal():
    lo = plan_layout([(1000, 1000), (1000, 1000)])
    assert (lo.canvas_width, lo.canvas_height, lo.gutter) == (2030, 1020, 10)
    assert _pl(lo) == [(10, 10, 1000, 1000), (1020, 10, 1000, 1000)]


def test_taller_scaled_down():
    lo = plan_layout([(1000, 1000), (960, 1280)])
    assert _pl(lo)[1][2:] == (750, 1000)
    assert (lo.canvas_width, lo.canvas_height) == (1780, 1020)


def test_odd_last_centered():
    lo = plan_layout([(1000, 1000), (750, 1000), (1000, 1000)])
    assert (lo.canvas_width, lo.canvas_height) == (1780, 2030)
    assert _pl(lo)[2] == (390, 1020, 1000, 1000)


def test_lone_last_scaled_to_previous_row():
    lo = plan_layout([(500, 500), (500, 500), (1000, 1000)])
    assert lo.gutter == 5
    p = lo.placements[2]
    assert (p.width, p.height) == (500, 500)
    assert p.x == 5 + (1005 - 500) // 2


def test_no_upscaling():
    lo = plan_layout([(400, 300), (800, 900)])
    assert _pl(lo)[0][2:] == (400, 300)
    assert _pl(lo)[1][2:] == (267, 300)


def test_different_row_widths():
    lo = plan_layout([(100, 100), (75, 100), (133, 100), (178, 100)])
    assert lo.gutter == 4
    assert lo.canvas_width == 4 + 315 + 4
    assert lo.placements[0].x == 72
    assert lo.placements[2].x == 4


def test_row_counts():
    lo = plan_layout([(100, 100)] * 4)
    assert sorted({p.y for p in lo.placements}) == [4, 108]
    lo = plan_layout([(100, 100)] * 5)
    assert sorted({p.y for p in lo.placements}) == [4, 108, 212]
    assert lo.placements[4].x == 4 + (204 - 100) // 2


def test_extreme_ratios():
    lo = plan_layout([(1, 1000), (1000, 1)])
    assert all(p.width >= 1 and p.height >= 1 for p in lo.placements)


def test_errors():
    with pytest.raises(ComposeError) as e:
        plan_layout([])
    assert e.value.reason == "no_panels"
    with pytest.raises(ComposeError) as e:
        plan_layout([(9000, 100), (9000, 100)])
    assert e.value.reason == "too_large" and e.value.position is None


def test_property():
    rng = random.Random(1)
    for _ in range(200):
        n = rng.randint(1, 6)
        sizes = [(rng.randint(1, 3000), rng.randint(1, 3000)) for _ in range(n)]
        try:
            lo = plan_layout(sizes)
        except ComposeError as e:
            assert e.reason == "too_large"
            continue
        ps = lo.placements
        assert [p.index for p in ps] == list(range(n))
        for p, (w, h) in zip(ps, sizes):
            assert p.x >= 0 and p.y >= 0
            assert p.x + p.width <= lo.canvas_width and p.y + p.height <= lo.canvas_height
            assert p.width <= w and p.height <= h
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                assert (a.x + a.width <= b.x or b.x + b.width <= a.x
                        or a.y + a.height <= b.y or b.y + b.height <= a.y)
        rows = {}
        for p in ps:
            rows.setdefault(p.y, set()).add(p.height)
        assert all(len(v) == 1 for v in rows.values())
