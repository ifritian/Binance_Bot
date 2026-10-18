import pytest

import binance_alerts as ba
import config
import queue_manager

NOW = 1_792_300_000.0


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")


def _it(code, kind, title, ts=NOW - 600, tickers=()):
    return {"code": code, "kind": kind, "title": title, "ts": ts, "tickers": list(tickers)}


ITEMS = [
    _it("d1", "delist", "Binance Will Delist EPIC, QUICK on 2026-10-28", tickers=["EPIC", "QUICK"]),
    _it("n1", "network", "Binance Will Support the Filecoin (FIL) Network Upgrade & Hard Fork - 2026-10-19", tickers=["FIL"]),
    _it("n2", "network", "Binance Will Support Micron Technology (MU) Cash Dividend Distribution via bStocks", tickers=["MU"]),
    _it("e1", "event", "Spot Trading Tournament Season 6"),
]


def test_first_run_seeds_and_sends_nothing():
    assert ba.run_once(NOW, lambda n, h: ITEMS, lambda t: {}) == []
    assert ba.run_once(NOW, lambda n, h: ITEMS, lambda t: {}) == []  # всё уже прочитано при засеве


def test_new_alert_after_seed_and_filtering():
    ba.run_once(NOW, lambda n, h: ITEMS[:0], lambda t: {})
    out = ba.run_once(NOW, lambda n, h: ITEMS, lambda t: {"EPIC": -3.21, "FIL": 1.5})
    codes = [c for c, _ in out]
    assert codes == ["d1", "n1"]                    # дивиденды и конкурсы не алертим
    assert "Delisting notice" in out[0][1] and "EPIC −3.2%" in out[0][1] and "detail/d1" in out[0][1]
    assert "Network notice" in out[1][1]
    assert all("$" not in t for _, t in out)


def test_sent_alerts_not_repeated_and_unsent_retry():
    ba.run_once(NOW, lambda n, h: [], lambda t: {})
    out = ba.run_once(NOW, lambda n, h: ITEMS, lambda t: {})
    ba.mark_sent(out[0][0])
    again = ba.run_once(NOW, lambda n, h: ITEMS, lambda t: {})
    assert [c for c, _ in again] == ["n1"]


def test_limit_per_tick():
    ba.run_once(NOW, lambda n, h: [], lambda t: {})
    many = [_it(f"x{i}", "delist", f"Binance Will Delist T{i} on 2026-11-01", ts=NOW - i) for i in range(8)]
    assert len(ba.run_once(NOW, lambda n, h: many, lambda t: {})) == ba.MAX_PER_TICK
