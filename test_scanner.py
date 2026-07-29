#!/usr/bin/env python3
"""
Тесты scanner.py: ручной денай-лист тикеров (config.EXCLUDED_TICKERS) -
монеты в списке должны полностью исчезать из universe, даже если
формально проходят фильтр по объёму.
"""
import config
import scanner


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _row(symbol: str, quote_volume: float) -> dict:
    return {"symbol": symbol, "quoteVolume": str(quote_volume)}


def test_fetch_universe_excludes_denylisted_ticker(monkeypatch):
    monkeypatch.setattr(config, "EXCLUDED_TICKERS", {"PHB"})
    rows = [_row("PHBUSDT", 28_000_000), _row("SOLUSDT", 900_000_000)]
    monkeypatch.setattr(scanner.requests, "get", lambda *a, **k: _FakeResponse(rows))

    universe = scanner._fetch_universe()

    symbols = [s for s, _ in universe]
    assert "PHBUSDT" not in symbols
    assert "SOLUSDT" in symbols


def test_fetch_universe_keeps_non_denylisted_tickers(monkeypatch):
    monkeypatch.setattr(config, "EXCLUDED_TICKERS", set())
    rows = [_row("PHBUSDT", 28_000_000)]
    monkeypatch.setattr(scanner.requests, "get", lambda *a, **k: _FakeResponse(rows))

    universe = scanner._fetch_universe()

    symbols = [s for s, _ in universe]
    assert "PHBUSDT" in symbols


def test_fetch_universe_denylist_is_exact_ticker_not_substring(monkeypatch):
    # Денай-лист должен матчить именно тикер целиком (без USDT), а не
    # произвольную подстроку символа - иначе "PH" случайно вырезал бы
    # что-то вроде "ALPHAUSDT".
    monkeypatch.setattr(config, "EXCLUDED_TICKERS", {"PH"})
    rows = [_row("PHBUSDT", 28_000_000), _row("ALPHAUSDT", 5_000_000)]
    monkeypatch.setattr(scanner.requests, "get", lambda *a, **k: _FakeResponse(rows))

    universe = scanner._fetch_universe()

    symbols = [s for s, _ in universe]
    assert "PHBUSDT" in symbols  # "PHB" != "PH", не исключается
    assert "ALPHAUSDT" in symbols


# --- _process_signal_candidate: колбэк on_signal_accepted ---

def _accepted_signal(ticker="SOL", score="85"):
    from signal_parser import RsiSignal
    return RsiSignal(
        ticker=ticker, timeframe="15m", strategy="RSI", direction="Лонг (перепроданность)",
        current_price="100", rsi_now="25", score=score, quality="Moderate",
        entry_low="99", entry_high="101", invalidation="95", target="110",
        change_24h="+1%", volume="10M", rsi_live="25", created_at="2026-07-29 00:00:00 UTC",
        description="тест", raw_text="тест",
    )


def test_process_signal_candidate_calls_callback_when_accepted(monkeypatch):
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)

    calls = []
    signal = _accepted_signal()
    accepted = scanner._process_signal_candidate(
        signal, "SOLUSDT", "SOL", min_score_cfg=70,
        on_signal_accepted=lambda s, sym: calls.append((s.ticker, sym)),
    )
    assert accepted is True
    assert calls == [("SOL", "SOLUSDT")]


def test_process_signal_candidate_without_callback_is_backward_compatible(monkeypatch):
    # Поведение по умолчанию (on_signal_accepted=None) не должно меняться.
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)

    accepted = scanner._process_signal_candidate(_accepted_signal(), "SOLUSDT", "SOL", min_score_cfg=70)
    assert accepted is True


def test_process_signal_candidate_callback_exception_does_not_propagate(monkeypatch):
    # Сигнал уже прошёл в очередь публикации - упавший колбэк не должен
    # превращать успешный accept в исключение наружу.
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)

    def _boom(signal, symbol):
        raise RuntimeError("симулированный сбой в колбэке")

    accepted = scanner._process_signal_candidate(
        _accepted_signal(), "SOLUSDT", "SOL", min_score_cfg=70, on_signal_accepted=_boom,
    )
    assert accepted is True  # публикация в очередь уже прошла успешно, несмотря на упавший колбэк


def test_process_signal_candidate_callback_not_called_when_below_score(monkeypatch):
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)

    calls = []
    accepted = scanner._process_signal_candidate(
        _accepted_signal(score="50"), "SOLUSDT", "SOL", min_score_cfg=70,
        on_signal_accepted=lambda s, sym: calls.append((s.ticker, sym)),
    )
    assert accepted is False
    assert calls == []


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
            fn(mp)
            print(f"OK   {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        finally:
            mp.undo()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
