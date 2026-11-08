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
