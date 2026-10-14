#!/usr/bin/env python3
"""
Тесты П4: гарантированный $TICKER и теги для постов на Binance Square.
- post_format.ensure_cashtag
- post_format.strategy_topic_tag / square_hashtags_line(topic=...)
- main: тикер для мнений по ВСЕМ темам (а не только BTC/ETH)
"""
import pytest

import config
import main
import opinion_generator
import post_format


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Тесты main.try_publish_* доходят до voice_memory/queue_manager и
    пишут в SQLite - без изоляции они засоряли бы настоящую bot_state.db."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_state.db")


# --- ensure_cashtag ---

def test_ensure_cashtag_adds_when_missing_in_hook():
    text = "Пробой вверх, объём растёт\n\n🟢 Лонг | Donchian Breakout\nВход: 1 - 2"
    result = post_format.ensure_cashtag(text, "ARB")
    assert result.startswith("$ARB Пробой вверх")
    assert result.endswith("Вход: 1 - 2")


def test_ensure_cashtag_keeps_text_when_present_in_hook():
    text = "$ARB откатился к поддержке\n\nблок"
    assert post_format.ensure_cashtag(text, "ARB") == text


def test_ensure_cashtag_is_case_insensitive():
    text = "$sol держится выше уровня"
    assert post_format.ensure_cashtag(text, "SOL") == text


def test_ensure_cashtag_longer_ticker_is_not_a_match():
    # $SOLANA - это не $SOL
    result = post_format.ensure_cashtag("$SOLANA растёт", "SOL")
    assert result.startswith("$SOL $SOLANA")


def test_ensure_cashtag_only_first_paragraph_counts_by_default():
    text = "Хук без тикера\n\n🟢 $ARB (лонг)"
    assert post_format.ensure_cashtag(text, "ARB").startswith("$ARB Хук")


def test_ensure_cashtag_anywhere_accepts_ticker_in_block():
    text = "Хук без тикера\n\n🟢 $ARB (лонг)"
    assert post_format.ensure_cashtag(text, "ARB", anywhere=True) == text


def test_ensure_cashtag_ignores_empty_or_weird_ticker():
    assert post_format.ensure_cashtag("текст", None) == "текст"
    assert post_format.ensure_cashtag("текст", "") == "текст"
    assert post_format.ensure_cashtag("текст", "BAD TICKER!") == "текст"


def test_ensure_cashtag_accepts_ticker_with_digits_and_prefix_symbols():
    assert post_format.ensure_cashtag("текст", "$1000pepe").startswith("$1000PEPE ")


# --- теги ---

def test_strategy_topic_tag_maps_known_strategies():
    assert post_format.strategy_topic_tag("Donchian Breakout") == "#Breakout"
    assert post_format.strategy_topic_tag("MACD Crossover") == "#MACD"
    assert post_format.strategy_topic_tag("RSI + Bollinger Touch") == "#RSI"
    assert post_format.strategy_topic_tag("Что-то новое") is None
    assert post_format.strategy_topic_tag(None) is None


def test_hashtags_line_uses_topic_when_given():
    assert post_format.square_hashtags_line("ARB", topic="#Breakout") == "#ARB #Breakout"


def test_hashtags_line_falls_back_to_general_tag():
    line = post_format.square_hashtags_line("ARB")
    assert line.startswith("#ARB ")
    assert line.split(" ")[1] in post_format._SQUARE_GENERAL_HASHTAGS


def test_hashtags_line_none_without_ticker():
    assert post_format.square_hashtags_line(None, topic="#Breakout") is None


def test_general_hashtags_can_be_configured_from_env(monkeypatch):
    monkeypatch.setenv("SQUARE_GENERAL_HASHTAGS", "Alpha, #Beta ,,")
    import importlib
    import config
    reloaded = importlib.reload(config)
    try:
        assert reloaded.SQUARE_GENERAL_HASHTAGS == ["#Alpha", "#Beta"]
    finally:
        monkeypatch.delenv("SQUARE_GENERAL_HASHTAGS")
        importlib.reload(config)


# --- _do_publish: тематический тег по стратегии ---

class _Sig:
    strategy = "Donchian Breakout"


def test_do_publish_uses_strategy_topic_tag(monkeypatch):
    monkeypatch.setattr(main.post_format, "maybe_binance_cta", lambda: None)
    monkeypatch.setattr(main, "_schedule_crossposts", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, **k: calls.append(text))

    main._do_publish("сигнал", None, ticker="ARB", signal=_Sig())

    assert calls[0].startswith("$ARB сигнал")
    assert calls[0].endswith("#ARB #Breakout")


# --- мнения: тикер для всех тем, а не только BTC/ETH ---

def _run_opinion(monkeypatch, theme):
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda post_type: 10 ** 9)
    monkeypatch.setattr(main.queue_manager, "get_jitter_seconds", lambda post_type: 0)
    monkeypatch.setattr(main.queue_manager, "should_retry_now", lambda post_type: True)
    monkeypatch.setattr(main.opinion_generator, "pick_theme", lambda last: theme)
    monkeypatch.setattr(main.opinion_generator, "generate_opinion_post",
                         lambda t, hook_mode=None: ("мнение без тикера.", set(), 1.5))
    monkeypatch.setattr(main.opinion_generator, "validate_opinion_post_text", lambda text, nums: (True, ""))
    calls = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, **k: calls.append(text))
    main.try_publish_opinion_post()
    return calls


def test_opinion_single_ticker_themes_all_get_cashtag_and_hashtag(monkeypatch):
    single = [t for t, v in opinion_generator.THEMES.items() if len(v["tickers"]) == 1]
    assert len(single) >= 10  # BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, DOT
    for theme in single:
        calls = _run_opinion(monkeypatch, theme)
        assert len(calls) == 1, theme
        assert calls[0].startswith(f"${theme} "), theme
        assert f"#{theme}" in calls[0], theme


def test_opinion_market_theme_has_no_single_cashtag(monkeypatch):
    calls = _run_opinion(monkeypatch, "market")
    assert len(calls) == 1
    assert not calls[0].startswith("$")
    assert any(tag in calls[0] for tag in post_format._SQUARE_GENERAL_HASHTAGS)
