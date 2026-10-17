import random
import time

import pytest

import config
import post_format
import treasury_generator as tg
import treasury_index as ti


def _fake(coin, ph):
    pct = {"SOL": 5.0, "AVAX": 1.0, "NEAR": 9.3, "ARB": -4.1}.get(coin["ticker"], 0.5)
    return ti.CoinChange(coin["ticker"], coin["weight"], pct, coin["ticker"] + "USDT")


def _result():
    return ti.compute_index(72, fetch_fn=_fake)


def test_basket_post_is_plain_english_without_jargon():
    r = _result()
    hist = {"launch_at": time.time() - 12 * 86400, "index_value": 104.2, "btc_value": 101.1, "eth_value": 102.0}
    text = tg.build_basket_post(r, 72, 1.3, 2.1, hist, ti.compute_breadth(r), random.Random(1))
    assert "altcoin basket" in text and "Best: $NEAR +9.3%" in text and "Worst: $ARB -4.1%" in text
    assert "Since we started tracking" in text and "Not financial advice." in text
    assert "3 days" in text
    for jargon in ("тир", "Treasury", "Фундамент", "Рост", "Риск", "Index"):
        assert jargon not in text
    assert text.count("$") <= 3  # лимит Square на $-тикеры


def test_basket_headline_reflects_direction():
    r = _result()
    up = tg.build_basket_post(r, 24, r.total_pct - 3, None, None, None, random.Random(1))
    down = tg.build_basket_post(r, 24, r.total_pct + 3, None, None, None, random.Random(1))
    flat = tg.build_basket_post(r, 24, r.total_pct, None, None, None, random.Random(1))
    assert up.startswith("Alts beat BTC by 3.0 pts over 24h")
    assert down.startswith("BTC beat alts by 3.0 pts over 24h")
    assert flat.startswith("Alts and BTC moved together")


def test_basket_post_without_benchmarks_still_valid():
    text = tg.build_basket_post(_result(), 12, None, None, None, None, random.Random(2))
    assert text.startswith("Altcoin basket over 12h") and "{" not in text


def test_heatmap_and_chart_labels_are_english():
    import treasury_chart
    labels = [v["label"] for v in treasury_chart._LINE_STYLE.values()]
    assert "Altcoin basket" in labels and not any("Рынок" in l for l in labels)
    assert set(ti.PLAIN_TIER_NAMES.values()) == {"Large caps", "Mid caps", "High risk"}


def test_treasury_defaults_are_every_three_days():
    assert config.TREASURY_INTERVAL_HOURS == 72


def test_english_opinion_hook_modes_cover_all_voices():
    assert set(post_format.HOOK_MODES_EN) == set(post_format.HOOK_MODES)
    assert all(not any("а" <= ch <= "я" for ch in v.lower()) for v in post_format.HOOK_MODES_EN.values())


def test_hypothetical_off_by_default():
    assert config.HYPOTHETICAL_ENABLED is False
