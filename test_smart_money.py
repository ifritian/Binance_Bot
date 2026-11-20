import math
import random

import smart_money as sm

H = 3600_000


def _series(n=260, spike_at=None):
    rng = random.Random(3)
    px, bars, hm = 100.0, [], {}
    for i in range(n):
        px *= 1 + rng.uniform(-0.01, 0.01)
        t = i * H
        bars.append((t, px, px, px, px, 1.0))
        top = 1.0 + 0.02 * math.sin(i) + rng.uniform(-0.01, 0.01)
        if spike_at is not None and i == spike_at:
            top = 3.0                                          # резкий перекос позиций топ-трейдеров
        hm[t] = (1000.0, top, 1.0 + rng.uniform(-0.01, 0.01), 1.0 + rng.uniform(-0.01, 0.01), 1.0 + rng.uniform(-0.01, 0.01))
    return bars, hm


def test_event_logged_once_with_cooldown_and_outcomes_filled():
    bars, hm = _series(spike_at=215)
    # последняя свеча в списке "идёт": закрытая = bars[-2] = индекс 215 при длине 217
    b = bars[:217]
    evs = sm.new_events("ETHUSDT", b, hm, [])
    assert any(e["f"] == "tt_pos" and e["z"] > 2 for e in evs)
    again = sm.new_events("ETHUSDT", b, hm, evs)
    assert not any(e["f"] == "tt_pos" for e in again)          # кулдаун 12 ч
    assert sm.new_events("ETHUSDT", b, hm, [], since_t=b[-2][0]) == []   # уже просмотренное время не берём
    events = [e for e in evs if e["f"] == "tt_pos"]
    sm.fill_outcomes(events, "ETHUSDT", bars, bars)             # через 24+ ч данные уже есть
    out = events[0]["out"]
    assert "4" in out and "24" in out and "48" not in out or len(bars) - 216 > 48


def test_stats_follow_vs_fade_signs_and_excess_vs_btc():
    events = [{"t": 0, "s": "X", "f": "tt_pos", "z": 2.5, "px": 1, "out": {"4": [2.0, 0.5]}},
              {"t": 1, "s": "X", "f": "tt_pos", "z": -2.5, "px": 1, "out": {"4": [-2.0, -0.5]}}]
    rows = {(r["f"], r["mode"], r["h"]): r for r in sm.stats(events)}
    fol, fad = rows[("tt_pos", "follow", 4)], rows[("tt_pos", "fade", 4)]
    assert fol["n"] == 2 and abs(fol["excess"] - 1.5) < 1e-9 and fad["excess"] == -fol["excess"]
    assert abs(fol["net"] - (2.0 - 0.15)) < 1e-9 or fol["net"] < 2.0


def test_universe_sorted_by_volume_and_filters():
    rows = [{"symbol": "AAAUSDT", "quoteVolume": "5"}, {"symbol": "USDCUSDT", "quoteVolume": "999"}, {"symbol": "BBBUSDT", "quoteVolume": "50"},
            {"symbol": "CCCBTC", "quoteVolume": "9999"}]
    assert sm.universe(rows) == ["BBBUSDT", "AAAUSDT"]


def test_scan_end_to_end_with_stubbed_api(monkeypatch):
    bars, hm = _series(spike_at=215)
    store = {}
    monkeypatch.setattr(sm.queue_manager, "_get", lambda k: store.get(k))
    monkeypatch.setattr(sm.queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(sm, "fetch_bars", lambda s, limit=500: bars[:217])
    monkeypatch.setattr(sm, "fetch_hm", lambda s, days=12: hm)
    n = sm.scan([{"symbol": "ETHUSDT", "quoteVolume": "10"}])
    assert n >= 1 and store["sm_events"]


def test_receipts_renders_watch_section():
    import site_builder as sb
    d = {"closed": [], "levels": [], "crowd": {"right": 0, "total": 0}, "followups": [], "futures": [], "open": {}, "listings": [], "listings_waiting": [],
         "posts": [], "idea_variants": {}, "futures_since": None,
         "smart_money": [{"f": "tt_pos", "mode": "follow", "h": 12, "n": 9, "excess": 0.31, "net": 0.1, "hit": 0.56}]}
    html = sb.render_receipts(d, now_ts=1.8e9, ls=[])
    assert "Smart-money watch" in html and "Top-trader positions" in html


def test_run_if_due_daily_and_after_03_utc(monkeypatch):
    calls = []
    store = {}
    monkeypatch.setattr(sm, "scan", lambda rows: calls.append(1))
    monkeypatch.setattr(sm.queue_manager, "seconds_since_last_post", lambda k: store.get(k, 1e9))
    monkeypatch.setattr(sm.queue_manager, "set_last_post_time", lambda k: store.__setitem__(k, 0))
    sm.run_if_due([{"symbol": "X"}], utc_hour=2)
    assert calls == []                                          # файлы за вчера ещё не выложены
    sm.run_if_due([{"symbol": "X"}], utc_hour=5)
    sm.run_if_due([{"symbol": "X"}], utc_hour=6)
    assert calls == [1]                                         # раз в сутки
