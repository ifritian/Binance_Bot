import time

import pytest

import config
import news_radar as nr
import post_cards
import queue_manager
import coin_whitelist

NOW = 1_792_150_000.0


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_manager, "DB_PATH", str(tmp_path / "t.db"), raising=False)
    monkeypatch.setattr(coin_whitelist, "is_allowed", lambda t: True)


def _payload(*arts):
    return {"data": {"catalogs": [{"articles": [
        {"code": c, "title": t, "releaseDate": int(ts * 1000)} for c, t, ts in arts]}]}}


def test_parse_cms_filters_age_and_skips_new_listings():
    p = _payload(("a", "Binance Will Delist EPIC, QUICK on 2026-10-28", NOW - 3600),
                 ("b", "Old thing", NOW - 99 * 3600))
    out = nr.parse_cms(p, "delist", NOW, 48)
    assert [i["code"] for i in out] == ["a"] and out[0]["tickers"] == ["EPIC", "QUICK"]
    p2 = _payload(("c", "Binance Will List Foo (FOO) with Seed Tag", NOW - 60))
    assert nr.parse_cms(p2, "listing", NOW, 48) == []


def test_parse_cms_bad_payload():
    assert nr.parse_cms({}, "news", NOW, 48) == []


def test_parse_rss():
    xml = ("<rss><channel><item><title>Bitcoin jumps</title><link>http://x/1</link>"
           "<pubDate>Sun, 18 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>")
    ts = 1_792_317_600.0  # 2026-10-18 10:00 UTC
    out = nr.parse_rss(xml, "CoinDesk", ts + 600, 10)
    assert out and out[0]["source"] == "CoinDesk"
    assert nr.parse_rss("<broken", "X", ts, 10) == []


def test_coin_in_headline_and_tickers():
    assert nr.coin_in_headline("Why Solana and Bitcoin rally") == "SOL"
    assert nr.coin_in_headline("Nothing here") is None
    assert nr.extract_tickers("Network Upgrade (FIL) & Hard Fork") == ["FIL"]


def test_radar_selection_rules():
    mk = lambda c, k, d: {"code": c, "title": "T " + c, "ts": NOW - d, "kind": k, "tickers": []}
    items = [mk("n", "news", 10), mk("d", "delist", 500), mk("e", "event", 20)]
    picked = nr.pick_radar_items(items, {"e"})
    assert [i["code"] for i in picked] == ["d", "n"]
    assert nr.radar_worth_posting(picked)
    assert not nr.radar_worth_posting([mk("x", "news", 1)])
    assert nr.radar_worth_posting([mk("x", "delist", 1)])


def test_news_move_picks_largest_and_respects_seen():
    news = [{"id": "1", "title": "Bitcoin ETF flows", "ts": 1, "source": "A"},
            {"id": "2", "title": "Solana outage ends", "ts": 2, "source": "B"},
            {"id": "3", "title": "Cardano update", "ts": 3, "source": "C"}]
    ch = {"BTC": 1.0, "SOL": -6.0, "ADA": 4.0}
    n, t, p = nr.pick_news_move(news, ch, set(), 3.0)
    assert t == "SOL" and p == -6.0
    n, t, p = nr.pick_news_move(news, ch, {"2"}, 3.0)
    assert t == "ADA"
    assert nr.pick_news_move(news, {"BTC": 1.0}, set(), 3.0) is None


def test_texts_limits_and_no_claims():
    items = [{"code": "d", "title": "Binance Will Delist EPIC, QUICK on 2026-10-28", "ts": 1, "kind": "delist",
              "tickers": ["EPIC", "QUICK"]}] + [
        {"code": str(i), "title": f"Thing {i} (T{i}AA)", "ts": 1, "kind": "news", "tickers": [f"T{i}AA"]} for i in range(4)]
    txt = nr.build_radar_text(items, {"EPIC": -12.34, "T0AA": 1.0, "T1AA": 2.0, "T2AA": 3.0})
    assert txt.count("$") <= 3 and "−12.3%" in txt and "Not financial advice" in txt
    nt = nr.build_news_text({"title": "Solana outage ends", "source": "B"}, "SOL", -6.0,
                            {"title": "Binance Will Support X (SOL) Upgrade - 2026-10-19"})
    assert nt.count("$") == 1 and "On Binance: Binance Will Support X (SOL) Upgrade" in nt and "may or may not be related" in nt


def test_radar_card_renders():
    items = [{"code": "d", "title": "Binance Will Delist EPIC, QUICK on 2026-10-28", "ts": 1, "kind": "delist", "tickers": []},
             {"code": "n", "title": "Binance Launches Covered Call & Cash-Secured Put", "ts": 1, "kind": "news", "tickers": []}]
    p = post_cards.radar_card(items)
    assert p and p.exists()


def test_clean_title_drops_dangling_preposition():
    assert nr.clean_title("Binance Will Delist EPIC, QUICK on 2026-10-28") == "Binance Will Delist EPIC, QUICK"
