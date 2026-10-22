import random

import pytest

import binance_publisher as bp
import config
import main
import market_digest
import queue_manager


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def test_post_log_records_format_tag_once(monkeypatch):
    monkeypatch.setattr(bp, "_send_content", lambda body: {"id": "9"})
    main._post("digest", "$BTC text")
    bp.publish_post("untagged")           # метка не "прилипает" к следующему посту
    log = queue_manager._get("square_post_log")
    assert [e["fmt"] for e in log] == ["digest", None]


def test_every_square_format_is_tagged():
    import inspect
    for fn, fmt in (("try_publish_market_digest", "digest"), ("try_publish_square_question", "question"),
                    ("try_publish_trend_breakout", "breakout"), ("try_publish_hot_mover", "hot_mover"),
                    ("try_publish_fng_post", "fear_greed"), ("try_publish_listing_announcement", "listing"),
                    ("try_publish_opinion_post", "opinion"), ("try_publish_treasury_post", "altcoin_basket")):
        assert f'_post("{fmt}"' in inspect.getsource(getattr(main, fn)), fn


DATA = {"majors": [("BTC", 81616.0, -0.8)], "gainers": [("TIA", 3.1, 7.4)], "losers": [("ARB", 0.4, -5.1)]}


def test_follow_line_probability_and_compact(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    monkeypatch.setattr(config, "SQUARE_FOLLOW_LINE_PROBABILITY", 1.0)
    full = market_digest.build_digest_text(DATA, 6, random.Random(1))
    assert "Follow to get the 07:00 and 13:00 UTC snapshots daily." in full
    assert "Follow" not in market_digest.build_digest_text(DATA, 6, random.Random(1), compact=True)
    monkeypatch.setattr(config, "SQUARE_FOLLOW_LINE_PROBABILITY", 0.0)
    assert "Follow" not in market_digest.build_digest_text(DATA, 6, random.Random(1))
    assert full.count("$") == 3
