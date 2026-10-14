#!/usr/bin/env python3
"""
Тесты П5 и П6:
- coin_whitelist: белый список монет для публикации на Square
- queue_manager.get_pending_post(allowed_fn=...): фильтр при выборе поста
- signal_explainer: объяснение "простыми словами"
- post_format.assemble_signal_post_short / _explained: новые форматы
- main: режим "с объяснением" раз в SIGNAL_EXPLAIN_INTERVAL_HOURS
"""
import dataclasses
import random

import pytest

import coin_whitelist
import config
import cliche_filter
import main
import post_format
import queue_manager
import signal_explainer
import validator
from signal_parser import RsiSignal


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Тесты не должны писать в настоящую bot_state.db."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_state.db")


def _signal(**overrides) -> RsiSignal:
    base = dict(
        ticker="ARB", timeframe="15m", strategy="Donchian Breakout",
        direction="Лонг (пробой диапазона вверх)", current_price="0.1236", rsi_now="н/д", score="91",
        quality="Conservative", entry_low="0.1236", entry_high="0.1238",
        invalidation="0.1200", target="0.1300", change_24h="+3.1%",
        volume="8.70M", rsi_live="н/д", created_at="2026-10-14 10:00:00 UTC",
        description="desc", raw_text="raw",
    )
    base.update(overrides)
    return RsiSignal(**base)


# ============================================================
# coin_whitelist
# ============================================================

