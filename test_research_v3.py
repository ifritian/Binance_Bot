import random

import research_sweep as rs
import research_v2 as r2
import research_v3 as r3
from test_research_v2 import _data, _walk


def test_random_walk_has_no_robust_findings():
    results, *_ = r3.run(_data(11, n=2600))
    assert results and not [r for r in results if r["robust"]]


def test_features_have_no_lookahead_and_right_shape():
    bars = _walk(900, 3)
    btc = {b.t: b.c for b in bars}
    ctx = r3.build_ctx(bars, {b.t: True for b in bars}, btc, "X")
    assert len(ctx["bw_pct"]) == len(bars) == len(ctx["rel30"])
    p = [x for x in ctx["bw_pct"] if x is not None]
    assert p and all(0 <= x <= 1 for x in p)
    assert all(abs(x) < 1e-9 for x in ctx["rel30"] if x is not None)  # сам к себе: ноль


def test_squeeze_signal_needs_low_bandwidth():
    bars = _walk(900, 4, drift=0.002)
    btc = {b.t: b.c for b in bars}
    ctx = r3.build_ctx(bars, {b.t: True for b in bars}, btc, "X")
    fired = [i for i in range(len(bars)) if r3.sig_squeeze(ctx, i, 40, 0.25)]
    assert all(ctx["bw_pct"][i - 1] <= 0.25 for i in fired)


def test_t_stat():
    assert r3._t_stat([1, 1, 1]) == 0.0  # нулевая дисперсия
    assert r3._t_stat([1, 2, 3, 2, 1, 2]) > 3


def test_render_contains_robust_header():
    results, t0, ts, t1 = r3.run(_data(12, n=2600))
    rep = r3.render(results, list(_data(12)), t0, ts, t1)
    assert "РОБАСТНО" in rep and "Проверено конфигураций" in rep
