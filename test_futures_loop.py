#!/usr/bin/env python3
"""Тесты futures_loop.run_one_iteration - в основном про то, ЧТО
вызывается (управление позициями всегда, скан рынка - только если есть
свободное место), а не про реальную биржевую логику (та уже покрыта
test_futures_position_monitor.py/test_futures_auto_trade-эквивалентом)."""
import futures_loop
import risk_guard


class _FakeClient:
    def __init__(self, open_positions_count=0):
        self.open_positions_count = open_positions_count

    def get_all_positions(self):
        return [{"symbol": f"SYM{i}USDT"} for i in range(self.open_positions_count)]


def _limits(max_open=3):
    return risk_guard.RiskLimits(max_open_positions=max_open, max_daily_loss_pct=5.0, max_consecutive_losses=3)


def test_skips_scan_when_at_capacity(monkeypatch):
    monkeypatch.setattr(futures_loop.futures_position_monitor, "check_and_manage_all", lambda client, dry_run: None)

    def _fail_if_called(*a, **k):
        raise AssertionError("run_cycle не должен был вызываться при заполненных слотах")

    monkeypatch.setattr(futures_loop, "run_cycle", _fail_if_called)

    client = _FakeClient(open_positions_count=3)
    futures_loop.run_one_iteration(client, _limits(max_open=3), live=True)  # не должно бросать


def test_scans_when_slot_free(monkeypatch):
    monkeypatch.setattr(futures_loop.futures_position_monitor, "check_and_manage_all", lambda client, dry_run: None)
    calls = []
    monkeypatch.setattr(futures_loop, "run_cycle",
                         lambda client, limits, live: (calls.append(live),
                                                        {"executed": [], "skipped_dry_run": []})[1])

    client = _FakeClient(open_positions_count=1)
    futures_loop.run_one_iteration(client, _limits(max_open=3), live=True)
    assert calls == [True]


def test_position_management_always_runs_even_at_capacity(monkeypatch):
    managed_calls = []
    monkeypatch.setattr(futures_loop.futures_position_monitor, "check_and_manage_all",
                         lambda client, dry_run: managed_calls.append(dry_run))
    monkeypatch.setattr(futures_loop, "run_cycle", lambda client, limits, live: {"executed": [], "skipped_dry_run": []})

    client = _FakeClient(open_positions_count=3)
    futures_loop.run_one_iteration(client, _limits(max_open=3), live=False)
    assert managed_calls == [True]  # dry_run=not live=True


def test_dry_run_passed_through_to_position_management(monkeypatch):
    managed_calls = []
    monkeypatch.setattr(futures_loop.futures_position_monitor, "check_and_manage_all",
                         lambda client, dry_run: managed_calls.append(dry_run))
    monkeypatch.setattr(futures_loop, "run_cycle", lambda client, limits, live: {"executed": [], "skipped_dry_run": []})

    client = _FakeClient(open_positions_count=0)
    futures_loop.run_one_iteration(client, _limits(max_open=3), live=True)
    assert managed_calls == [False]  # dry_run=not live=False


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
