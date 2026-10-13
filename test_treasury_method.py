#!/usr/bin/env python3
"""
Тесты исправления методики "с запуска" в Treasury Index (П1):
- окно расчёта = реальное время с прошлого замера (resolve_period_hours)
- переход на новую методику: архив старых данных и новый старт с 100
  (queue_manager.ensure_treasury_method_v2)
- цепочка периодов даёт честный итог по цене, а не завышенный
- пометка о смене методики в тексте поста
"""
import queue_manager
import treasury_generator

HOUR = 3600


def _fake_store(monkeypatch, initial=None):
    store = dict(initial or {})
    monkeypatch.setattr(queue_manager, "_get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(queue_manager, "_set", lambda key, value: store.__setitem__(key, value))
    return store


# --- resolve_period_hours ---

def test_resolve_period_uses_default_when_no_snapshots(monkeypatch):
    monkeypatch.setattr(queue_manager, "get_treasury_snapshots", lambda: [])
    assert treasury_generator.resolve_period_hours(12.0, now=1_000_000) == 12.0


def test_resolve_period_counts_hour_candles_between_posts(monkeypatch):
    # прошлый замер в 10:40, сейчас на следующий день в 10:15 -> 24 границы часа
    prev = 100 * 24 * HOUR + 10 * HOUR + 40 * 60
    now = prev + 23 * HOUR + 35 * 60   # +23ч35м -> 10:15 следующего дня
    monkeypatch.setattr(queue_manager, "get_treasury_snapshots", lambda: [{"timestamp": prev}])
    assert treasury_generator.resolve_period_hours(12.0, now=now) == 24.0


def test_resolve_period_never_below_one_hour(monkeypatch):
    prev = 5000 * HOUR + 10
    monkeypatch.setattr(queue_manager, "get_treasury_snapshots", lambda: [{"timestamp": prev}])
    # два замера внутри одного часа (например, ручной запуск) -> минимум 1
    assert treasury_generator.resolve_period_hours(12.0, now=prev + 300) == 1.0


def test_resolve_period_capped_at_one_week(monkeypatch):
    prev = 5000 * HOUR
    monkeypatch.setattr(queue_manager, "get_treasury_snapshots", lambda: [{"timestamp": prev}])
    assert treasury_generator.resolve_period_hours(12.0, now=prev + 400 * HOUR) == 168.0


# --- ensure_treasury_method_v2 ---

def test_ensure_v2_noop_when_already_current(monkeypatch):
    store = _fake_store(monkeypatch, {"treasury_history": {
        "launch_at": 1.0, "index_value": 103.0, "btc_value": 101.0,
        "method_version": queue_manager.TREASURY_METHOD_VERSION,
    }})
    assert queue_manager.ensure_treasury_method_v2() is False
    assert store["treasury_history"]["index_value"] == 103.0
    assert "treasury_archive_v1" not in store


def test_ensure_v2_archives_old_history_and_restarts_at_100(monkeypatch):
    old = {"launch_at": 1.0, "index_value": 182.0765, "btc_value": 149.8963}
    store = _fake_store(monkeypatch, {
        "treasury_history": old,
        "treasury_snapshots": [{"timestamp": 1.0, "index_value": 105.11}],
        "treasury_returns_history": [5.11, 5.38],
    })

    assert queue_manager.ensure_treasury_method_v2() is True

    fresh = store["treasury_history"]
    assert fresh["index_value"] == 100.0 and fresh["btc_value"] == 100.0
    assert fresh["eth_value"] == 100.0 and fresh["market_value"] == 100.0
    assert fresh["method_version"] == queue_manager.TREASURY_METHOD_VERSION
    assert fresh["method_note_until"] > fresh["launch_at"]
    # одна стартовая точка графика, история доходностей пуста
    assert len(store["treasury_snapshots"]) == 1
    assert store["treasury_snapshots"][0]["index_value"] == 100.0
    assert store["treasury_returns_history"] == []
    # старые данные сохранены, а не потеряны
    archive = store["treasury_archive_v1"]
    assert archive["history"] == old
    assert archive["returns"] == [5.11, 5.38]
    assert archive["snapshots"][0]["index_value"] == 105.11


def test_ensure_v2_second_call_is_noop_and_keeps_archive(monkeypatch):
    store = _fake_store(monkeypatch, {"treasury_history": {"launch_at": 1.0, "index_value": 150.0, "btc_value": 120.0}})
    assert queue_manager.ensure_treasury_method_v2() is True
    archived = dict(store["treasury_archive_v1"])
    assert queue_manager.ensure_treasury_method_v2() is False
    assert store["treasury_archive_v1"] == archived


def test_ensure_v2_first_install_has_nothing_to_archive(monkeypatch):
    store = _fake_store(monkeypatch)
    assert queue_manager.ensure_treasury_method_v2() is True
    assert store["treasury_history"]["index_value"] == 100.0
    assert "treasury_archive_v1" not in store


# --- цепочка периодов даёт честный итог ---

def test_chained_periods_match_true_price_move(monkeypatch):
    """Цена BTC за двое суток: 100 -> 110 (первые 24ч) -> 99 (вторые 24ч).
    Честный итог с запуска: 99/100 = -1%. Цепочка 24ч-окон должна
    совпасть с ним, а старая схема (только 12ч в конце суток) - нет."""
    _fake_store(monkeypatch)
    queue_manager.ensure_treasury_method_v2()

    for window_pct in ((110 - 100) / 100 * 100, (99 - 110) / 110 * 100):
        history = queue_manager.update_treasury_history(window_pct, window_pct)

    assert abs(history["btc_value"] - 99.0) < 0.01
    assert abs(history["index_value"] - 99.0) < 0.01


def test_five_test_runs_minutes_apart_get_short_windows(monkeypatch):
    """Тестовые запуски с интервалом в минуты раньше каждый применяли
    12-часовое движение. Теперь окно = 1 час (минимум), а не 12."""
    base = 7000 * HOUR + 120
    for gap_minutes in (6, 11, 9, 14):
        prev = base
        now = prev + gap_minutes * 60
        monkeypatch.setattr(queue_manager, "get_treasury_snapshots", lambda p=prev: [{"timestamp": p}])
        assert treasury_generator.resolve_period_hours(12.0, now=now) <= 2.0


# --- пометка о смене методики ---

def _history(note_until):
    return {
        "launch_at": 1_700_000_000.0, "index_value": 100.0, "btc_value": 100.0,
        "eth_value": 100.0, "market_value": 100.0, "method_note_until": note_until,
    }


def test_comparison_block_shows_note_while_active(monkeypatch):
    monkeypatch.setattr(treasury_generator.time, "time", lambda: 1_000.0)
    block = treasury_generator._format_comparison_block(None, 24.0, None, None, None, _history(2_000.0))
    assert "Методика расчёта уточнена" in block


def test_comparison_block_hides_note_after_expiry(monkeypatch):
    monkeypatch.setattr(treasury_generator.time, "time", lambda: 3_000.0)
    block = treasury_generator._format_comparison_block(None, 24.0, None, None, None, _history(2_000.0))
    assert "Методика расчёта уточнена" not in block
