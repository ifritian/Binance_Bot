import random
from datetime import datetime, timezone

import pytest

import config, main, event_posts, queue_manager


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def _payload(now_ms, age_h=1.0):
    return {"data": {"catalogs": [{"articles": [
        {"code": "a1", "title": "Binance Will List Foo Token (FOO) with Seed Tag Applied", "releaseDate": now_ms - age_h * 3600_000},
        {"code": "a2", "title": "Binance Will Add Bar on Earn", "releaseDate": now_ms - 1000},
        {"code": "a3", "title": "Binance Will List Old (OLD)", "releaseDate": now_ms - 30 * 3600_000},
    ]}]}}


def test_parse_listing_title():
    assert event_posts.parse_listing_title("Binance Will List Foo Token (FOO) with Seed Tag Applied") == ("Foo Token", "FOO")
    assert event_posts.parse_listing_title("Binance Will Add Bar on Earn") is None


def test_fetch_announcements_filters_age_and_type():
    now = 1_800_000_000.0
    items = event_posts.fetch_listing_announcements(now, payload=_payload(now * 1000))
    assert [i["ticker"] for i in items] == ["FOO"]
    assert event_posts.fetch_listing_announcements(now, payload={"oops": 1}) == []


def test_announcement_text(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    t = event_posts.build_announcement_text({"name": "Foo Token", "ticker": "FOO"}, random.Random(1))
    assert "$FOO" in t and "#FOO" in t and "Not financial advice." in t
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "ru")
    t = event_posts.build_announcement_text({"name": "Foo Token", "ticker": "FOO"}, random.Random(1))
    assert "$FOO" in t and "анонсировала" in t


def test_fng_text(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    t = event_posts.build_fng_text({"value": 23, "label": "Extreme Fear", "prev": 31}, random.Random(2))
    assert "23 (Extreme Fear)" in t and "Yesterday: 31 (-8)" in t and "?" in t
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "ru")
    t = event_posts.build_fng_text({"value": 70, "label": "Greed", "prev": 70}, random.Random(2))
    assert "жадность" in t and "без изменений" in t


def test_fng_publish_once_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(event_posts, "now_utc", lambda: datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(event_posts, "fetch_fng", lambda: {"value": 40, "label": "Fear", "prev": 38})
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_fng_post()
    queue_manager.set_last_post_time("square_global", 0)  # снимаем общий антидребезг
    main.try_publish_fng_post()
    assert len(sent) == 1 and queue_manager._get("fng_done") == "2026-10-16"


def test_announcement_dedup(monkeypatch):
    sent = []
    item = {"code": "a1", "name": "Foo", "ticker": "FOO", "ts": 1.0}
    monkeypatch.setattr(event_posts, "fetch_listing_announcements", lambda now_ts: [item])
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    main.try_publish_listing_announcement()
    queue_manager.set_last_post_time("square_global", 0)
    queue_manager.set_last_post_time("announcement", 0)
    main.try_publish_listing_announcement()
    assert len(sent) == 1


def test_templates_clean():
    import cliche_filter
    for t in (event_posts.build_fng_text({"value": 20, "label": "Extreme Fear", "prev": 25}, random.Random(3)),
              event_posts.build_announcement_text({"name": "Foo", "ticker": "FOO"}, random.Random(3))):
        assert "—" not in t and cliche_filter.check_cliches(t)[0]
