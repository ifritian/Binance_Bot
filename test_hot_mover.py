import pytest
import config, main, market_digest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def _r(t, p, c, v="500000000"):
    return {"symbol": f"{t}USDT", "lastPrice": str(p), "priceChangePercent": str(c), "quoteVolume": v}


def test_pick_and_cooldown():
    rows = [_r("SUI", 2.5, 12), _r("SOL", 150, -15), _r("BTC", 1, 2), _r("FAKEXYZ", 1, 90), _r("ADA", .5, 30, "1000")]
    assert market_digest.pick_hot_mover(rows, {}, 1e6)[0] == "SOL"
    assert market_digest.pick_hot_mover(rows, {"SOL": 1e6 - 5}, 1e6)[0] == "SUI"
    assert market_digest.pick_hot_mover([_r("BTC", 1, 2)], {}, 1e6) is None


def test_text(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "ru")
    t = market_digest.build_hot_text("SOL", 150.0, -15.0)
    assert "$SOL" in t and "#SOL" in t and "−15.0%" in t and "{" not in t and "не финансовая" in t


def test_publish_limits(monkeypatch):
    sent = []
    monkeypatch.setattr(market_digest, "pick_hot_mover", lambda rows=None, recent=None, now_ts=0, cooldown_s=0: ("SUI", 2.5, 12.0))
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_hot_mover()
    main.try_publish_hot_mover()  # блок общего интервала
    assert len(sent) == 1


def test_text_en(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    t = market_digest.build_hot_text("SOL", 150.0, -15.0)
    assert "$SOL" in t and "−15.0%" in t and "not financial advice" in t and "{" not in t
    assert not any("а" <= ch <= "я" for ch in t.lower())