def test_whitelist_contains_majors_and_known_alts():
    for ticker in ("BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "DOGE", "ADA", "LINK", "AVAX", "PEPE"):
        assert coin_whitelist.is_allowed(ticker), ticker


def test_whitelist_rejects_pump_coins_from_real_signal_history():
    for ticker in ("MARSCOIN", "KORUB", "TUT", "AAOIB", "MSTRB", "HEMI", "MUBARAK"):
        assert not coin_whitelist.is_allowed(ticker), ticker


def test_whitelist_has_no_delisted_tokens():
    # делистинг спота (август 2026) и более ранние объявления
    for ticker in ("ACX", "HFT", "PIVX", "PYR", "VANRY", "VIC", "BIFI", "FIO", "FUN", "OXT", "MDT", "WAN"):
        assert not coin_whitelist.is_allowed(ticker), ticker


def test_whitelist_is_reasonably_sized_and_clean():
    tickers = coin_whitelist.DEFAULT_WHITELIST
    assert 60 <= len(tickers) <= 120
    assert all(t == t.upper() and t.isalnum() for t in tickers)
    assert "USDT" not in tickers and "USDC" not in tickers  # стейблкоины не нужны


def test_whitelist_is_case_insensitive_and_strips_symbols():
    assert coin_whitelist.is_allowed("btc")
    assert coin_whitelist.is_allowed("$ETH")
    assert coin_whitelist.is_allowed(" sol ")
    assert not coin_whitelist.is_allowed(None)
    assert not coin_whitelist.is_allowed("")


def test_whitelist_extra_and_exclude_from_config(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_COIN_WHITELIST_EXTRA", "pump, $kaito ,")
    monkeypatch.setattr(config, "SQUARE_COIN_WHITELIST_EXCLUDE", "DOGE")
    assert coin_whitelist.is_allowed("PUMP")
    assert coin_whitelist.is_allowed("KAITO")
    assert not coin_whitelist.is_allowed("DOGE")
    assert coin_whitelist.is_allowed("BTC")


def test_whitelist_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_COIN_WHITELIST_ENABLED", False)
    assert coin_whitelist.is_allowed("MARSCOIN")


# ============================================================
# queue_manager.get_pending_post(allowed_fn=...)
# ============================================================

def _queue_item(kind="signal", **overrides):
    if kind == "signal":
        payload = dataclasses.asdict(_signal(**overrides))
    else:
        payload = {"ticker": overrides.get("ticker", "ARB"), "direction": "up", "note": "n", "image_url": "http://x/img.png"}
    return {"kind": kind, "payload": payload, "attempts": 0}


def _patch_queue(monkeypatch, items):
    monkeypatch.setattr(queue_manager, "_get_queue", lambda: items)
    monkeypatch.setattr(queue_manager, "get_recent_tickers", lambda: [])


def test_pending_post_skips_coins_rejected_by_filter(monkeypatch):
    items = [_queue_item(ticker="MARSCOIN", score="99"), _queue_item(ticker="SOL", score="92")]
    _patch_queue(monkeypatch, items)

    idx, kind, payload = queue_manager.get_pending_post(min_score=90, allowed_fn=coin_whitelist.is_allowed)

    assert (idx, payload.ticker) == (1, "SOL")  # у MARSCOIN score выше, но он не из списка


def test_pending_post_none_when_only_rejected_coins_left(monkeypatch):
    _patch_queue(monkeypatch, [_queue_item(ticker="MARSCOIN", score="99"), _queue_item(ticker="KORUB", score="95")])
    assert queue_manager.get_pending_post(min_score=90, allowed_fn=coin_whitelist.is_allowed) is None


def test_pending_post_without_filter_behaves_as_before(monkeypatch):
    _patch_queue(monkeypatch, [_queue_item(ticker="MARSCOIN", score="99"), _queue_item(ticker="SOL", score="92")])
    idx, _, payload = queue_manager.get_pending_post(min_score=90)
    assert payload.ticker == "MARSCOIN"


def test_pending_post_filter_also_applies_to_image_fallback(monkeypatch):
    _patch_queue(monkeypatch, [_queue_item(kind="image", ticker="MARSCOIN")])
    assert queue_manager.get_pending_post(min_score=90, allowed_fn=coin_whitelist.is_allowed) is None
    _patch_queue(monkeypatch, [_queue_item(kind="image", ticker="SOL")])
    assert queue_manager.get_pending_post(min_score=90, allowed_fn=coin_whitelist.is_allowed) is not None


def test_try_publish_currency_post_passes_whitelist_filter(monkeypatch):
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda t="currency": 10 ** 9)
    monkeypatch.setattr(main.queue_manager, "get_jitter_seconds", lambda t: 0)
    monkeypatch.setattr(main, "_square_slot_available", lambda: True)
    seen = {}

    def fake_pending(min_score=0, allowed_fn=None):
        seen["allowed_fn"] = allowed_fn
        return None

    monkeypatch.setattr(main.queue_manager, "get_pending_post", fake_pending)
    main.try_publish_currency_post()
    assert seen["allowed_fn"] is coin_whitelist.is_allowed


# ============================================================
# signal_explainer
# ============================================================

def test_risk_reward_percents_long_and_short():
    # середина входа 0.1237; стоп 0.1200 -> риск ~2.99%; тейк 0.1300 -> ~5.09%
    risk, reward = signal_explainer.risk_reward_percents(_signal())
    assert risk == pytest.approx(3.0, abs=0.1) and reward == pytest.approx(5.1, abs=0.1)

    short = _signal(direction="Шорт (пробой вниз)", entry_low="100", entry_high="100", invalidation="102", target="94")
    assert signal_explainer.risk_reward_percents(short) == (2.0, 6.0)


def test_risk_reward_percents_none_on_bad_data():
    assert signal_explainer.risk_reward_percents(_signal(invalidation="н/д")) == (None, None)
    assert signal_explainer.risk_reward_percents(_signal(entry_low="100", entry_high="100", invalidation="100")) == (None, None)


_CASES = [
    ("Donchian Breakout", "Лонг (пробой вверх)"), ("Donchian Breakout", "Шорт (пробой вниз)"),
    ("MACD Crossover", "Лонг (бычье пересечение MACD)"), ("MACD Crossover", "Шорт (медвежье пересечение MACD)"),
    ("RSI + Bollinger Touch", "Лонг (перепроданность)"), ("RSI + Bollinger Touch", "Шорт (перекупленность)"),
    ("RSI + Divergence", "Лонг (дивергенция)"), ("Неизвестная стратегия", "Лонг (что-то)"),
]


@pytest.mark.parametrize("strategy,direction", _CASES)
def test_explanation_is_plain_russian_for_every_strategy_and_direction(strategy, direction):
    long_ = direction.startswith("Лонг")
    signal = _signal(
        strategy=strategy, direction=direction,
        entry_low="100", entry_high="100",
        invalidation="98" if long_ else "102", target="106" if long_ else "94",
    )
    for seed in range(6):  # все варианты шаблонов
        text = signal_explainer.explain_signal(signal, rng=random.Random(seed))
        assert "2%" in text and "6%" in text  # проценты посчитаны кодом
        assert not validator.find_suspicious_english_words(text), text
        assert cliche_filter.check_cliches(text)[0], text
        for jargon in ("Дончиан", "MACD", "RSI", "Боллинджер", "Bollinger", "score", "Score"):
            assert jargon not in text, (jargon, text)


def test_explanation_mentions_stop_and_target_levels_from_signal():
    text = signal_explainer.explain_signal(_signal(), rng=random.Random(1))
    assert "0.1200" in text or "0.1300" in text


def test_explanation_without_numbers_falls_back_gracefully():
    text = signal_explainer.explain_signal(_signal(invalidation="н/д"), rng=random.Random(0))
    assert "%" not in text and "стоп" in text.lower()


def test_explanation_is_not_overlong():
    for seed in range(20):
        assert len(signal_explainer.explain_signal(_signal(), rng=random.Random(seed))) < 600


# ============================================================
# post_format: короткий и "с объяснением"
# ============================================================

def test_short_post_has_levels_but_no_jargon():
    text = post_format.assemble_signal_post_short("$ARB пробивает потолок 🔥", _signal())
    assert text.startswith("$ARB пробивает потолок")
    assert "🟢 Лонг | Вход: 0.1236 - 0.1238" in text
    assert "Стоп: 0.1200 | Тейк: 0.1300" in text
    assert text.endswith(post_format.DISCLAIMER)
    for banned in ("Score", "score", "RSI", "Donchian", "Breakout", "R:R", "Quality"):
        assert banned not in text, banned


def test_short_post_is_much_shorter_than_old_format():
    signal = _signal()
    old = post_format.assemble_signal_post("Хук.", signal)
    new = post_format.assemble_signal_post_short("Хук.", signal)
    assert len(new.splitlines()) < len(old.splitlines())


def test_short_post_short_direction_uses_red_marker():
    text = post_format.assemble_signal_post_short("Хук.", _signal(direction="Шорт (пробой вниз)"))
    assert "🔴 Шорт | Вход:" in text


def test_short_post_single_entry_value_is_not_repeated():
    text = post_format.assemble_signal_post_short("Хук.", _signal(entry_low="5", entry_high="5"))
    assert "Вход: 5\n" in text


def test_explained_post_adds_plain_block_before_disclaimer():
    text = post_format.assemble_signal_post_explained("Хук.", _signal(), rng=random.Random(2))
    assert "💡 Простыми словами:" in text
    assert text.index("Тейк:") < text.index("💡") < text.index(post_format.DISCLAIMER)
    assert text.endswith(post_format.DISCLAIMER)


@pytest.mark.parametrize("builder", ["assemble_signal_post_short", "assemble_signal_post_explained"])
def test_new_formats_pass_validator_without_indicators(builder):
    signal = _signal()
    text = getattr(post_format, builder)("Монета ожила, смотрим внимательно.", signal)
    ok, reason = validator.validate_post_text(text, signal, require_indicators=False)
    assert ok, reason


def test_validator_still_requires_indicators_by_default():
    signal = _signal(rsi_now="81.74", score="89")
    text = post_format.assemble_signal_post_short("Хук.", signal)
    ok, reason = validator.validate_post_text(text, signal)  # старое поведение
    assert not ok and "RSI" in reason


def test_validator_still_checks_levels_without_indicators():
    signal = _signal()
    text = post_format.assemble_signal_post_short("Хук.", signal).replace("0.1300", "0.9999")
    ok, reason = validator.validate_post_text(text, signal, require_indicators=False)
    assert not ok and "тейк" in reason


# ============================================================
# main: режим "с объяснением" по расписанию
# ============================================================

def test_should_explain_first_time_and_after_interval(monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_EXPLAIN_INTERVAL_HOURS", 48.0)
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda t: float("inf"))
    assert main._should_explain_signal() is True
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda t: 49 * 3600)
    assert main._should_explain_signal() is True


