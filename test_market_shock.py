import random
from pathlib import Path

import market_shock as ms


def _rows(pcts):
    out = [{"symbol": "BTCUSDT", "lastPrice": "70000", "priceChangePercent": str(pcts[0]), "quoteVolume": "9e9"}]
    for i, p in enumerate(pcts[1:], 1):
        out.append({"symbol": f"C{i}USDT", "lastPrice": "1.5", "priceChangePercent": str(p), "quoteVolume": str(8e9 - i * 1e8)})
    return out


def _coins(pcts):
    return ms.collect(_rows(pcts), set(), 1e8)


def test_quiet_market_no_event():
    assert ms.detect(_coins([0.5, -0.3, 0.2, 1.0, -1.0, 0.4, 0.1, -0.2, 0.3, 0.6]), 0.2) is None


def test_crash_by_btc_day():
    ev = ms.detect(_coins([-5, -6, -4, -7, -3, -6, -5, -4, -8, -2]), -0.3)
    assert ev and ev["kind"] == "crash" and ev["breadth"] == 1.0


def test_rally_by_fast_move():
    ev = ms.detect(_coins([1.0, 2, 3, 1, 2, 4, 1, 2, 3, 1]), 2.5)
    assert ev and ev["kind"] == "rally"


def test_text_has_no_links_and_uses_unicode_minus():
    ev = ms.detect(_coins([-5, -6, -4, -7, -3, -6, -5, -4, -8, -2]), -3.0)
    t = ms.build_text(ev, lambda x: f"{x:.0f}", random.Random(1))
    assert "http" not in t and "-5" not in t and "Not financial advice" in t


def test_chart_created(tmp_path):
    coins = _coins([-5, -6, -4, -7, -3, -6, -5, -4, -8, -2])
    ev = ms.detect(coins, -3.0)
    assert ms.chart(ev, coins, Path(tmp_path) / "c.png").exists()
