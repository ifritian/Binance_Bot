#!/usr/bin/env python3
"""Тесты futures_state.py - на временном sqlite-файле (не настоящем
futures_state.db), чтобы не трогать реальное состояние."""
import os
import tempfile

import config
import futures_state


def _with_temp_db(fn):
    """Подменяет config.FUTURES_DB_PATH на временный файл на время теста -
    futures_state._connect читает config.FUTURES_DB_PATH при КАЖДОМ вызове
    (не кэширует), так что подмены достаточно."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # sqlite сам создаст файл заново
    old = config.FUTURES_DB_PATH
    config.FUTURES_DB_PATH = path
    try:
        fn()
    finally:
        config.FUTURES_DB_PATH = old
        if os.path.exists(path):
            os.remove(path)


def test_daily_baseline_roundtrip():
    def _inner():
        assert futures_state.get_risk_daily_baseline("2026-07-29") is None
        futures_state.set_risk_daily_baseline("2026-07-29", 1234.5)
        assert futures_state.get_risk_daily_baseline("2026-07-29") == 1234.5
        # другой день - отдельная запись, не задета
        assert futures_state.get_risk_daily_baseline("2026-07-30") is None
    _with_temp_db(_inner)


def test_kill_switch_roundtrip():
    def _inner():
        assert futures_state.get_kill_switch() is None
        futures_state.set_kill_switch("тестовая причина")
        ks = futures_state.get_kill_switch()
        assert ks is not None and ks["reason"] == "тестовая причина"
        futures_state.clear_kill_switch()
        assert futures_state.get_kill_switch() is None
    _with_temp_db(_inner)


def test_own_scan_cooldown_roundtrip():
    def _inner():
        assert futures_state.was_recently_alerted("SOL", "long", cooldown_hours=6) is False
        futures_state.mark_alerted("SOL", "long")
        assert futures_state.was_recently_alerted("SOL", "long", cooldown_hours=6) is True
        # другое направление того же тикера - отдельный cooldown
        assert futures_state.was_recently_alerted("SOL", "short", cooldown_hours=6) is False
    _with_temp_db(_inner)


def test_push_pending_signal_is_a_no_op():
    # Не должно бросать исключение и не должно ничего сохранять -
    # futures-боту не нужна очередь постов (см. docstring модуля).
    def _inner():
        futures_state.push_pending_signal(object())  # любой объект, даже не RsiSignal
    _with_temp_db(_inner)


def test_managed_position_registry_roundtrip():
    def _inner():
        assert futures_state.list_managed_positions() == {}
        futures_state.register_managed_position("BTCUSDT", "BUY", 100.0, 95.0, 115.0)
        positions = futures_state.list_managed_positions()
        assert set(positions.keys()) == {"BTCUSDT"}
        entry = positions["BTCUSDT"]
        assert entry["side"] == "BUY"
        assert entry["entry_price"] == 100.0
        assert entry["initial_stop_price"] == 95.0
        assert entry["take_profit_price"] == 115.0
        assert entry["trail_distance"] == 5.0  # |100 - 95|, зафиксировано один раз

        futures_state.unregister_managed_position("BTCUSDT")
        assert futures_state.list_managed_positions() == {}
    _with_temp_db(_inner)


def test_managed_position_registry_tracks_multiple_symbols_independently():
    def _inner():
        futures_state.register_managed_position("BTCUSDT", "BUY", 100.0, 95.0, 115.0)
        futures_state.register_managed_position("ETHUSDT", "SELL", 3000.0, 3100.0, 2800.0)
        positions = futures_state.list_managed_positions()
        assert set(positions.keys()) == {"BTCUSDT", "ETHUSDT"}

        futures_state.unregister_managed_position("BTCUSDT")
        remaining = futures_state.list_managed_positions()
        assert set(remaining.keys()) == {"ETHUSDT"}
    _with_temp_db(_inner)


def test_alert_throttle_roundtrip():
    def _inner():
        assert futures_state.get_last_alert_sent("k") == 0
        futures_state.set_last_alert_sent("k")
        assert futures_state.get_last_alert_sent("k") > 0
    _with_temp_db(_inner)


def test_scanner_with_futures_state_never_touches_queue_manager(monkeypatch):
    """Главная гарантия этого разделения: если scanner._process_signal_candidate
    вызван с state=futures_state (как это делает futures_auto_trade.py),
    он не должен трогать queue_manager (bot_state.db постинг-бота)
    ВООБЩЕ - ни на чтение, ни на запись."""
    import scanner
    from signal_parser import RsiSignal

    def _fail_if_touched(*a, **k):
        raise AssertionError("queue_manager не должен был вызываться из futures-пути")

    monkeypatch.setattr(scanner.queue_manager, "was_recently_alerted", _fail_if_touched)
    monkeypatch.setattr(scanner.queue_manager, "mark_alerted", _fail_if_touched)
    monkeypatch.setattr(scanner.queue_manager, "push_pending_signal", _fail_if_touched)
    monkeypatch.setattr(scanner.multi_timeframe, "refine_signal", lambda signal, symbol: signal)
    monkeypatch.setattr(scanner.strategy_tuner, "get_effective_min_score", lambda strategy, cfg: cfg)

    def _inner():
        signal = RsiSignal(
            ticker="SOL", timeframe="15m", strategy="RSI", direction="Лонг (перепроданность)",
            current_price="100", rsi_now="25", score="85", quality="Moderate",
            entry_low="99", entry_high="101", invalidation="95", target="110",
            change_24h="+1%", volume="10M", rsi_live="25", created_at="2026-07-29 00:00:00 UTC",
            description="тест", raw_text="тест",
        )
        accepted = scanner._process_signal_candidate(
            signal, "SOLUSDT", "SOL", min_score_cfg=70, state=futures_state,
        )
        assert accepted is True

    _with_temp_db(_inner)


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
