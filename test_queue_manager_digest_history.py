#!/usr/bin/env python3
"""
Тесты queue_manager.log_signal_history/get_digest_history - в первую
очередь новое поле "price" (см. article_generator.py docstring: цена
на момент публикации сигнала теперь сохраняется в истории дайджестов,
чтобы article_generator мог давать LLM реальную цену вместо того,
чтобы LLM придумывала её "из головы").
"""
import pytest

import config
import queue_manager
from signal_parser import RsiSignal


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Каждый тест пишет в свой временный sqlite-файл, а не в реальный
    bot_state.db рядом с кодом (тот же паттерн, что и в
    test_news_opinion_generator.py)."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_bot_state.db")


def _make_signal(**overrides) -> RsiSignal:
    base = dict(
        ticker="BEAT", timeframe="15m", strategy="RSI + Bollinger Touch",
        direction="Шорт", current_price="2.225", rsi_now="81.74", score="89",
        quality="Conservative", entry_low="2.205", entry_high="2.2178",
        invalidation="2.2371", target="2.1729", change_24h="+35.67%",
        volume="57.67M", rsi_live="82.64", created_at="2026-06-23 22:44:59 EEST",
        description="desc", raw_text="raw",
    )
    base.update(overrides)
    return RsiSignal(**base)


def test_log_signal_history_stores_price():
    queue_manager.log_signal_history(_make_signal(current_price="2.225"))

    history = queue_manager.get_digest_history(since_seconds_ago=3600)

    assert len(history) == 1
    assert history[0]["price"] == "2.225"


def test_log_signal_history_stores_all_expected_fields():
    queue_manager.log_signal_history(_make_signal(
        ticker="BTC", direction="Лонг", strategy="RSI + Bollinger Touch",
        change_24h="+5.10%", score="77", current_price="67890.5",
    ))

    [record] = queue_manager.get_digest_history(since_seconds_ago=3600)

    assert record["ticker"] == "BTC"
    assert record["direction"] == "Лонг"
    assert record["change_pct"] == "+5.10%"
    assert record["score"] == "77"
    assert record["price"] == "67890.5"
    assert "ts" in record


def test_get_digest_history_respects_since_seconds_ago(monkeypatch):
    import time as time_module

    queue_manager.log_signal_history(_make_signal())

    # Записи старше окна не должны возвращаться - симулируем "давно"
    # напрямую подменив время в самой сохранённой записи через новую
    # запись с явным ts в прошлом было бы сложнее (log_signal_history
    # сам ставит time.time()), поэтому проверяем через маленькое окно
    # в будущем, что "живая" запись видна сейчас.
    history_now = queue_manager.get_digest_history(since_seconds_ago=60)
    assert len(history_now) == 1

    history_far_future_cutoff = queue_manager.get_digest_history(since_seconds_ago=-1)
    assert history_far_future_cutoff == []
