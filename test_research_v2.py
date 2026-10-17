"""Тесты research_v2 на синтетических данных (сети нет)."""
import random

import research_sweep as rs
import research_v2 as r2


def _walk(n, seed, drift=0.0, vol=0.01, t0=1_600_000_000_000):
    rng = random.Random(seed)
    price, bars = 100.0, []
    for i in range(n):
        o = price
        c = o * (1 + drift + rng.gauss(0, vol))
        h, l = max(o, c) * 1.003, min(o, c) * 0.997
        bars.append(rs.Bar(t0 + i * r2.INTERVAL_MS, o, h, l, c, 1e6))
        price = c
    return bars


def _data(seed0, drift=0.0, n=2500):
    syms = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOT"]
    return {s: _walk(n, seed0 + k, drift) for k, s in enumerate(syms)}


SPECS = [(180, 3)]


def test_random_walk_yields_nothing_found():
    results, *_ = r2.run(_data(1), SPECS)
    assert results
    assert not [r for r in results if r["found"]]


def test_trailing_stop_cuts_loss_and_costs_applied():
    bars = _walk(400, 5)
    ctx = r2.build_ctx(bars, {b.t: True for b in bars}, None, "BTC")
    trades = r2.simulate_trailing(ctx, lambda c, i: 1 if i == 310 else 0, 2.5, 30)
    assert len(trades) == 1
    ei, xi, pct = trades[0]
    assert ei == 311 and xi >= ei
    gross_floor = -(2.5 * ctx["atr"][310] / bars[311].o * 100) - r2.COST_PCT - 5  # допуск на гэп
    assert pct > gross_floor


def test_holdout_is_separate_from_train():
    results, t_min, t_split, t_max = r2.run(_data(2), SPECS)
    assert t_min < t_split < t_max
    report = r2.render(results, list(_data(2)), t_min, t_split, t_max)
    assert "Контроль" in report and "Найдено" in report
