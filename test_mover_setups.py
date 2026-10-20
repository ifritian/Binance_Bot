import pytest

import mover_setups as ms
import queue_manager


@pytest.fixture(autouse=True)
def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))


def _bars(n=30, price=100.0, rng=1.0):
    return [(i * 3600_000, price, price + rng / 2, price - rng / 2, price) for i in range(n)]


def test_build_setup_long_and_short():
    s = ms.build_setup("SOL", 100.0, 8.0, _bars())
    assert s["side"] == "long" and s["stop"] < 100 < s["target"]
    assert abs((s["target"] - 100) - 2 * (100 - s["stop"])) < 1e-9
    d = ms.build_setup("SOL", 100.0, -8.0, _bars())
    assert d["side"] == "short" and d["stop"] > 100 > d["target"]
    assert s["dist_pct"] >= ms.MIN_STOP_PCT


def test_build_setup_needs_data():
    assert ms.build_setup("SOL", 100.0, 8.0, _bars(5)) is None


def test_evaluate_target_stop_and_tie():
    pos = {"side": "long", "entry": 100.0, "stop": 98.0, "target": 104.0, "t0": 1000.0}
    t = 1000 * 1000
    assert ms.evaluate_one(pos, [(t, 100, 105, 99, 104)], 1100)["outcome"] == "target"
    assert ms.evaluate_one(pos, [(t, 100, 101, 97, 98)], 1100)["outcome"] == "stop"
    both = ms.evaluate_one(pos, [(t, 100, 105, 97, 100)], 1100)
    assert both["outcome"] == "stop"          # консервативно
    assert ms.evaluate_one(pos, [(t, 100, 101, 99, 100)], 1100) is None
    late = ms.evaluate_one(pos, [(t, 100, 101, 99, 100.5)], 1000 + ms.HOLD_S + 1)
    assert late["outcome"] == "time" and late["net"] == pytest.approx(0.5 - ms.COST_PCT, abs=0.01)


def test_short_pnl_and_fees():
    assert ms._net("short", 100, 96) == pytest.approx(4 - ms.COST_PCT)
    assert ms._net("long", 100, 98) == pytest.approx(-2 - ms.COST_PCT)


def test_register_advance_stats_and_texts():
    s = ms.build_setup("SOL", 100.0, 8.0, _bars())
    ms.register(s, now_ts=1000.0)
    bars = [(1000 * 1000, 100, s["target"] + 1, 99.9, s["target"])]
    ms.advance(now_ts=1100.0, fetch=lambda *a, **k: bars)
    st = ms.stats()
    assert st["closed"] == 1 and st["wins"] == 1 and st["open"] == 0
    c = ms._state()["closed"][0]
    txt = ms.build_result_text(c)
    assert "hit the target" in txt and "$SOL" in txt
    assert "Track record: 1 closed" in ms.record_line()
    post = ms.build_text(s)
    assert post.count("$") == 1 and "Stop:" in post and "Target:" in post and "Not financial advice" in post


def test_record_line_with_enough_closed():
    line = ms.record_line({"closed": 10, "wins": 4, "avg_net": -0.3, "open": 0})
    assert "4/10" in line and "-0.30%" in line
