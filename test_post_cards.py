from pathlib import Path

import pytest

import chart_generator
import config
import main
import market_digest
import post_cards


@pytest.fixture(autouse=True)
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(chart_generator, "_CHARTS_DIR", tmp_path / "charts")
    monkeypatch.setattr(main, "_square_slot_available", lambda: True)


DATA = {"majors": [("BTC", 81616.0, -0.8), ("ETH", 2740.5, 1.9)],
        "gainers": [("TIA", 3.1, 7.4), ("SUI", 1.9, 5.2)], "losers": [("ARB", 0.4, -5.1)]}


def test_digest_card_renders_even_with_only_gainers():
    p = post_cards.digest_card(DATA, 6)
    assert p and p.exists() and p.stat().st_size > 5000
    only_up = {"majors": DATA["majors"][:1], "gainers": DATA["gainers"], "losers": []}
    assert post_cards.digest_card(only_up, 17).exists()


def test_fng_card_renders_for_extremes_and_missing_prev():
    for v, prev in ((0, None), (18, 25), (100, 99)):
        p = post_cards.fng_card({"value": v, "label": "x", "prev": prev})
        assert p and p.exists() and p.stat().st_size > 5000


def test_cards_return_none_on_bad_data():
    assert post_cards.digest_card({"majors": []}, 6) is None
    assert post_cards.fng_card({}) is None


def test_hot_mover_and_fng_posts_get_images(monkeypatch):
    sent = []
    monkeypatch.setattr(main.binance_publisher, "publish_post",
                        lambda text, image_paths=None: sent.append((text, image_paths)) or {"id": "1"})
    monkeypatch.setattr(market_digest, "pick_hot_mover", lambda *a, **k: ("SUI", 2.5, 12.0))
    fake_chart = Path("/tmp/fake_chart.png")
    monkeypatch.setattr(main, "_coin_chart", lambda ticker, days, expected_price=None: fake_chart)
    main.try_publish_hot_mover()
    assert sent and sent[0][1] == [fake_chart]

    sent.clear()
    monkeypatch.setattr(main.event_posts, "now_utc", lambda: __import__("datetime").datetime(2026, 10, 17, 12, tzinfo=__import__("datetime").timezone.utc))
    monkeypatch.setattr(main.event_posts, "fetch_fng", lambda: {"value": 55, "label": "Greed", "prev": 63})
    main.try_publish_fng_post()
    assert sent and sent[0][1] and sent[0][1][0].name == "fng_card.png"


def test_digest_post_gets_card_and_survives_card_failure(monkeypatch):
    sent = []
    monkeypatch.setattr(main.binance_publisher, "publish_post",
                        lambda text, image_paths=None: sent.append(image_paths) or {"id": "1"})
    monkeypatch.setattr(market_digest, "fetch_movers", lambda: DATA)
    monkeypatch.setattr(market_digest, "due_slot", lambda *a, **k: 6)
    main.try_publish_market_digest()
    assert sent[0] and sent[0][0].name == "digest_card.png"

    sent.clear()
    config_slot = main.queue_manager._set("market_digest_done", None)
    monkeypatch.setattr(post_cards, "digest_card", lambda *a, **k: None)
    main.try_publish_market_digest()
    assert sent == [None]  # без картинки, но пост вышел


def test_coin_chart_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(chart_generator, "generate_chart_image", boom)
    assert main._coin_chart("SUI", 2) is None
