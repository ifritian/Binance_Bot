import random

import config
import event_posts as ep
import main
import market_digest


def test_airdrop_title_parsed_and_post_is_hedged(monkeypatch):
    assert ep.parse_airdrop_title("Binance HODLer Airdrops Announces Kite (KITE) and Will List It") == ("Kite", "KITE")
    assert ep.parse_airdrop_title("Binance Will List Foo (FOO)") is None
    payload = {"data": {"catalogs": [{"articles": [
        {"code": "a", "title": "Binance HODLer Airdrops Announces Kite (KITE)", "releaseDate": 1_700_000_000_000},
        {"code": "b", "title": "Binance Will List Foo (FOO) with Seed Tag", "releaseDate": 1_700_000_100_000},
        {"code": "c", "title": "Maintenance notice", "releaseDate": 1_700_000_200_000}]}]}}
    items = ep.fetch_listing_announcements(1_700_000_500.0, 6, payload=payload)
    assert {i["ticker"]: i["kind"] for i in items} == {"KITE": "airdrop", "FOO": "listing"}
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    txt = ep.build_announcement_text(next(i for i in items if i["kind"] == "airdrop"), random.Random(1))
    assert "$KITE" in txt and txt.count("$") == 1 and "Not financial advice" in txt and "guarantee" not in txt.lower()


def test_fast_lane_polls_repeatedly_and_survives_errors(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(main.time, "time", lambda: clock[0])
    monkeypatch.setattr(main.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(main, "try_publish_listing_announcement", lambda: calls.append("l"))
    monkeypatch.setattr(main, "try_publish_hot_mover", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(main, "try_publish_binance_alerts", lambda: calls.append("a"))
    main.fast_lane(seconds=200, interval=45)
    assert calls.count("l") == 4 and calls.count("a") == 4          # цикл не прервался из-за ошибки в hot_mover
    calls.clear(); main.fast_lane(seconds=0); assert calls == []


def test_snapshots_not_added_more_often_than_every_5_minutes(monkeypatch):
    from market_digest import update_snapshots
    monkeypatch.setattr(market_digest.coin_whitelist, "allowed_tickers", lambda: {"SUI"})
    rows = [{"symbol": "SUIUSDT", "lastPrice": "2", "priceChangePercent": "1", "quoteVolume": "2e8"}]
    s = update_snapshots(rows, 10_000.0, None)
    s = update_snapshots(rows, 10_045.0, s)
    assert len(s) == 1
    s = update_snapshots(rows, 10_400.0, s)
    assert len(s) == 2
