import pytest

import config
import listing_tracker as lt
import post_cards
import queue_manager

T0 = 1_792_000_000.0  # момент анонса


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")


def _row(t, o, h, l, c, qv=1000.0):
    return [int(t * 1000), str(o), str(h), str(l), str(c), "1", int(t * 1000) + 59999, str(qv)]


class FakeMarket:
    """Пара появляется в момент list_t; цена растёт на 1% в час."""
    def __init__(self, list_t, exists_before=False):
        self.list_t, self.before = list_t, exists_before
        self.calls = 0

    def __call__(self, symbol, interval, start_ms, limit):
        self.calls += 1
        start = start_ms / 1000.0
        if interval == "1m":
            t = max(start, self.list_t) if not self.before else start
            return [_row(t, 1.0, 1.0, 1.0, 1.0)] if t <= T0 + 30 * 86400 and (self.before or start <= self.list_t + 3600 * 5) and self.list_t < T0 + 99 * 86400 else []
        step = 3600
        rows = []
        for k in range(limit):
            t = start + k * step
            price = 1.0 * (1.01 ** ((t - self.list_t) / 3600))
            rows.append(_row(t, price, price * 1.02, price * 0.98, price, qv=2_000_000))
        return rows


ANN = [{"code": "c1", "name": "Foo", "ticker": "FOO", "ts": T0}]


def test_waits_until_pair_trades_then_reports_d1_and_d7():
    lt.register(ANN, T0)
    # пары ещё нет
    assert lt.advance(T0 + 600, lambda *a: []) == []
    mk = FakeMarket(T0 + 7200)
    assert lt.advance(T0 + 7300, mk) == []                       # торги начались, до отчёта далеко
    ev = lt.advance(T0 + 7200 + 86400 + 700, mk)
    assert [e["kind"] for e in ev] == ["d1"]
    assert ev[0]["stats"]["pct_close"] > 20 and ev[0]["stats"]["quote_volume"] == 24 * 2_000_000
    ev7 = lt.advance(T0 + 7200 + 7 * 86400 + 700, mk)
    assert [e["kind"] for e in ev7] == ["d7"]
    assert ev7[0]["stats"]["pct_d1"] is not None
    assert lt.summary("d7")["n"] == 1


def test_pair_traded_before_announcement_is_skipped():
    lt.register(ANN, T0)
    mk = FakeMarket(T0 - 5 * 86400, exists_before=True)
    assert lt.advance(T0 + 600, mk) == []
    assert queue_manager._get(lt.STATE_KEY)["tracked"]["c1"]["stage"] == "skip"


def test_expires_if_never_lists_and_register_is_idempotent():
    assert lt.register(ANN, T0) == 1 and lt.register(ANN, T0) == 0
    lt.advance(T0 + 11 * 86400, lambda *a: [])
    assert queue_manager._get(lt.STATE_KEY)["tracked"]["c1"]["stage"] == "expired"


def test_network_error_is_not_fatal():
    lt.register(ANN, T0)
    assert lt.advance(T0 + 600, lambda *a: None) == []


def test_pending_event_ttl_and_mark_posted():
    lt.register(ANN, T0)
    mk = FakeMarket(T0 + 7200)
    lt.advance(T0 + 7300, mk)
    lt.advance(T0 + 7200 + 86400 + 700, mk)
    now = T0 + 7200 + 86400 + 800
    ev = lt.pending_event(now)
    assert ev and ev["id"] == "c1:d1"
    assert lt.pending_event(now + 25 * 3600) is None            # устарел
    lt.mark_posted(ev["id"])
    assert lt.pending_event(now) is None


def test_text_and_card():
    lt.register(ANN, T0)
    mk = FakeMarket(T0 + 7200)
    lt.advance(T0 + 7300, mk)
    ev = lt.advance(T0 + 7200 + 86400 + 700, mk)[0]
    txt = lt.build_text(ev, None)
    assert "$FOO" in txt and txt.count("$") == 2 and "Not financial advice" in txt and "tracked" not in txt
    txt2 = lt.build_text(ev, {"n": 5, "avg": 12.3, "median": 4.0, "up": 3})
    assert "All 5 Binance listings" in txt2 and "Small sample" in txt2
    p = post_cards.listing_card(ev)
    assert p and p.exists()