def test_should_not_explain_before_interval_or_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_EXPLAIN_INTERVAL_HOURS", 48.0)
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda t: 47 * 3600)
    assert main._should_explain_signal() is False
    monkeypatch.setattr(config, "SIGNAL_EXPLAIN_INTERVAL_HOURS", 0.0)
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda t: float("inf"))
    assert main._should_explain_signal() is False


def _wire_publish_signal(monkeypatch, explain, published=True):
    marks = []
    monkeypatch.setattr(main.post_format, "pick_hook_mode", lambda last: "technician")
    monkeypatch.setattr(main, "_should_explain_signal", lambda: explain)
    seen = {}

    def fake_generate(signal, mode, explain=False):
        seen["explain"] = explain
        return "текст", "хук"

    monkeypatch.setattr(main.text_generator, "generate_post_text", fake_generate)
    monkeypatch.setattr(main.validator, "validate_post_text", lambda t, s, **k: seen.setdefault("kw", k) and (True, ""))
    monkeypatch.setattr(main.chart_generator, "generate_chart_image", lambda *a, **k: "fake.png")
    monkeypatch.setattr(main, "_do_publish", lambda *a, **k: (published, None))
    monkeypatch.setattr(main.outcome_tracker, "record_signal_outcome", lambda *a, **k: None)
    monkeypatch.setattr(main.voice_memory, "record_post", lambda *a, **k: None)
    monkeypatch.setattr(main.queue_manager, "set_last_hook_mode", lambda m: None)
    monkeypatch.setattr(main.queue_manager, "set_last_post_time", lambda key: marks.append(key))
    return seen, marks


