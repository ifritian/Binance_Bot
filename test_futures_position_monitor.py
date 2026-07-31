#!/usr/bin/env python3
"""Тесты futures_position_monitor.py - трейлинг-стоп и обнаружение
закрытия позиций, на ПОДДЕЛЬНОМ FuturesClient/futures_state/alerting."""
import futures_position_monitor as monitor
from futures_client import FuturesApiError


# --- compute_new_trailing_stop: чистая математика, без сети ---

def test_trailing_stop_long_tightens_when_price_moves_favorably():
    # long: trail_distance=5, mark=110 -> candidate=105, текущий стоп=95 -> двигаем
    new_stop = monitor.compute_new_trailing_stop("BUY", trail_distance=5, mark_price=110, current_stop_price=95)
    assert new_stop == 105


def test_trailing_stop_long_does_not_loosen_on_pullback():
    # цена откатила ниже уровня, откуда стоп уже был подтянут - НЕ двигаем назад
    new_stop = monitor.compute_new_trailing_stop("BUY", trail_distance=5, mark_price=98, current_stop_price=105)
    assert new_stop is None


def test_trailing_stop_short_tightens_when_price_moves_favorably():
    new_stop = monitor.compute_new_trailing_stop("SELL", trail_distance=5, mark_price=90, current_stop_price=105)
    assert new_stop == 95


def test_trailing_stop_short_does_not_loosen_on_pullback():
    new_stop = monitor.compute_new_trailing_stop("SELL", trail_distance=5, mark_price=102, current_stop_price=95)
    assert new_stop is None


def test_trailing_stop_zero_distance_never_moves():
    assert monitor.compute_new_trailing_stop("BUY", trail_distance=0, mark_price=200, current_stop_price=95) is None


# --- manage_position: находит стоп-ордер, решает, трейлить ли ---

class _FakeClient:
    def __init__(self, orders=None, mark_price=100.0, positions=None, income_rows=None,
                 fail_replace=False, fail_mark_price=False):
        self.orders = orders if orders is not None else []
        self.mark_price = mark_price
        self.positions = positions if positions is not None else []
        self.income_rows = income_rows or []
        self.fail_replace = fail_replace
        self.fail_mark_price = fail_mark_price
        self.replace_calls = []

    def get_open_orders(self, symbol):
        return self.orders

    def get_mark_price(self, symbol):
        if self.fail_mark_price:
            raise FuturesApiError("симулированный сбой цены")
        return self.mark_price

    def get_all_positions(self):
        return self.positions

    def get_income_history(self, income_type="REALIZED_PNL", limit=10):
        return self.income_rows


def test_manage_position_no_stop_order_found_logs_and_returns():
    client = _FakeClient(orders=[{"type": "TAKE_PROFIT_MARKET", "algoId": 1, "triggerPrice": "120"}])
    managed = {"side": "BUY", "trail_distance": 5}
    monitor.manage_position(client, "BTCUSDT", managed)  # не должно бросать исключение


def test_manage_position_calls_replace_when_favorable(monkeypatch):
    client = _FakeClient(
        orders=[{"type": "STOP_MARKET", "algoId": 42, "triggerPrice": "95"}],
        mark_price=110.0,
    )
    calls = []
    monkeypatch.setattr(monitor, "replace_stop_order",
                         lambda c, symbol, side, old_id, new_price: calls.append((symbol, side, old_id, new_price)))
    managed = {"side": "BUY", "trail_distance": 5}
    monitor.manage_position(client, "BTCUSDT", managed)
    assert calls == [("BTCUSDT", "SELL", 42, 105.0)]


def test_manage_position_skips_replace_when_not_favorable(monkeypatch):
    client = _FakeClient(
        orders=[{"type": "STOP_MARKET", "algoId": 42, "triggerPrice": "105"}],
        mark_price=98.0,
    )

    def _fail_if_called(*a, **k):
        raise AssertionError("replace_stop_order не должен был вызываться")

    monkeypatch.setattr(monitor, "replace_stop_order", _fail_if_called)
    managed = {"side": "BUY", "trail_distance": 5}
    monitor.manage_position(client, "BTCUSDT", managed)


