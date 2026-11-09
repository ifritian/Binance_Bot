import random

import quick_setup as qs


def _bars(n=60):
    return [(i, 100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1, 1.0) for i in range(n)]


def test_build_modes_and_thresholds():
    assert qs.build("SOL", 150.0, 2.0, _bars(), 55.0) is None                    # мало движения
    assert qs.build("PEPE", 150.0, 3.2, _bars(), 55.0)["variant"] == "q2.0/1.0/12h"   # для мема порог ниже
    assert qs.build("SOL", 150.0, 3.2, _bars(), 55.0) is None
    m = qs.build("SOL", 150.0, 6.0, _bars(), 60.0)
    assert m["mode"] == "momentum" and m["side"] == "long" and m["rr"] == 1.0
    f = qs.build("SOL", 150.0, 8.0, _bars(), 86.0)                               # перегрев: идея против движения
    assert f["mode"] == "fade" and f["side"] == "short" and f["variant"] == "qf3.0/2.0/72h"


def test_text_is_short_and_complete():
    s = qs.build("PEPE", 150.0, -7.0, _bars(), 15.0)
    for i in range(20):
        t = qs.build_text(s, rng=random.Random(i), rsi=qs.rsi_info(15.0))
        lines = t.split("\n")
        assert len(lines) <= 11 and "entry:" in t and "Stop-loss:" in t and "Take-profit:" in t
        assert "Not financial advice." in t and lines[-1] == "$PEPE #PEPE #Crypto" and "http" not in t


def test_pick_candidates_prefers_memes_and_skips_recent():
    rows = [{"symbol": "PEPEUSDT", "quoteVolume": "9e7", "priceChangePercent": "-5", "lastPrice": "1"},
            {"symbol": "SOLUSDT", "quoteVolume": "9e8", "priceChangePercent": "9", "lastPrice": "100"},
            {"symbol": "DOGEUSDT", "quoteVolume": "9e8", "priceChangePercent": "1", "lastPrice": "1"},
            {"symbol": "ADAUSDT", "quoteVolume": "9e8", "priceChangePercent": "6", "lastPrice": "1"}]
    c = qs.pick_candidates(rows, {"PEPE", "SOL", "DOGE", "ADA"}, {}, 1e6, "post about $ADA", 3e7, 1e8)
    assert [x[2] for x in c] == ["PEPE", "SOL"]
    assert qs.pick_candidates(rows, {"PEPE"}, {"PEPE": 1e6 - 3600}, 1e6, "", 3e7, 1e8) == []


def test_try_publish_quick_setup_end_to_end(monkeypatch, tmp_path):
    import main
    import queue_manager as qm
    posted = {}
    monkeypatch.setattr(main.config, "QUICK_SETUP_ENABLED", True)
    monkeypatch.setattr(main.config, "SQUARE_TEMPLATE_LANG", "en")
    monkeypatch.setattr(main, "_square_slot_available", lambda *a, **k: True)
    monkeypatch.setattr(main.market_digest, "fetch_ticker_rows", lambda: [
        {"symbol": "PEPEUSDT", "quoteVolume": "9e7", "priceChangePercent": "-6", "lastPrice": "100"}])
    monkeypatch.setattr(main.mover_setups, "fetch_klines", lambda *a, **k: _bars(72))
    monkeypatch.setattr(main, "_mover_setup_chart", lambda s, title=None: None)
    monkeypatch.setattr(main, "_post", lambda fmt, text, image_paths=None: posted.update(fmt=fmt, text=text))
    registered = []
    monkeypatch.setattr(main.mover_setups, "register_with_shadows", lambda s, sh, now: registered.append(s))
    store = {}
    monkeypatch.setattr(qm, "_get", lambda k: store.get(k))
    monkeypatch.setattr(qm, "_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(qm, "should_retry_now", lambda k: True)
    monkeypatch.setattr(qm, "seconds_since_last_post", lambda k: 1e9)
    monkeypatch.setattr(qm, "set_last_post_time", lambda *a, **k: None)
    main.try_publish_quick_setup()
    assert posted["fmt"] == "quick_setup" and "Stop-loss:" in posted["text"] and registered
    assert "PEPE" in store["quick_setup_state"]["coins"]
