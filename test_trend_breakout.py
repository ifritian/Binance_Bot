import random

import pytest

import config, main, queue_manager, research_sweep as rs, research_v2 as r2, trend_breakout as tb


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


T0 = 1_700_000_000_000 - (1_700_000_000_000 % tb.BAR_MS)


def _walk(n, seed, drift=0.0, vol=0.01, start=100.0):
    rng = random.Random(seed)
    price, bars = start, []
    for i in range(n):
        o = price
        c = o * (1 + drift + rng.gauss(0, vol))
        bars.append(rs.Bar(T0 + i * tb.BAR_MS, o, max(o, c) * 1.003, min(o, c) * 0.997, c, 1e6))
        price = c
    return bars


def test_advance_matches_research_simulator():
    """Сопровождение позиции == simulate_trailing из исследования на тех же свечах."""
    for seed in range(8):
        bars = _walk(420, seed, drift=0.0004)
        i = 340
        ctx = r2.build_ctx(bars, {b.t: True for b in bars}, None, "X")
        ref = r2.simulate_trailing(ctx, lambda c, k, i=i: 1 if k == i else 0, tb.TRAIL_K, tb.MAX_HOLD)
        assert len(ref) == 1
        _, xi, ref_pct = ref[0]
        pos = tb.new_position(bars[i + 1].o, ctx["atr"][i], bars[i].t)
        pos, ex = tb.advance_position(pos, bars[i + 1:])
        if ex is None:  # серия кончилась раньше выхода - сравнивать нечего
            continue
        assert tb.net_pnl_pct(pos["entry"], ex["exit"]) == pytest.approx(ref_pct, abs=1e-9)
        assert bars[i + 1].t + (ex["held"] - 1) * tb.BAR_MS == bars[xi].t


def test_breakout_signal_needs_all_filters():
    bars = _walk(400, 3, drift=0.0003)
    bars[-1] = rs.Bar(bars[-1].t, bars[-1].o, bars[-1].o * 1.5, bars[-1].o * 0.99, bars[-1].o * 1.45, 1e6)
    assert tb.breakout_signal(bars, True) is not None
    assert tb.breakout_signal(bars, False) is None  # BTC не в аптренде
    assert tb.breakout_signal(bars[:100], True) is None  # мало истории
    flat = _walk(400, 3)
    below = max(b.h for b in flat[-121:-1]) * 0.98
    assert tb.breakout_signal(flat[:-1] + [rs.Bar(flat[-1].t, below, below * 1.001, below * 0.999, below, 1)], True) is None


def _fake_market(n_bars=400, breakout_sym="SOL"):
    now_ms = T0 + n_bars * tb.BAR_MS + 600_000  # 10 минут после закрытия последней свечи
    data = {}
    for k, s in enumerate(["BTC", "ETH", "SOL"]):
        bars = _walk(n_bars + 1, 10 + k, drift=0.002 if s == "BTC" else 0.0004)
        if s == breakout_sym:
            c = bars[n_bars - 1]
            bars[n_bars - 1] = rs.Bar(c.t, c.o, c.o * 1.6, c.o * 0.99, c.o * 1.55, 1e6)
            bars[n_bars] = rs.Bar(bars[n_bars].t, c.o * 1.55, c.o * 1.57, c.o * 1.5, c.o * 1.52, 1e6)
        data[s] = bars
    return now_ms, data


def test_cycle_opens_position_and_queues_entry(monkeypatch):
    monkeypatch.setattr(tb, "universe", lambda: ["BTC", "ETH", "SOL"])
    now_ms, data = _fake_market()
    tb.run_cycle(now_ms, fetch=lambda sym, limit=0: data[sym])
    st = tb._state()
    assert "SOL" in st["positions"] and st["last_bar_t"] == data["BTC"][-2].t
    ev = tb.pending_event(now_ms / 1000)
    assert ev["kind"] == "entry" and ev["ticker"] == "SOL" and ev["stop"] < ev["entry"]
    before = dict(st["positions"])
    tb.run_cycle(now_ms + 1000, fetch=lambda sym, limit=0: data[sym])  # та же свеча - ничего нового
    assert tb._state()["positions"] == before and len(tb._state()["outbox"]) == 1