def test_manage_position_dry_run_never_calls_replace(monkeypatch):
    client = _FakeClient(
        orders=[{"type": "STOP_MARKET", "algoId": 42, "triggerPrice": "95"}],
        mark_price=110.0,
    )

    def _fail_if_called(*a, **k):
        raise AssertionError("replace_stop_order не должен был вызываться в dry-run")

    monkeypatch.setattr(monitor, "replace_stop_order", _fail_if_called)
    managed = {"side": "BUY", "trail_distance": 5}
    monitor.manage_position(client, "BTCUSDT", managed, dry_run=True)


def test_manage_position_handles_mark_price_failure_gracefully():
    client = _FakeClient(
        orders=[{"type": "STOP_MARKET", "algoId": 42, "triggerPrice": "95"}],
        fail_mark_price=True,
    )
    monitor.manage_position(client, "BTCUSDT", {"side": "BUY", "trail_distance": 5})  # не должно бросать


# --- check_and_manage_all: обнаружение закрытия + алерт + продолжение при ошибках ---

def test_check_and_manage_all_detects_closed_position_and_alerts(monkeypatch):
    client = _FakeClient(positions=[], income_rows=[{"symbol": "BTCUSDT", "income": "12.5"}])
    monkeypatch.setattr(monitor.futures_state, "list_managed_positions",
                         lambda: {"BTCUSDT": {"side": "BUY", "trail_distance": 5}})
    unregistered = []
    monkeypatch.setattr(monitor.futures_state, "unregister_managed_position", lambda symbol: unregistered.append(symbol))
    alerts = []
    monkeypatch.setattr(monitor.alerting, "send_owner_alert",
                         lambda key, msg, state=None: alerts.append((key, msg)))

    monitor.check_and_manage_all(client)

    assert unregistered == ["BTCUSDT"]
    assert len(alerts) == 1
    assert "закрылась" in alerts[0][1]
    assert "+12.5000" in alerts[0][1]


def test_check_and_manage_all_manages_still_open_position(monkeypatch):
    client = _FakeClient(
        positions=[{"symbol": "BTCUSDT"}],
        orders=[{"type": "STOP_MARKET", "algoId": 1, "triggerPrice": "95"}],
        mark_price=110.0,
    )
    monkeypatch.setattr(monitor.futures_state, "list_managed_positions",
                         lambda: {"BTCUSDT": {"side": "BUY", "trail_distance": 5}})
    calls = []
    monkeypatch.setattr(monitor, "replace_stop_order",
                         lambda c, symbol, side, old_id, new_price: calls.append(new_price))

    monitor.check_and_manage_all(client)
    assert calls == [105.0]


def test_check_and_manage_all_empty_registry_makes_no_calls():
    client = _FakeClient()

    def _fail_if_called():
        raise AssertionError("get_all_positions не должен был вызываться для пустого реестра")

    client.get_all_positions = _fail_if_called
    monitor.check_and_manage_all(client)  # не должно бросать исключение


def test_check_and_manage_all_continues_after_one_position_errors(monkeypatch):
    client = _FakeClient(positions=[{"symbol": "AUSDT"}, {"symbol": "BUSDT"}])
    monkeypatch.setattr(monitor.futures_state, "list_managed_positions",
                         lambda: {"AUSDT": {"side": "BUY", "trail_distance": 5},
                                  "BUSDT": {"side": "BUY", "trail_distance": 5}})

    handled = []

    def _fake_manage(client_, symbol, managed, dry_run=False):
        if symbol == "AUSDT":
            raise RuntimeError("симулированный сбой на AUSDT")
        handled.append(symbol)

    monkeypatch.setattr(monitor, "manage_position", _fake_manage)
    monitor.check_and_manage_all(client)  # не должно бросать, несмотря на сбой на AUSDT
    assert handled == ["BUSDT"]


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
