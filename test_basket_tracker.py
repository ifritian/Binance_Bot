import pytest

import basket_tracker as bt
import config
import queue_manager

T = 1_792_000_000.0


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")


def test_first_tick_seeds_at_100_then_waits_six_hours():
    assert bt.tick(T, lambda h: (1, 1, 1)) is True
    assert bt.points()[0]["index_value"] == 100.0
    assert bt.tick(T + 3600, lambda h: (1, 1, 1)) is False


def test_compounds_and_uses_elapsed_hours():
    bt.tick(T, lambda h: None)
    seen = []
    assert bt.tick(T + 6 * 3600 + 120, lambda h: (seen.append(h) or (2.0, 1.0, -1.0))) is True
    p = bt.points()[-1]
    assert seen == [7] and p["index_value"] == 102.0 and p["btc_value"] == 101.0 and p["eth_value"] == 99.0
    bt.tick(T + 13 * 3600, lambda h: (1.0, 0.0, 0.0))
    assert abs(bt.points()[-1]["index_value"] - 102 * 1.01) < 1e-3


def test_failure_does_not_add_points_and_gap_is_capped():
    bt.tick(T, lambda h: None)
    assert bt.tick(T + 7 * 3600, lambda h: None) is False and len(bt.points()) == 1
    seen = []
    bt.tick(T + 100 * 3600, lambda h: (seen.append(h) or (0, 0, 0)))
    assert seen == [bt.MAX_GAP_H]


def test_series_falls_back_to_treasury_snapshots():
    queue_manager._set("treasury_snapshots", [{"timestamp": 1, "index_value": 99, "btc_value": 99, "eth_value": 99}] * 3)
    assert len(bt.series()) == 3
    bt.tick(T, lambda h: None)
    bt.tick(T + 7 * 3600, lambda h: (1, 1, 1))
    assert bt.series() == bt.points()