def test_late_detection_skips_entries(monkeypatch):
    monkeypatch.setattr(tb, "universe", lambda: ["BTC", "ETH", "SOL"])
    now_ms, data = _fake_market()
    tb.run_cycle(now_ms + 3 * 3600 * 1000, fetch=lambda sym, limit=0: data[sym])
    assert tb._state()["positions"] == {}


def test_exit_recorded_with_loss_and_counted(monkeypatch):
    st = tb._state()
    st["positions"]["SOL"] = tb.new_position(100.0, 2.0, T0)
    tb._save(st)
    crash = [rs.Bar(T0 + tb.BAR_MS * k, 100, 100.5, 90 if k == 1 else 99, 99, 1e6) for k in range(1, 3)] \
        + [rs.Bar(T0 + 400 * tb.BAR_MS, 99, 99.5, 98.5, 99, 1e6)]
    btc = _walk(400, 1)
    now_ms = btc[-1].t + tb.BAR_MS + 1000
    monkeypatch.setattr(tb, "universe", lambda: [])
    tb.run_cycle(now_ms, fetch=lambda sym, limit=0: btc if sym == "BTC" else crash)
    st = tb._state()
    assert "SOL" not in st["positions"] and len(st["closed"]) == 1
    assert st["closed"][0]["pnl"] < 0
    stats = tb.live_stats()
    assert stats["n"] == 1 and stats["wins"] == 0
    text = tb.build_text(tb.pending_event(now_ms / 1000), stats, "en")
    assert "1 closed, 0 in profit" in text and "$SOL" in text and "{" not in text


def test_texts_both_languages_and_no_profit_promises():
    entry = {"kind": "entry", "ticker": "SOL", "entry": 150.0, "stop": 140.0, "risk_pct": 6.7, "ts": 1}
    exit_ = {"kind": "exit", "ticker": "SOL", "entry": 150.0, "exit": 162.0, "pnl": 7.8, "held": 12, "reason": "stop", "ts": 1}
    for lang in ("ru", "en"):
        for ev in (entry, exit_):
            for st in ({"n": 0, "wins": 0, "avg": 0.0}, {"n": 9, "wins": 4, "avg": 0.4}):
                t = tb.build_text(ev, st, lang)
                assert "$SOL" in t and "#SOL" in t and "{" not in t and "—" not in t
                assert ("Не финансовый совет" in t) if lang == "ru" else ("Not financial advice" in t)
                low = t.lower()
                assert "guaranteed" not in low and "гарант" not in low and "прибыльн" not in low


def test_publish_flow_marks_event_posted(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "TREND_BREAKOUT_ENABLED", True)
    monkeypatch.setattr(tb, "run_cycle", lambda *a, **k: None)
    st = tb._state()
    import time
    st["outbox"].append({"kind": "entry", "ticker": "SOL", "entry": 150.0, "stop": 140.0, "risk_pct": 6.7, "ts": time.time()})
    tb._save(st)
    monkeypatch.setattr(main, "_square_slot_available", lambda: True)
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_trend_breakout()
    assert len(sent) == 1 and tb._state()["outbox"] == []


def test_charts_render_for_entry_and_exit(tmp_path, monkeypatch):
    import chart_generator
    monkeypatch.setattr(chart_generator, "_CHARTS_DIR", tmp_path)
    bars = _walk(400, 3, drift=0.0006)
    entry = {"kind": "entry", "ticker": "SOL", "entry": bars[-1].c, "stop": bars[-1].c * 0.93,
             "level": bars[-1].c * 0.97, "entry_t": bars[-1].t, "risk_pct": 7.0, "ts": 1}
    path = tb.build_chart(entry, fetch=lambda s: bars)
    assert path and path.exists() and path.stat().st_size > 5000
    i = 370
    ex = {"kind": "exit", "ticker": "SOL", "entry": bars[i].c, "exit": bars[i + 8].c, "pnl": 3.2, "held": 9,
          "entry_t": bars[i].t, "stop0": bars[i].c * 0.93, "reason": "stop", "ts": 1}
    path2 = tb.build_chart(ex, fetch=lambda s: bars)
    assert path2 and path2.exists() and path2 != path


def test_chart_failure_returns_none_not_raises():
    assert tb.build_chart({"kind": "entry", "ticker": "SOL", "entry": 1.0, "stop": 0.9, "ts": 1}, fetch=lambda s: []) is None
