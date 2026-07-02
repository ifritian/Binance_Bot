#!/usr/bin/env python3
"""
Тесты validator.py - защита от искажения чисел LLM-ом. Чистая логика,
без сети и без обращения к LLM.
"""
from post_format import DISCLAIMER
from signal_parser import RsiSignal
import validator


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


def test_valid_post_passes():
    signal = _make_signal()
    text = (
        f"BEAT выглядит перегретым. Вход 2.205 - 2.2178, стоп 2.2371, "
        f"тейк 2.1729, RSI 81.74, score 89.\n\n{DISCLAIMER}"
    )
    ok, reason = validator.validate_post_text(text, signal)
    assert ok is True, reason


def test_missing_target_fails():
    signal = _make_signal()
    text = f"Вход 2.205 - 2.2178, стоп 2.2371, RSI 81.74, score 89.\n\n{DISCLAIMER}"
    ok, reason = validator.validate_post_text(text, signal)
    assert ok is False
    assert "тейк" in reason


def test_altered_number_fails():
    signal = _make_signal()
    # LLM исказил стоп (2.2371 -> 2.24) - должно быть отклонено
    text = f"Вход 2.205 - 2.2178, стоп 2.24, тейк 2.1729, RSI 81.74, score 89.\n\n{DISCLAIMER}"
    ok, reason = validator.validate_post_text(text, signal)
    assert ok is False


def test_missing_disclaimer_fails():
    signal = _make_signal()
    text = "Вход 2.205 - 2.2178, стоп 2.2371, тейк 2.1729, RSI 81.74, score 89."
    ok, reason = validator.validate_post_text(text, signal)
    assert ok is False
    assert "дисклеймер" in reason.lower()


def test_image_post_without_numbers_passes():
    text = f"Похоже на разворот, но подтверждения пока не видно.\n\n{DISCLAIMER}"
    ok, reason = validator.validate_image_post_text(text)
    assert ok is True, reason


def test_image_post_with_invented_number_fails():
    text = f"RSI около 75, разворот вероятен.\n\n{DISCLAIMER}"
    ok, reason = validator.validate_image_post_text(text)
    assert ok is False
    assert "числа" in reason or "чисел" in reason


if __name__ == "__main__":
    import sys
    import types

    passed, failed = 0, 0
    module = sys.modules[__name__]
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        if not isinstance(fn, types.FunctionType):
            continue
        try:
            fn()
            print(f"OK   {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
