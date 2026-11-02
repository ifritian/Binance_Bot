import binance_breaking as bb
import queue_manager


def _item(code, title, kind, tickers, ts=1000.0):
    return {"code": code, "title": title, "kind": kind, "tickers": tickers, "ts": ts}


def test_significance():
    liquid = {"BTC", "SOL"}
    assert bb.significance(_item("a", "Binance Will Delist SOL on 2026-11-10", "delist", ["SOL"]), liquid) == "delist"
    assert bb.significance(_item("b", "Binance Will Delist XYZ", "delist", ["XYZ"]), liquid) is None
    assert bb.significance(_item("c", "Binance Will Temporarily Suspend Withdrawals on Network", "network", []), liquid) == "emergency"
    assert bb.significance(_item("d", "Weekly update", "news", []), liquid) is None


def test_text_hook_and_no_links():
    t = bb.build_text(_item("a", "Binance Will Delist SOL", "delist", ["SOL"]), "delist", {"SOL": -3.2})
    assert t.split("\n")[0].startswith(("STOP", "BREAKING", "Wait")) and "$SOL" in t and "http" not in t


def test_run_once_seed_then_pick(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k: store.get(k))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    old = _item("old", "Binance Will Delist SOL", "delist", ["SOL"])
    assert bb.run_once(2000.0, {"SOL"}, fetch=lambda n, h: [old], fetch_changes=lambda t: {}) == []   # seeding
    new = _item("new", "Binance Will Delist SOL again", "delist", ["SOL"], ts=1900.0)
    out = bb.run_once(2000.0, {"SOL"}, fetch=lambda n, h: [old, new], fetch_changes=lambda t: {"SOL": -1.0})
    assert [c for c, _ in out] == ["new"]
    bb.mark_sent("new", 2000.0)
    assert bb.run_once(2000.0, {"SOL"}, fetch=lambda n, h: [old, new], fetch_changes=lambda t: {}) == []
    assert bb.posts_last_day(2000.0) == 1
