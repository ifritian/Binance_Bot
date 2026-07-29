#!/usr/bin/env python3
"""
Тесты risk_guard.py - на ПОДДЕЛЬНОМ FuturesClient (никакой реальной
сети) и с monkeypatch queue_manager (никакой реальной SQLite - как в
test_strategy_tuner.py)."""
import risk_guard


class _FakeClient:
    """Имитирует ровно те методы FuturesClient, которые нужны
    risk_guard - каждый настраивается напрямую полем, без сети."""

    def __init__(self, positions=None, wallet_balance=10_000.0, income_rows=None):
        self.positions = positions or []
        self.wallet_balance = wallet_balance
        self.income_rows = income_rows or []
        self.calls = []

    def get_all_positions(self):
        self.calls.append("get_all_positions")
        return self.positions

    def get_wallet_balance(self, asset="USDT"):
        self.calls.append("get_wallet_balance")
        return self.wallet_balance

    def get_income_history(self, income_type="REALIZED_PNL", start_time_ms=None, limit=1000):
        self.calls.append("get_income_history")
        return self.income_rows


def _limits(max_open=3, max_daily_loss_pct=5.0, max_consecutive_losses=3):
    return risk_guard.RiskLimits(
        max_open_positions=max_open,
        max_daily_loss_pct=max_daily_loss_pct,
        max_consecutive_losses=max_consecutive_losses,
    )


def _income(pnls):
    """pnls - список чисел в хронологическом порядке (старые первыми) -
    сознательно возвращаем их в ОБРАТНОМ порядке от client.get_income_history,
    чтобы проверить, что risk_guard сам сортирует по времени, а не
    полагается на порядок ответа биржи."""
    rows = [{"income": str(p), "time": i} for i, p in enumerate(pnls)]
    return list(reversed(rows))


# --- kill switch: блокирует немедленно, без единого сетевого вызова ---

def test_kill_switch_blocks_before_any_client_call(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch",
                         lambda: {"reason": "тестовая причина", "tripped_at": 0})
    client = _FakeClient()
    reason = risk_guard.check_new_position_allowed(client, _limits())
    assert reason is not None
    assert "тестовая причина" in reason
    assert client.calls == []  # ни один метод клиента не должен был вызваться


# --- лимит открытых позиций ---

def test_max_open_positions_blocks(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    client = _FakeClient(positions=[{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "SOLUSDT"}])
    reason = risk_guard.check_new_position_allowed(client, _limits(max_open=3))
    assert reason is not None
    assert "3/3" in reason
    assert "BTCUSDT" in reason and "ETHUSDT" in reason and "SOLUSDT" in reason


def test_open_positions_under_limit_does_not_block_on_that_check(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    client = _FakeClient(positions=[{"symbol": "BTCUSDT"}], wallet_balance=10_000.0, income_rows=[])
    reason = risk_guard.check_new_position_allowed(client, _limits(max_open=3))
    assert reason is None


# --- дневной лимит убытка ---

def test_daily_loss_trips_kill_switch(monkeypatch):
    tripped = {}
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", lambda reason: tripped.setdefault("reason", reason))

    # баланс упал с 10000 до 9400 = ровно 6% убытка, лимит 5%
    client = _FakeClient(positions=[], wallet_balance=9_400.0, income_rows=[])
    reason = risk_guard.check_new_position_allowed(client, _limits(max_daily_loss_pct=5.0))
    assert reason is not None
    assert "KILL SWITCH" in reason
    assert "reason" in tripped  # queue_manager.set_kill_switch реально был вызван


def test_daily_loss_under_limit_does_not_trip(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)

    def _fail_if_called(reason):
        raise AssertionError("set_kill_switch не должен был вызываться")

    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", _fail_if_called)
    client = _FakeClient(positions=[], wallet_balance=9_600.0, income_rows=[])  # -4%, лимит 5%
    reason = risk_guard.check_new_position_allowed(client, _limits(max_daily_loss_pct=5.0))
    assert reason is None


def test_daily_baseline_set_once_on_first_check(monkeypatch):
    stored = {}
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: stored.get(day))
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: stored.__setitem__(day, bal))

    client = _FakeClient(wallet_balance=10_000.0)
    loss_pct, baseline, current = risk_guard._daily_loss_pct(client)
    assert baseline == 10_000.0 and loss_pct == 0.0

    # баланс изменился, но baseline за сегодня уже зафиксирован - должен остаться прежним
    client.wallet_balance = 9_000.0
    loss_pct2, baseline2, current2 = risk_guard._daily_loss_pct(client)
    assert baseline2 == 10_000.0  # НЕ пересчитался
    assert round(loss_pct2, 2) == 10.0


# --- серия убытков подряд ---

