#!/usr/bin/env python3
"""
Тесты scanner.py: ручной денай-лист тикеров (config.EXCLUDED_TICKERS) -
монеты в списке должны полностью исчезать из universe, даже если
формально проходят фильтр по объёму.
"""
import config
import scanner
import strategies


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


# --- A2: ATR-стопы в _build_signal (RSI + Bollinger) ---

def _downtrend_candles(n=60, start=100.0, step=0.5):
    """Устойчивый нисходящий тренд - RSI(14) уходит к 0, гарантированно
    ниже RSI_OVERSOLD(30), даёт стабильный сигнал "Лонг (перепроданность)"
    для проверки формулы стопа."""
    candles = []
    price = start
    for _ in range(n):
        price -= step
        candles.append(scanner._Candle(open=price + 0.05, high=price + 0.1, low=price - 0.1, close=price, volume=100_000))
    return candles


def test_build_signal_uses_fixed_pct_stop_by_default(monkeypatch):
    monkeypatch.setattr(config, "USE_ATR_STOPS", False)
    candles = _downtrend_candles()
    signal = scanner._build_signal("TESTUSDT", candles, 8_000_000)
    assert signal is not None
    recent_low = min(c.low for c in candles[-20:])
    assert abs(float(signal.invalidation) - recent_low * 0.997) < 1e-6


def test_build_signal_uses_atr_stop_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "USE_ATR_STOPS", True)
    monkeypatch.setattr(config, "ATR_PERIOD", 14)
    monkeypatch.setattr(config, "ATR_STOP_MULTIPLIER", 1.5)
    candles = _downtrend_candles()
    signal = scanner._build_signal("TESTUSDT", candles, 8_000_000)
    assert signal is not None
    recent_low = min(c.low for c in candles[-20:])
    atr = strategies.calc_atr(candles, 14)
    expected = recent_low - atr * 1.5
    assert abs(float(signal.invalidation) - expected) < 1e-6
    assert abs(float(signal.invalidation) - recent_low * 0.997) > 1e-6  # реально другая формула, не совпадение


def test_build_signal_atr_disabled_ignores_atr_config(monkeypatch):
    # USE_ATR_STOPS=False - даже если ATR_STOP_MULTIPLIER настроен на
    # что-то экзотическое, формула не должна его использовать вовсе.
    monkeypatch.setattr(config, "USE_ATR_STOPS", False)
    monkeypatch.setattr(config, "ATR_STOP_MULTIPLIER", 99.0)
    candles = _downtrend_candles()
    signal = scanner._build_signal("TESTUSDT", candles, 8_000_000)
    assert signal is not None
    recent_low = min(c.low for c in candles[-20:])
    assert abs(float(signal.invalidation) - recent_low * 0.997) < 1e-6


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


def test_process_signal_candidate_accepts_score_exactly_at_threshold(monkeypatch):
    # Регресс-тест на off-by-one: score, РОВНО равный порогу публикации,
    # обязан пройти (порог - это "минимум, который проходит", а не
    # "минимум + 1"). Раньше здесь стояло `<=`, из-за чего сигналы,
    # которые multi_timeframe.refine_signal часто подтягивает ровно до
    # порога (например 54 -> 70 при подтверждении старшими ТФ), тихо
    # отбрасывались - ни в очередь постов, ни колбэку futures-автотрейдинга.
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)

    calls = []
    accepted = scanner._process_signal_candidate(
        _accepted_signal(score="70"), "SOLUSDT", "SOL", min_score_cfg=70,
        on_signal_accepted=lambda s, sym: calls.append((s.ticker, sym)),
    )
    assert accepted is True
    assert calls == [("SOL", "SOLUSDT")]


# --- _process_signal_candidate: минимальный R:R (см. config.MIN_RISK_REWARD_RATIO) ---

def _poor_rr_signal(ratio: float, score="85"):
    """entry mid = 100, риск фиксирован в 10 (стоп=90) - target подобран
    так, чтобы reward/risk == ratio ровно."""
    from signal_parser import RsiSignal
    target = 100 + 10 * ratio
    return RsiSignal(
        ticker="SOL", timeframe="15m", strategy="RSI", direction="Лонг (перепроданность)",
        current_price="100", rsi_now="25", score=score, quality="Moderate",
        entry_low="99", entry_high="101", invalidation="90", target=f"{target:.6g}",
        change_24h="+1%", volume="10M", rsi_live="25", created_at="2026-07-29 00:00:00 UTC",
        description="тест", raw_text="тест",
    )


def test_process_signal_candidate_rejected_when_risk_reward_below_threshold(monkeypatch):
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(config, "MIN_RISK_REWARD_RATIO", 1.2)

    calls = []
    accepted = scanner._process_signal_candidate(
        _poor_rr_signal(ratio=0.8), "SOLUSDT", "SOL", min_score_cfg=70,
        on_signal_accepted=lambda s, sym: calls.append((s.ticker, sym)),
    )
    assert accepted is False
    assert calls == []


def test_process_signal_candidate_accepts_risk_reward_exactly_at_threshold(monkeypatch):
    # ratio РОВНО равный порогу должен пройти (порог - "минимум, который
    # проходит", та же логика, что и в off-by-one тесте для score выше).
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)
    monkeypatch.setattr(config, "MIN_RISK_REWARD_RATIO", 1.2)

    calls = []
    accepted = scanner._process_signal_candidate(
        _poor_rr_signal(ratio=1.2), "SOLUSDT", "SOL", min_score_cfg=70,
        on_signal_accepted=lambda s, sym: calls.append((s.ticker, sym)),
    )
    assert accepted is True
    assert calls == [("SOL", "SOLUSDT")]


def test_process_signal_candidate_high_score_does_not_rescue_poor_risk_reward(monkeypatch):
    # ключевая идея фильтра: плохой R:R не спасти высоким score.
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(config, "MIN_RISK_REWARD_RATIO", 1.2)

    accepted = scanner._process_signal_candidate(
        _poor_rr_signal(ratio=0.5, score="100"), "SOLUSDT", "SOL", min_score_cfg=0,
    )
    assert accepted is False


def test_process_signal_candidate_not_blocked_when_ratio_cannot_be_computed(monkeypatch):
    # числа не распознались/риск=0 -> calc_risk_reward_ratio вернёт None -
    # фильтр НЕ блокирует из-за собственной невозможности посчитать,
    # проверка просто пропускается (как и остальные "мягкие" проверки).
    from signal_parser import RsiSignal
    signal = RsiSignal(
        ticker="SOL", timeframe="15m", strategy="RSI", direction="Лонг (перепроданность)",
        current_price="100", rsi_now="25", score="85", quality="Moderate",
        entry_low="100", entry_high="100", invalidation="100", target="130",  # риск = 0
        change_24h="+1%", volume="10M", rsi_live="25", created_at="2026-07-29 00:00:00 UTC",
        description="тест", raw_text="тест",
    )
    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", lambda *a, **k: False)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda s, sym: s)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", lambda signal: None)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", lambda ticker, direction: None)
    monkeypatch.setattr(config, "MIN_RISK_REWARD_RATIO", 1.2)

    accepted = scanner._process_signal_candidate(signal, "SOLUSDT", "SOL", min_score_cfg=70)
    assert accepted is True


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