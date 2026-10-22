import pytest
import config, main, market_digest, coin_whitelist


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
    assert "$SOL" in t and "#SOL" in t and "−15.0%" in t and "{" not in t and "Не финансовый совет" in t


def test_publish_limits(monkeypatch):
    sent = []
    monkeypatch.setattr(market_digest, "fetch_ticker_rows", lambda: [])
    monkeypatch.setattr(market_digest, "pick_hot_event", lambda *a, **k: {"ticker": "SUI", "price": 2.5, "pct24": 12.0, "fast_pct": None, "fast_min": None})
    monkeypatch.setattr(main.config, "MOVER_SETUPS_ENABLED", False)
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_hot_mover()
    main.try_publish_hot_mover()  # блок общего интервала
    assert len(sent) == 1


def test_text_en(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    t = market_digest.build_hot_text("SOL", 150.0, -15.0)
    assert "$SOL" in t and "−15.0%" in t and "financial advice" in t and "{" not in t
    assert not any("а" <= ch <= "я" for ch in t.lower())


def test_publish_with_setup_registers_idea(monkeypatch):
    import mover_setups
    sent, reg = [], []
    monkeypatch.setattr(market_digest, "fetch_ticker_rows", lambda: [])
    monkeypatch.setattr(market_digest, "pick_hot_event", lambda *a, **k: {"ticker": "SUI", "price": 2.5, "pct24": 12.0, "fast_pct": None, "fast_min": None})
    monkeypatch.setattr(main.config, "SQUARE_TEMPLATE_LANG", "en")
    monkeypatch.setattr(main.config, "MOVER_SETUPS_ENABLED", True)
    bars = [(i * 3600_000, 2.5, 2.55, 2.45, 2.5) for i in range(40)]
    monkeypatch.setattr(mover_setups, "fetch_klines", lambda *a, **k: bars)
    monkeypatch.setattr(mover_setups, "register", lambda s, now=None: reg.append(s))
    monkeypatch.setattr(main, "_mover_setup_chart", lambda s: None)
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_hot_mover()
    assert len(sent) == 1 and "Entry:" in sent[0] and "Stop:" in sent[0] and "Target:" in sent[0]
    assert len(reg) == 1 and reg[0]["ticker"] == "SUI"


def test_publish_levels_post_by_default(monkeypatch):
    import mover_setups
    sent = []
    monkeypatch.setattr(market_digest, "fetch_ticker_rows", lambda: [])
    monkeypatch.setattr(market_digest, "pick_hot_event", lambda *a, **k: {"ticker": "SUI", "price": 2.5, "pct24": 12.0, "fast_pct": None, "fast_min": None})
    monkeypatch.setattr(main.config, "SQUARE_TEMPLATE_LANG", "en")
    monkeypatch.setattr(main.config, "MOVER_SETUPS_ENABLED", False)
    bars = [(i * 3600_000, 2.5, 2.55, 2.45, 2.5, 100.0) for i in range(80)]
    monkeypatch.setattr(mover_setups, "fetch_klines", lambda *a, **k: bars)
    monkeypatch.setattr(main, "_mover_levels_chart", lambda lv: None)
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_hot_mover()
    assert len(sent) == 1 and "Entry:" not in sent[0] and "Target" not in sent[0] and "$SUI" in sent[0]
    assert "24h" in sent[0]


def test_fast_detector_catches_hourly_spike_before_24h_threshold(monkeypatch):
    monkeypatch.setattr(coin_whitelist, "is_allowed", lambda t: True)
    monkeypatch.setattr(coin_whitelist, "allowed_tickers", lambda: {"SUI", "SOL"})
    now = 1_000_000.0
    rows = [{"symbol": "SUIUSDT", "lastPrice": "2.06", "priceChangePercent": "3.0", "quoteVolume": "2e8"},
            {"symbol": "SOLUSDT", "lastPrice": "150", "priceChangePercent": "2.0", "quoteVolume": "9e8"}]
    snaps = [[now - 3600, {"SUI": 2.0, "SOL": 150.0}], [now - 600, {"SUI": 2.05, "SOL": 150.0}]]
    fast = market_digest.fast_changes(rows, snaps, now)
    assert round(fast["SUI"][0], 1) == 3.0 and fast["SUI"][1] == 60 and fast["SOL"][0] == 0
    ev = market_digest.pick_hot_event(rows, {}, now, min_pct=6, fast=fast, fast_pct=3.0)
    assert ev["ticker"] == "SUI" and ev["fast_pct"] is not None and ev["pct24"] == 3.0
    assert market_digest.pick_hot_event(rows, {}, now, min_pct=6, fast=fast, fast_pct=5.0) is None
    assert market_digest.pick_hot_event(rows, {"SUI": now - 5}, now, min_pct=6, fast=fast, fast_pct=3.0) is None


def test_snapshots_pruned_and_text_mentions_window(monkeypatch):
    monkeypatch.setattr(coin_whitelist, "allowed_tickers", lambda: {"SUI"})
    rows = [{"symbol": "SUIUSDT", "lastPrice": "2", "priceChangePercent": "1", "quoteVolume": "2e8"}]
    snaps = market_digest.update_snapshots(rows, 10_000.0, [[1.0, {"SUI": 1}], [9_000.0, {"SUI": 1.9}]])
    assert [s[0] for s in snaps] == [9_000.0, 10_000.0]
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    txt = market_digest.build_hot_text("SUI", 2.0, 3.0, fast_pct=4.2, fast_min=60)
    assert "last hour" in txt and "+4.2%" in txt and "24 hours" not in txt


def test_mover_poll_limited_to_once_per_day(monkeypatch, tmp_path):
    import queue_manager
    monkeypatch.setattr(queue_manager, "DB_PATH", str(tmp_path / "p.db"), raising=False)
    polls = []
    monkeypatch.setattr(main.telegram_publisher, "is_configured", lambda: True)
    monkeypatch.setattr(main.telegram_publisher, "publish_poll", lambda q, o: polls.append((q, o)) or {})
    main._telegram_mover_poll("SUI", 4.2, 60)
    main._telegram_mover_poll("SOL", 5.0, 60)
    assert len(polls) == 1 and "SUI is +4.2% in the last hour" in polls[0][0] and len(polls[0][1]) == 4
