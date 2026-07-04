#!/usr/bin/env python3
"""
Тесты accuracy_report_generator.py - чистая логика форматирования и
валидации + generate_accuracy_report_post с замоканными
outcome_tracker.get_accuracy_stats и call_groq (без сети).
"""
import accuracy_report_generator as arg


def _fake_stats(count=10, win_rate=60.0, avg_pnl=1.5):
    return {
        "overall": {"count": count, "win_rate": win_rate, "avg_pnl_pct": avg_pnl},
        "by_strategy": {
            "RSI": {"count": count, "win_rate": win_rate, "avg_pnl_pct": avg_pnl},
        },
        "by_quality": {},
    }


def test_extract_numbers():
    nums = arg._extract_numbers("win-rate 66.7%, среднее +1.23%, n=10")
    assert 66.7 in nums
    assert 1.23 in nums
    assert 10 in nums


def test_format_stats_block_contains_key_numbers():
    stats = _fake_stats(count=10, win_rate=60.0, avg_pnl=1.5)
    block = arg._format_stats_block(stats, days=7)
    assert "10" in block
    assert "60.0%" in block
    assert "+1.5%" in block


def test_format_stats_block_handles_no_data():
    stats = {"overall": {"count": 0, "win_rate": None, "avg_pnl_pct": None}, "by_strategy": {}, "by_quality": {}}
    block = arg._format_stats_block(stats, days=7)
    assert "н/д" in block


def test_validate_accuracy_hook_ok_with_known_numbers():
    ok, reason = arg.validate_accuracy_hook("Win-rate за неделю 60.0%, неплохо", {60.0, 7})
    assert ok is True, reason


def test_validate_accuracy_hook_rejects_unknown_number():
    ok, reason = arg.validate_accuracy_hook("Win-rate 99.9%!", {60.0, 7})
    assert ok is False


def test_validate_accuracy_hook_rejects_english_words(monkeypatch=None):
    ok, reason = arg.validate_accuracy_hook("This week was solid, win-rate держится", {60.0, 7})
    assert ok is False


def test_generate_report_returns_none_when_not_enough_data(monkeypatch):
    monkeypatch.setattr(arg.outcome_tracker, "get_accuracy_stats", lambda days: _fake_stats(count=1))
    monkeypatch.setattr(arg, "call_groq", lambda *a, **k: "не должно вызываться")
    import config
    monkeypatch.setattr(config, "ACCURACY_REPORT_MIN_CLOSED_SIGNALS", 5)

    result = arg.generate_accuracy_report_post()
    assert result is None


def test_generate_report_returns_text_when_enough_data(monkeypatch):
    monkeypatch.setattr(arg.outcome_tracker, "get_accuracy_stats", lambda days: _fake_stats(count=10))
    monkeypatch.setattr(arg, "call_groq", lambda *a, **k: "Неделя вышла крепкой")
    import config
    monkeypatch.setattr(config, "ACCURACY_REPORT_MIN_CLOSED_SIGNALS", 5)

    result = arg.generate_accuracy_report_post()
    assert result is not None
    binance_text, telegram_text = result
    assert "Неделя вышла крепкой" in binance_text
    assert "60.0%" in binance_text
    assert binance_text == telegram_text


def test_generate_report_falls_back_to_neutral_hook_on_bad_llm_output(monkeypatch):
    monkeypatch.setattr(arg.outcome_tracker, "get_accuracy_stats", lambda days: _fake_stats(count=10))
    monkeypatch.setattr(arg, "call_groq", lambda *a, **k: "This week win-rate 99.9% amazing")
    import config
    monkeypatch.setattr(config, "ACCURACY_REPORT_MIN_CLOSED_SIGNALS", 5)

    result = arg.generate_accuracy_report_post()
    assert result is not None
    binance_text, _ = result
    assert "This week" not in binance_text
    assert "Свежий срез" in binance_text


if __name__ == "__main__":
    import sys
    import types

    class _MiniMonkeypatch:
        def __init__(self):
            self._restore = []

        def setattr(self, obj, name, value):
            self._restore.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._restore):
                setattr(obj, name, old)

    passed, failed = 0, 0
    module = sys.modules[__name__]
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        if not isinstance(fn, types.FunctionType):
            continue
        mp = _MiniMonkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"OK   {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        finally:
            mp.undo()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
