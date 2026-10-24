import anchors as a
from datetime import datetime, timezone, timedelta


def test_due_windows():
    e = a.EVENTS[0]
    assert a.due(e["t"] - timedelta(hours=20), [])[0] == "pre"
    assert a.due(e["t"] - timedelta(hours=20), ["pre:" + e["id"]]) is None
    assert a.due(e["t"] + timedelta(hours=3), ["pre:" + e["id"]])[0] == "post"
    assert a.due(e["t"] - timedelta(hours=40), []) is None


def test_expiry_is_last_friday():
    for e in a.expiries(2026):
        assert e["t"].weekday() == 4 and (e["t"] + timedelta(days=7)).month != e["t"].month


def test_reaction_and_history():
    bars = [(0, 100, 102, 99, 101, 1)] * 3 + [(0, 101, 104, 100, 103, 1)] * 5
    r = a.reaction(bars)
    assert round(r["move"], 1) == 3.0
    assert a.reaction(bars[:2]) is None
    assert a.history_summary([r, r]) is None and a.history_summary([r, r, r])["n"] == 3


def test_market_news_pick():
    import news_radar as nr
    news = [{"id": "1", "title": "Local festival opens", "source": "X", "ts": 3}, {"id": "2", "title": "SEC approves new spot ETF", "source": "CoinDesk", "ts": 2}]
    assert nr.pick_market_news(news, set())["id"] == "2"
    assert nr.pick_market_news(news, {"2"}) is None
    t = nr.build_market_news_text(news[1], -2.5)
    assert t.count("$") == 1 and "−2.5%" in t
