#!/usr/bin/env python3
"""
Тесты чистой логики outcome_tracker: _resolve_outcome и
get_accuracy_stats (последний - через monkeypatch queue_manager, без
реальной SQLite и без сети).
"""
import outcome_tracker


def _c(high, low, close=None):
    return {"high": high, "low": low, "close": close if close is not None else (high + low) / 2}


def test_resolve_long_hits_target():
    record = {"direction": "long", "target": 110, "stop": 90}
    candles = [_c(105, 100), _c(112, 108)]
    result = outcome_tracker._resolve_outcome(record, candles)
    assert result == ("win", 110), result


def test_resolve_long_hits_stop():
    record = {"direction": "long", "target": 110, "stop": 90}
    candles = [_c(105, 100), _c(95, 88)]
    result = outcome_tracker._resolve_outcome(record, candles)
    assert result == ("loss", 90), result


def test_resolve_short_hits_target():
    record = {"direction": "short", "target": 90, "stop": 110}
    candles = [_c(102, 98), _c(95, 88)]
    result = outcome_tracker._resolve_outcome(record, candles)
    assert result == ("win", 90), result


def test_resolve_both_hit_same_candle_is_conservative_loss():
    record = {"direction": "long", "target": 110, "stop": 90}
    candles = [_c(115, 85)]  # свеча пробила и тейк, и стоп
    result = outcome_tracker._resolve_outcome(record, candles)
    assert result == ("loss", 90), result


def test_resolve_none_when_nothing_hit():
    record = {"direction": "long", "target": 110, "stop": 90}
    candles = [_c(105, 95), _c(103, 97)]
    result = outcome_tracker._resolve_outcome(record, candles)
    assert result is None, result


def test_accuracy_stats_aggregation(monkeypatch):
    fake_closed = [
        {"result": "win", "pnl_pct": 2.0, "strategy": "RSI", "quality": "Moderate", "closed_at": 0},
        {"result": "loss", "pnl_pct": -1.5, "strategy": "RSI", "quality": "Moderate", "closed_at": 0},
        {"result": "win", "pnl_pct": 3.0, "strategy": "RSI + Bollinger Touch", "quality": "Conservative", "closed_at": 0},
        {"result": "timeout", "pnl_pct": 0.2, "strategy": "RSI", "quality": "Moderate", "closed_at": 0},
    ]
    monkeypatch.setattr(outcome_tracker.queue_manager, "get_closed_outcomes", lambda: fake_closed)

    stats = outcome_tracker.get_accuracy_stats(days=None)

    assert stats["overall"]["count"] == 4
    # win_rate считается только по win/loss (3 записи, из них 2 win -> RSI bucket 1 win/1 loss = 50%,
    # но overall: 2 win из 3 decided (win/loss) = 66.7%
    assert stats["overall"]["win_rate"] == 66.7
    assert stats["by_strategy"]["RSI"]["count"] == 3
    assert stats["by_strategy"]["RSI"]["win_rate"] == 50.0
    assert stats["by_quality"]["Conservative"]["win_rate"] == 100.0


def test_accuracy_stats_empty(monkeypatch):
    monkeypatch.setattr(outcome_tracker.queue_manager, "get_closed_outcomes", lambda: [])
    stats = outcome_tracker.get_accuracy_stats()
    assert stats["overall"] == {"count": 0, "win_rate": None, "avg_pnl_pct": None}


if __name__ == "__main__":
    import sys
    import types

    # Простой раннер без pytest - находим все test_* функции и вызываем,
    # подставляя фейковый monkeypatch-объект тем тестам, которые его просят
    # (совместимо и с pytest, и с прямым запуском `python test_outcome_tracker.py`).
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
