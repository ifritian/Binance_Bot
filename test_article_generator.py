#!/usr/bin/env python3
"""
Тесты article_generator.py - в первую очередь новая логика цены в
фактах для статьи недели (см. docstring модуля): цена берётся из
signal.current_price (уже была в данных, без доп. API-вызова),
пробрасывается в промпт и в allowed_numbers валидатора, чтобы LLM
могла сослаться на реальное число вместо того, чтобы придумывать
цену "из головы" (обучающие данные) - именно это раньше приводило к
ложному отбраковыванию статьи (см. validate_article_text).

Раньше тестов на этот модуль не было вообще.
"""
import article_generator


def _fact(**overrides):
    base = dict(
        ticker="BTC", timeframe="4h", direction="Лонг",
        strategy="RSI + Bollinger Touch", change_pct="+3.45%",
        score="82", price="67890.5", ts=1_700_000_000.0,
    )
    base.update(overrides)
    return base


# --- _parse_price ----------------------------------------------------------

def test_parse_price_plain_dot():
    assert article_generator._parse_price("67890.5") == 67890.5


def test_parse_price_comma_decimal():
    # Сигнал иногда приходит в русской локали (запятая как разделитель) -
    # тот же паттерн, что и в main.py._publish_signal.
    assert article_generator._parse_price("2,225") == 2.225


def test_parse_price_missing_returns_none():
    assert article_generator._parse_price(None) is None


def test_parse_price_garbage_returns_none():
    assert article_generator._parse_price("н/д") is None


# --- _format_facts -----------------------------------------------------------

def test_format_facts_includes_price_when_present():
    facts = article_generator._format_facts([_fact(price="67890.5")])
    assert "цена 67890.5" in facts
    assert "$BTC" in facts


def test_format_facts_marks_missing_price_explicitly():
    fact = _fact()
    del fact["price"]
    facts = article_generator._format_facts([fact])
    assert "цена н/д" in facts


def test_format_facts_handles_unparseable_price_as_missing():
    facts = article_generator._format_facts([_fact(price="н/д")])
    assert "цена н/д" in facts


# --- validate_article_text ----------------------------------------------------

def _valid_body(extra: str = "") -> str:
    return f"Отличная неделя для BTC. {extra}\n\n{article_generator.DISCLAIMER}"


def test_validate_article_text_accepts_real_price_from_history():
    history = [_fact(price="67890.5", score="82", change_pct="+3.45%")]
    body = _valid_body("Цена на момент сигнала была 67890.5, score 82, движение +3.45%.")
    ok, reason = article_generator.validate_article_text("Итоги недели", body, history)
    assert ok, reason


def test_validate_article_text_rejects_price_not_in_history():
    # Ровно тот сценарий из бага: LLM пишет правдоподобную, но не
    # подтверждённую фактами цену (взятую "из головы", а не из истории).
    history = [_fact(price="67890.5", score="82", change_pct="+3.45%")]
    body = _valid_body("BTC уверенно торговался около 140000.")
    ok, reason = article_generator.validate_article_text("Итоги недели", body, history)
    assert not ok
    assert "не из истории" in reason


def test_validate_article_text_backward_compatible_with_records_without_price():
    # Записи истории, залогированные до этого изменения - без ключа
    # "price" вообще (см. docstring article_generator) - не должны
    # приводить к KeyError, просто у LLM не будет цены для этого факта.
    fact = _fact()
    del fact["price"]
    body = _valid_body("Score 82, движение +3.45%.")
    ok, reason = article_generator.validate_article_text("Итоги недели", body, [fact])
    assert ok, reason


def test_validate_article_text_still_rejects_when_no_disclaimer():
    history = [_fact()]
    ok, reason = article_generator.validate_article_text("Итоги недели", "Просто текст без ничего.", history)
    assert not ok
    assert "дисклеймер" in reason


# --- generate_weekly_article: факты, переданные в промпт ------------------

def test_generate_weekly_article_passes_price_to_prompt(monkeypatch):
    captured = {}

    def _fake_call_groq(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "ЗАГОЛОВОК: Итоги недели\nСТАТЬЯ:\nВсё прошло отлично."

    monkeypatch.setattr(article_generator, "call_groq", _fake_call_groq)

    history = [_fact(ticker="BTC", price="67890.5"), _fact(ticker="ETH", price="3200"), _fact(ticker="SOL", price="150")]
    result = article_generator.generate_weekly_article(history)

    assert result is not None
    assert "цена 67890.5" in captured["user_prompt"]
    assert "цена 3200" in captured["user_prompt"]
    assert "н/д" not in captured["user_prompt"]
