import pytest

import queue_manager
import xs_momentum as xm


@pytest.fixture(autouse=True)
def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    return store


def _closes(n_coins=20, drift=lambda i: i * 0.002):
    out = {}
    for i in range(n_coins):
        out[f"C{i}USDT"] = [(d * 86400_000, 100 * (1 + drift(i)) ** d) for d in range(31)]
    return out


def test_rank_and_legs():
    r = xm.rank(_closes())
    assert r[0][0] == "C0USDT" and r[-1][0] == "C19USDT"
    longs, shorts = xm.pick_legs(r)
    assert longs[0][0] == "C19USDT" and shorts[0][0] == "C0USDT"
    assert len(longs) == len(shorts) == 4
    assert xm.pick_legs(r[:5]) is None


def test_tick_opens_then_holds_then_rebalances():
    closes = _closes()
    prices = {s: c[-1][1] for s, c in closes.items()}
    uni = list(closes)
    ev = xm.tick(1000.0, uni, lambda s: closes[s], lambda syms: {s: prices[s] for s in syms if s in prices})
    assert ev["opened"] and ev["closed"] is None
    assert xm.tick(1000.0 + 3 * 86400, uni, lambda s: closes[s], lambda syms: prices) is None   # ещё держим
    up = {s: p * (1.10 if s in {x["sym"] for x in ev["opened"]["long"]} else 0.95 if s in {x["sym"] for x in ev["opened"]["short"]} else 1.0)
          for s, p in prices.items()}
    ev2 = xm.tick(1000.0 + 8 * 86400, uni, lambda s: closes[s], lambda syms: {s: up[s] for s in syms if s in up})
    c = ev2["closed"]
    assert c["long_pct"] == pytest.approx(10.0) and c["short_pct"] == pytest.approx(5.0)
    assert c["net"] == pytest.approx(15.0 - xm.COST_PCT)
    assert ev2["opened"] and xm.stats()["n"] == 1


def test_text_limits_and_honesty():
    closes = _closes()
    prices = {s: c[-1][1] for s, c in closes.items()}
    ev = xm.tick(1.0, list(closes), lambda s: closes[s], lambda syms: {s: prices[s] for s in syms if s in prices})
    ev["closed"] = {"net": -1.2, "long_pct": 2.0, "short_pct": -3.0}
    txt = xm.build_text(ev)
    import re
    assert len(set(re.findall(r"\$([A-Z0-9]+)", txt))) <= 3
    assert "not a recommendation" in txt and "−1.20%" not in txt and "-1.20%" in txt
    assert "Paper record so far: 0 closed weeks" in txt


def test_no_universe_no_event():
    assert xm.tick(1.0, [], lambda s: [], lambda s: {}) is None