def test_consecutive_losses_trips_kill_switch(monkeypatch):
    tripped = {}
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", lambda reason: tripped.setdefault("reason", reason))

    # 3 убытка подряд, лимит 3
    client = _FakeClient(positions=[], wallet_balance=10_000.0, income_rows=_income([-10, -20, -30]))
    reason = risk_guard.check_new_position_allowed(client, _limits(max_consecutive_losses=3))
    assert reason is not None
    assert "3 убыточных" in reason
    assert "reason" in tripped


def test_streak_broken_by_a_win_does_not_trip(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)

    def _fail_if_called(reason):
        raise AssertionError("set_kill_switch не должен был вызываться")

    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", _fail_if_called)
    # два убытка, потом выигрыш (последняя сделка) - серия оборвана, несмотря на два убытка до неё
    client = _FakeClient(positions=[], wallet_balance=10_000.0, income_rows=_income([-10, -20, 15]))
    reason = risk_guard.check_new_position_allowed(client, _limits(max_consecutive_losses=3))
    assert reason is None


def test_consecutive_losses_ignores_zero_income_rows():
    # 0.0 income (например, комиссийная запись без реального закрытия) - не считается ни выигрышем, ни проигрышем
    client = _FakeClient(income_rows=_income([-10, 0, -20]))
    streak = risk_guard._consecutive_losses(client)
    assert streak == 2


def test_consecutive_losses_sorts_by_time_itself():
    """Проверяем, что порядок ответа биржи не важен - risk_guard сам
    сортирует записи по полю 'time' перед подсчётом серии."""
    rows = [{"income": "-5", "time": 2}, {"income": "10", "time": 1}, {"income": "-7", "time": 3}]
    client = _FakeClient(income_rows=rows)
    # хронологически: +10, -5, -7 -> серия из последних двух отрицательных = 2
    streak = risk_guard._consecutive_losses(client)
    assert streak == 2


# --- всё в норме ---

def test_all_clear_returns_none(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    client = _FakeClient(positions=[{"symbol": "BTCUSDT"}], wallet_balance=10_100.0, income_rows=_income([10, 20]))
    reason = risk_guard.check_new_position_allowed(client, _limits(max_open=3, max_daily_loss_pct=5.0, max_consecutive_losses=3))
    assert reason is None


# --- status(): не должен "молчать" о превышенном лимите ---

def test_status_trips_kill_switch_on_already_breached_streak(monkeypatch):
    """Регрессионный тест на реальный баг: status() раньше мог
    показать 'убытков подряд: 4/3' и одновременно 'kill switch: не
    взведён' - потому что взведение раньше происходило только внутри
    check_new_position_allowed (в момент попытки открыть позицию), а не
    при простом просмотре статуса."""
    tripped = {}
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", lambda reason: tripped.setdefault("reason", reason))

    client = _FakeClient(positions=[{"symbol": "BTCUSDT"}], wallet_balance=10_000.0,
                          income_rows=_income([-10, -20, -30, -40]))  # 4 подряд, лимит 3
    s = risk_guard.status(client, _limits(max_consecutive_losses=3))

    assert s["consecutive_losses"] == 4
    assert s["kill_switch"] is not None  # больше не "молчит"
    assert "4 убыточных" in s["kill_switch"]["reason"]
    assert "reason" in tripped  # queue_manager.set_kill_switch реально был вызван


def test_status_does_not_trip_when_within_limits(monkeypatch):
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch", lambda: None)
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)

    def _fail_if_called(reason):
        raise AssertionError("set_kill_switch не должен был вызываться")

    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", _fail_if_called)
    client = _FakeClient(positions=[], wallet_balance=10_000.0, income_rows=_income([10, -5]))
    s = risk_guard.status(client, _limits(max_consecutive_losses=3))
    assert s["kill_switch"] is None
    assert s["consecutive_losses"] == 1


def test_status_reports_already_tripped_kill_switch_without_retripping(monkeypatch):
    calls = []
    monkeypatch.setattr(risk_guard.queue_manager, "get_kill_switch",
                         lambda: {"reason": "уже взведён ранее", "tripped_at": 0})
    monkeypatch.setattr(risk_guard.queue_manager, "set_kill_switch", lambda reason: calls.append(reason))
    monkeypatch.setattr(risk_guard.queue_manager, "get_risk_daily_baseline", lambda day: 10_000.0)
    monkeypatch.setattr(risk_guard.queue_manager, "set_risk_daily_baseline", lambda day, bal: None)
    client = _FakeClient(positions=[], wallet_balance=10_000.0, income_rows=[])
    s = risk_guard.status(client, _limits())
    assert s["kill_switch"]["reason"] == "уже взведён ранее"
    assert calls == []  # не должен пытаться взвести повторно то, что уже взведено


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
