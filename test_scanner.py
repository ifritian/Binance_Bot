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
