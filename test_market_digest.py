import random
from datetime import datetime, timezone

import pytest

import config
import market_digest
import main
import queue_manager


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def _rows():
    coins = {"BTC": (67000, 1.2), "ETH": (3500, -0.5), "SOL": (150, 8.0), "SUI": (2.5, 12.3),
             "LINK": (15, 5.0), "ADA": (0.5, -6.0), "DOGE": (0.15, -9.1), "XRP": (0.6, -3.0),
             "TIA": (5, 0.3), "FAKEXYZ": (1, 99.0)}
    return [{"symbol": f"{t}USDT", "lastPrice": str(p), "priceChangePercent": str(c), "quoteVolume": "100000000"}
            for t, (p, c) in coins.items()]


def test_movers_only_whitelist_and_sorted():
    d = market_digest.fetch_movers(_rows())
    assert [m[0] for m in d["majors"]] == ["BTC", "ETH"]
    assert d["gainers"][0][0] == "SUI"
    assert d["losers"][0][0] == "DOGE"
    assert all(t != "FAKEXYZ" for t, _, _ in d["gainers"])


def test_no_data_returns_none():
    assert market_digest.fetch_movers([]) is None


def test_text_has_cashtags_question_disclaimer(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "ru")
    d = market_digest.fetch_movers(_rows())
    text = market_digest.build_digest_text(d, 6, random.Random(1))
    assert "Утренний" in text and "$SUI" in text and "$DOGE" in text and "?" in text
    assert "не финансовая рекомендация" in text
    assert text.strip().splitlines()[-1].startswith("#")
    assert "Вечерний" in market_digest.build_digest_text(d, 17, random.Random(1))


def test_due_slot():
    t = lambda h: datetime(2026, 10, 15, h, 5, tzinfo=timezone.utc)
    assert market_digest.due_slot(t(5), [6, 17], None) is None
    assert market_digest.due_slot(t(6), [6, 17], None) == 6
    assert market_digest.due_slot(t(6), [6, 17], "2026-10-15:06") is None
    assert market_digest.due_slot(t(17), [6, 17], "2026-10-15:06") == 17
    assert market_digest.due_slot(t(22), [6, 17], None) is None  # протух


def test_publish_once(monkeypatch):
    sent = []
    monkeypatch.setattr(market_digest, "now_utc", lambda: datetime(2026, 10, 15, 6, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(market_digest, "fetch_movers", lambda rows=None: market_digest.fetch_movers.__wrapped__ if False else {
        "majors": [("BTC", 67000.0, 1.0)], "gainers": [("SUI", 2.5, 12.0)], "losers": [("DOGE", 0.15, -9.0)]})
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_market_digest()
    main.try_publish_market_digest()
    assert len(sent) == 1
    assert queue_manager._get("market_digest_done") == "2026-10-15:06"


def test_english_text(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    d = market_digest.fetch_movers(_rows())
    text = market_digest.build_digest_text(d, 17, random.Random(1))
    assert "Evening market snapshot" in text and "Top gainers" in text and "not financial advice" in text
    assert not any("а" <= ch <= "я" for ch in text.lower())
