import random
from datetime import datetime, timezone

import pytest

import config
import market_digest
import voice_variety
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
    assert "Не финансовый совет" in text
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
    monkeypatch.setattr(market_digest, "now_utc", lambda: datetime(2026, 10, 15, 7, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(market_digest, "fetch_movers", lambda rows=None: market_digest.fetch_movers.__wrapped__ if False else {
        "majors": [("BTC", 67000.0, 1.0)], "gainers": [("SUI", 2.5, 12.0)], "losers": [("DOGE", 0.15, -9.0)]})
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_market_digest()
    main.try_publish_market_digest()
    assert len(sent) == 1
    assert queue_manager._get("market_digest_done") == "2026-10-15:07"


def test_english_text(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    d = market_digest.fetch_movers(_rows())
    text = market_digest.build_digest_text(d, 17, random.Random(1))
    assert "$BTC" in text and "$SUI" in text and "+12.3%" in text
    assert any(h.split(" ", 1)[-1] in text for h in voice_variety.EVENING_HEADS)
    assert not any("а" <= ch <= "я" for ch in text.lower())


def test_compact_text_has_fewer_cashtags(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    d = market_digest.fetch_movers(_rows())
    full = market_digest.build_digest_text(d, 6, random.Random(1))
    small = market_digest.build_digest_text(d, 6, random.Random(1), compact=True)
    assert small.count("$") < full.count("$")
    assert small.count("$") <= 4  # BTC + лидер роста + лидер падения (+ возможный тикер в вопросе)


def test_digest_falls_back_to_compact_and_records_error(monkeypatch):
    sent = []
    monkeypatch.setattr(market_digest, "now_utc", lambda: datetime(2026, 10, 15, 7, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(market_digest, "fetch_movers", lambda rows=None: {
        "majors": [("BTC", 67000.0, 1.0), ("ETH", 3500.0, 0.5)],
        "gainers": [("SUI", 2.5, 12.0), ("SOL", 150.0, 8.0)], "losers": [("DOGE", 0.15, -9.0), ("ADA", 0.5, -6.0)]})

    def fake_publish(text, image_paths=None):
        if text.count("$") > 4:
            raise main.binance_publisher.PublishError("too many tags")
        sent.append(text)
        return {}
    monkeypatch.setattr(main.binance_publisher, "publish_post", fake_publish)
    main.try_publish_market_digest()
    assert len(sent) == 1
    errs = queue_manager._get("publish_errors")
    assert errs and errs[0]["fmt"] == "market_digest" and "too many tags" in errs[0]["error"]


def test_digest_texts_vary(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    d = market_digest.fetch_movers(_rows())
    texts = {market_digest.build_digest_text(d, 6, random.Random(i)) for i in range(30)}
    assert len(texts) >= 15
    heads = {t.split("\n")[0] for t in texts}
    assert len(heads) >= 6


def test_fng_texts_vary(monkeypatch):
    import event_posts
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    d = {"value": 55, "label": "Greed", "prev": 59}
    texts = {event_posts.build_fng_text(d, random.Random(i)) for i in range(40)}
    assert len(texts) >= 15
    assert all("$BTC" in t for t in texts)