def test_publish_signal_marks_explain_time_only_for_explained_posts(monkeypatch):
    seen, marks = _wire_publish_signal(monkeypatch, explain=True)
    assert main._publish_signal(_signal()) is True
    assert seen["explain"] is True and "signal_explain" in marks
    assert seen["kw"] == {"require_indicators": False}


def test_publish_signal_short_mode_does_not_touch_explain_clock(monkeypatch):
    seen, marks = _wire_publish_signal(monkeypatch, explain=False)
    assert main._publish_signal(_signal()) is True
    assert seen["explain"] is False and "signal_explain" not in marks


def test_failed_explained_publish_does_not_reset_explain_clock(monkeypatch):
    seen, marks = _wire_publish_signal(monkeypatch, explain=True, published=False)
    assert main._publish_signal(_signal()) is False
    assert "signal_explain" not in marks


# ============================================================
# config: пустая строка (так GitHub передаёт незаданный секрет)
# ============================================================

def test_config_empty_env_values_fall_back_to_defaults(monkeypatch):
    import importlib
    monkeypatch.setenv("SIGNAL_EXPLAIN_INTERVAL_HOURS", "")
    monkeypatch.setenv("SQUARE_GENERAL_HASHTAGS", "")
    monkeypatch.setenv("SQUARE_COIN_WHITELIST_ENABLED", "")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.SIGNAL_EXPLAIN_INTERVAL_HOURS == 48.0
        assert reloaded.SQUARE_GENERAL_HASHTAGS == ["#Crypto", "#Trading", "#Altcoins", "#CryptoTrading"]
        assert reloaded.SQUARE_COIN_WHITELIST_ENABLED is True
    finally:
        monkeypatch.undo()
        importlib.reload(config)
