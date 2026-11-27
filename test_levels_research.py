import random

import levels_research as lr


def _walk(seed, n=12000):
    random.seed(seed)
    p, bars = 100.0, []
    for i in range(n):
        o = p
        c = o * (1 + random.gauss(0, 0.006))
        h = max(o, c) * (1 + abs(random.gauss(0, 0.002)))
        low = min(o, c) * (1 - abs(random.gauss(0, 0.002)))
        bars.append((i * 3600_000, o, h, low, c, 1.0))
        p = c
    return bars


def test_aggregate_4h_complete_only():
    bars = [(i * 3600_000, 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i, 1.0) for i in range(10)]
    out = lr.aggregate(bars, 4)
    assert [b[0] for b in out] == [0, 4 * 3600_000]          # неполный хвост отброшен
    assert out[0][1] == 1.0 and out[0][4] == 1.5 + 3 and out[0][2] == 2.0 + 3 and out[0][3] == 0.5 and out[0][5] == 4.0


def test_swings_known_only_after_k_bars():
    bars = [(i * 3600_000, 10, h, 9, 10, 1) for i, h in enumerate([10, 11, 12, 15, 12, 11, 10, 10, 10])]
    hi, _lo = lr.swings(bars, 3)
    assert 3 + 3 in hi and hi[6] == (3, 15)                   # максимум на свече 3 известен только с индекса 6
    assert all(k >= 6 for k in hi)


def test_simulate_stop_before_target_in_same_bar():
    bars = [(0, 100, 100, 100, 100, 1), (1, 100, 102, 98, 100, 1)]
    assert lr.simulate(bars, 0, 100.0, 99.0, 1) == (1, 99.0)  # в свече достигнуты и стоп 99, и цель 101: засчитывается стоп


def test_random_walk_has_no_edge():
    """Контроль: на случайном блуждании ни одна конфигурация не должна проходить отбор (иначе в тесте утечка из будущего)."""
    for seed in (1, 2):
        bars = _walk(seed)
        for tf in (1, 4):
            for strat in lr.STRATS:
                tr = [(t, "X", n, r) for t, n, r in lr.trades_for(lr.aggregate(bars, tf), strat, False)]
                ev = lr.evaluate(tr)
                assert not lr.verdict(ev), (seed, tf, strat, ev)
                if ev.get("n", 0) > 200:
                    assert ev["net"] < 0.15, (seed, tf, strat, ev)


def test_report_renders():
    rows = [(("breakout", 4, False), {"n": 300, "days": 200, "net": -0.1, "r": -0.1, "win": 0.5, "t": -1.0, "m1": -0.1, "m2": -0.1, "coins_pos": 0.3}),
            (("retest_close", 1, True), {"n": 0})]
    text = lr.report(rows, 30, 900)
    assert "breakout" in text and "0 из 2" in text.replace("**", "") or "**0** из 2" in text
