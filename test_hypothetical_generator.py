#!/usr/bin/env python3
"""
Тесты hypothetical_generator.py - формат "А что если?" (см. docstring
модуля). Ключевые инварианты: в тексте нет вообще никаких чисел (как у
binance_promo_generator - нет реальных данных, значит числа только
выдуманные), и обязательно есть явная фраза-рамка (что если/представь/
и т.п.), иначе гипотетический сценарий может прочитаться как реальное
заявление о рынке.
"""
import hypothetical_generator
import post_format


def test_pick_ticker_avoids_repeating_last():
    for last in hypothetical_generator._TICKERS:
        chosen = hypothetical_generator.pick_ticker(last)
        assert chosen != last
        assert chosen in hypothetical_generator._TICKERS


def test_pick_ticker_handles_unknown_last():
    chosen = hypothetical_generator.pick_ticker(None)
    assert chosen in hypothetical_generator._TICKERS


def test_tickers_have_no_delisted_or_renamed_or_duplicate_entries():
    # Та же регрессия, что и в opinion_generator - MATIC (делистнут,
    # заменён на POL) и FTM (переименован в Sonic/S) не должны
    # оказаться в пуле снова.
    assert "MATIC" not in hypothetical_generator._TICKERS
    assert "FTM" not in hypothetical_generator._TICKERS
    assert len(hypothetical_generator._TICKERS) == len(set(hypothetical_generator._TICKERS))


def test_generate_hypothetical_post_returns_assembled_text(monkeypatch):
    monkeypatch.setattr(
        hypothetical_generator, "call_groq",
        lambda *a, **k: "Что если BTC вдруг начали майнить инопланетяне? Волатильность точно не заскучает.",
    )
    text = hypothetical_generator.generate_hypothetical_post("BTC")
    assert text is not None
    assert "инопланетяне" in text
    assert post_format.DISCLAIMER in text


def test_generate_hypothetical_post_returns_none_when_too_short(monkeypatch):
    monkeypatch.setattr(hypothetical_generator, "call_groq", lambda *a, **k: "Ха")
    assert hypothetical_generator.generate_hypothetical_post("BTC") is None


def test_generate_hypothetical_post_substitutes_ticker_into_prompt(monkeypatch):
    captured = {}

    def _fake_call_groq(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "Представь, что $ETH стал официальной валютой одной маленькой страны."

    monkeypatch.setattr(hypothetical_generator, "call_groq", _fake_call_groq)
    hypothetical_generator.generate_hypothetical_post("ETH")

    assert "$ETH" in captured["system_prompt"]
    assert "$TICKER" not in captured["system_prompt"]  # плейсхолдер должен быть подставлен


# --- validate_hypothetical_post ---------------------------------------------

def _valid_text(body: str) -> str:
    return f"{body}\n\n{post_format.DISCLAIMER}"


def test_validate_hypothetical_post_accepts_clean_hypothetical():
    text = _valid_text("Что если BTC вдруг начали майнить инопланетяне? Было бы весело.")
    ok, reason = hypothetical_generator.validate_hypothetical_post(text)
    assert ok, reason


def test_validate_hypothetical_post_rejects_any_number():
    text = _valid_text("Что если BTC вырос на 500%? Было бы безумие.")
    ok, reason = hypothetical_generator.validate_hypothetical_post(text)
    assert not ok
    assert "чисел" in reason.lower()


def test_validate_hypothetical_post_rejects_missing_framing_marker():
    # Нет ни одной фразы-рамки - звучит как реальное заявление, а не игра воображения.
    text = _valid_text("BTC вдруг начали майнить инопланетяне. Было бы весело.")
    ok, reason = hypothetical_generator.validate_hypothetical_post(text)
    assert not ok
    assert "рамк" in reason.lower()


def test_validate_hypothetical_post_rejects_missing_disclaimer():
    ok, reason = hypothetical_generator.validate_hypothetical_post(
        "Что если BTC вдруг начали майнить инопланетяне?"
    )
    assert not ok
    assert "дисклеймер" in reason.lower()


def test_validate_hypothetical_post_accepts_various_framing_markers():
    for marker in ("Представим", "Вообрази", "Мысленный эксперимент:", "А что если"):
        text = _valid_text(f"{marker}, что BTC стал официальной валютой одной страны.")
        ok, reason = hypothetical_generator.validate_hypothetical_post(text)
        assert ok, f"'{marker}' should be accepted as a framing marker, got: {reason}"
