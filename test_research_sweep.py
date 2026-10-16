import random

import research_sweep as rs


def _walk(n=3000, seed=1, drift=0.0, vol=0.01):
    rng = random.Random(seed)
    p, bars = 100.0, []
    for i in range(n):
        o = p
        p = max(0.01, p * (1 + rng.gauss(drift, vol)))
        hi, lo = max(o, p) * (1 + abs(rng.gauss(0, vol / 3))), min(o, p) * (1 - abs(rng.gauss(0, vol / 3)))
        bars.append(rs.Bar(i * 3600_000, o, hi, lo, p, 1e6 * (1 + rng.random() * (3 if rng.random() < 0.1 else 1))))
    return bars


def test_random_walk_has_no_robust_edge():
    data = {f"C{k}": _walk(seed=k) for k in range(6)}
    ctxs = {s: rs.build_context(b, None) for s, b in data.items()}
    results = rs.run_sweep(ctxs)
    assert results
    assert not any(r["robust"] for r in results)  # случайное блуждание: после издержек преимущества нет
    avg = sum(r["total"]["avg"] for r in results if r["total"]["n"]) / sum(1 for r in results if r["total"]["n"])
    assert avg < 0  # в среднем минус - издержки реально вычитаются


def test_entry_uses_next_bar_open_and_costs():
    bars = [rs.Bar(i * 3600_000, 100, 101, 99, 100, 1) for i in range(300)]
    ctx = rs.build_context(bars, None)
    trades = rs.simulate(ctx, lambda c, i: 1 if i == 250 else 0, 1.5, 2.0)
    assert len(trades) == 1
    idx, pct = trades[0]
    assert idx == 251
    assert abs(pct - (0.0 - rs.ROUND_TRIP_COST_PCT)) < 0.5  # плоский рынок -> выход по времени, ~ -издержки


def test_stop_wins_when_both_hit_same_bar():
    bars = [rs.Bar(i * 3600_000, 100, 100.5, 99.5, 100, 1) for i in range(300)]
    bars[251] = rs.Bar(251 * 3600_000, 100, 120, 80, 100, 1)  # задеты и стоп, и цель
    ctx = rs.build_context(bars, None)
    (_, pct), = rs.simulate(ctx, lambda c, i: 1 if i == 250 else 0, 1.5, 2.0)
    assert pct < 0


def test_report_renders():
    data = {f"C{k}": _walk(seed=k) for k in range(5)}
    ctxs = {s: rs.build_context(b, None) for s, b in data.items()}
    text = rs.render_report(rs.run_sweep(ctxs), list(data), 120)
    assert "Устойчивые конфигурации" in text and "| стратегия |" in text
