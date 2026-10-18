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
    st["positions"]["SOL"]["announced"] = True
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


def test_exit_not_posted_if_entry_was_never_announced(monkeypatch):
    st = tb._state()
    st["positions"]["SOL"] = tb.new_position(100.0, 2.0, T0)  # announced не выставлен
    tb._save(st)
    crash = [rs.Bar(T0 + tb.BAR_MS, 100, 100.5, 90, 99, 1e6), rs.Bar(T0 + 400 * tb.BAR_MS, 99, 99.5, 98.5, 99, 1e6)]
    btc = _walk(400, 1)
    monkeypatch.setattr(tb, "universe", lambda: [])
    tb.run_cycle(btc[-1].t + tb.BAR_MS + 1000, fetch=lambda sym, limit=0: btc if sym == "BTC" else crash)
    st = tb._state()
    assert len(st["closed"]) == 1 and st["outbox"] == []  # в счёт идёт, постить нечего


def test_events_are_batched_and_marked():
    import time
    now = time.time()
    st = tb._state()
    for k, t in enumerate(["SOL", "ADA", "XRP", "DOGE"]):
        st["positions"][t] = tb.new_position(10.0, 0.5, T0)
        st["outbox"].append({"kind": "entry", "ticker": t, "entry": 10.0, "stop": 8.25, "risk_pct": 17.5, "ts": now})
    tb._save(st)
    evs = tb.pending_events(now)
    assert [e["ticker"] for e in evs] == ["SOL", "ADA", "XRP"]  # не больше 3 тикеров на пост
    text = tb.build_group_text(evs, {"n": 0, "wins": 0, "avg": 0.0}, "en")
    assert text.count("$") == 3 and "Several coins" in text and "{" not in text
    tb.mark_posted(evs)
    assert [e["ticker"] for e in tb.pending_events(now)] == ["DOGE"]
    assert tb._state()["positions"]["SOL"]["announced"] and not tb._state()["positions"]["DOGE"].get("announced")


def test_stale_entry_events_dropped():
    import time
    now = time.time()
    st = tb._state()
    st["outbox"].append({"kind": "entry", "ticker": "SOL", "entry": 1.0, "stop": 0.9, "risk_pct": 10, "ts": now - 4 * 3600})
    tb._save(st)
    assert tb.pending_events(now) == [] and tb._state()["outbox"] == []


def test_group_text_exit_ru_and_en():
    evs = [{"kind": "exit", "ticker": t, "entry": 1.0, "exit": 1.1, "pnl": p, "held": 6, "reason": "stop", "ts": 1}
           for t, p in (("SOL", 4.2), ("ADA", -2.5))]
    for lang, marker in (("ru", "Идеи закрыты"), ("en", "Ideas closed")):
        t = tb.build_group_text(evs, {"n": 5, "wins": 2, "avg": -0.1}, lang)
        assert marker in t and "$SOL: +4.2%" in t and "$ADA: -2.5%" in t and "{" not in t


# ---------------- сводка "открытые идеи" ----------------

def _announced(sym, entry=100.0, stop_now=None, last=None, held=6):
    p = tb.new_position(entry, 2.0, T0)
    p.update({"announced": True, "held": held, "last_close": last if last is not None else entry})
    if stop_now is not None:
        p["stop"] = stop_now
    return sym, p


def test_update_needs_announced_idea_and_waits_for_interval():
    import time
    now = time.time()
    tb.maybe_queue_update(now)
    assert tb._state()["outbox"] == []                      # идей нет
    st = tb._state()
    k, p = _announced("SOL")
    st["positions"][k] = p
    st["last_idea_post_ts"] = now - 3600                    # недавно уже писали про идеи
    tb._save(st)
    tb.maybe_queue_update(now)
    assert tb._state()["outbox"] == []
    st = tb._state(); st["last_idea_post_ts"] = now - 25 * 3600; tb._save(st)
    tb.maybe_queue_update(now)
    ob = tb._state()["outbox"]
    assert len(ob) == 1 and ob[0]["kind"] == "update" and ob[0]["items"][0]["ticker"] == "SOL"
    tb.maybe_queue_update(now + 10)
    assert len(tb._state()["outbox"]) == 1                   # не дублируется


def test_update_not_queued_while_entry_or_exit_pending_and_has_lowest_priority():
    import time
    now = time.time()
    st = tb._state()
    k, p = _announced("SOL"); st["positions"][k] = p
    st["outbox"].append({"kind": "entry", "ticker": "ADA", "entry": 1.0, "stop": 0.9, "risk_pct": 10, "ts": now})
    tb._save(st)
    tb.maybe_queue_update(now)
    assert [e["kind"] for e in tb._state()["outbox"]] == ["entry"]
    st = tb._state()
    st["outbox"].append({"kind": "update", "ticker": "*", "ts": now, "items": []})
    tb._save(st)
    assert tb.pending_events(now)[0]["kind"] == "entry"


def test_update_text_shows_raised_and_unchanged_stops_and_rest(monkeypatch):
    items = [
        {"ticker": "SOL", "entry": 100.0, "last": 112.0, "pnl": 12.0, "stop": 105.0, "stop_prev": 90.0, "days": 3.5},
        {"ticker": "ADA", "entry": 1.0, "last": 0.98, "pnl": -2.0, "stop": 0.9, "stop_prev": 0.9, "days": 1.0},
        {"ticker": "XRP", "entry": 1.0, "last": 1.05, "pnl": 5.0, "stop": 0.95, "stop_prev": 0.9, "days": 2.0},
        {"ticker": "DOGE", "entry": 1.0, "last": 1.01, "pnl": 1.0, "stop": 0.9, "stop_prev": 0.9, "days": 2.0},
    ]
    ev = {"kind": "update", "ticker": "*", "ts": 1, "items": items}
    stats = {"n": 3, "wins": 1, "avg": -0.2}
    en = tb.build_group_text([ev], stats, "en")
    assert en.startswith("Open ideas: 4 running, 2 with a raised stop")
    assert "$SOL: +12.0% since entry before fees, stop raised to 105 (was 90)" in en
    assert "stop unchanged at 0.9" in en and "+1 more open" in en
    assert en.count("$") == 3 and "Stops only move up" in en and "Not financial advice" in en
    ru = tb.build_group_text([ev], stats, "ru")
    assert "стоп подтянут до 105 (был 90)" in ru and "ещё 1 в работе" in ru and "{" not in ru


def test_mark_posted_update_records_stop_and_resets_timer():
    import time
    st = tb._state()
    k, p = _announced("SOL", stop_now=105.0); st["positions"][k] = p
    ev = {"kind": "update", "ticker": "*", "ts": 5.0, "items": [{"ticker": "SOL", "stop": 105.0}]}
    st["outbox"].append(ev); tb._save(st)
    tb.mark_posted([ev])
    st = tb._state()
    assert st["positions"]["SOL"]["stop_at_post"] == 105.0 and st["outbox"] == []
    assert time.time() - st["last_idea_post_ts"] < 5


def test_advance_position_tracks_last_close():
    bars = [rs.Bar(T0 + tb.BAR_MS * k, 100, 101, 99.5, 100.5 + k, 1e6) for k in range(1, 4)]
    pos, ex = tb.advance_position(tb.new_position(100.0, 2.0, T0), bars)
    assert ex is None and pos["last_close"] == bars[-1].c
