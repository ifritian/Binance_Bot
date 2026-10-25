import telegram_bot as tb
import queue_manager


def fetch():
    bars = [(i, 1, 110, 90, 100, 1) for i in range(30)]
    return {"klines": lambda t: bars, "price": lambda t: 100.0, "funding": lambda: None, "fng": lambda: {"value": 50, "label": "Neutral"}}


def test_parse_and_args():
    assert tb.parse("/levels@mybot btc") == ("levels", ["btc"])
    assert tb.parse("hello") == ("", [])
    assert tb.ticker_arg(["$sol"]) == "SOL" and tb.ticker_arg(["a;drop"]) is None and tb.ticker_arg([]) == "BTC"


def test_levels_and_alerts(monkeypatch):
    monkeypatch.setattr(tb.coin_whitelist, "is_allowed", lambda t: t in ("BTC", "ETH"))
    f, al = fetch(), []
    assert "24h high 110" in tb.answer("levels", ["BTC"], 1, al, f)
    assert "major coin" in tb.answer("levels", ["XYZ"], 1, al, f)
    assert "crosses" in tb.answer("alert", ["BTC", "120"], 1, al, f) and al[0]["dir"] == "up"
    assert tb.answer("alert", ["BTC", "abc"], 1, al, f).startswith("Usage")
    for _ in range(6):
        out = tb.answer("alert", ["ETH", "50"], 1, al, f)
    assert "already have" in out
    assert tb.answer("stop", [], 1, al, f).startswith("Removed") and al == []


def test_due_alerts():
    al = [{"uid": 1, "t": "BTC", "level": 120, "dir": "up"}, {"uid": 2, "t": "BTC", "level": 90, "dir": "down"}]
    hit, keep = tb.due_alerts(al, {"BTC": 121})
    assert len(hit) == 1 and hit[0][0]["uid"] == 1 and len(keep) == 1


def test_rate_limit():
    st = {}
    assert all(tb.rate_ok(1, 100.0, st) for _ in range(tb.MAX_CMDS_PER_HOUR)) and not tb.rate_ok(1, 101.0, st)


def test_poll_private_only(monkeypatch):
    store = {}
    monkeypatch.setattr(tb.queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(tb.queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(tb.config, "TELEGRAM_BOT_TOKEN", "x")
    ups = [{"update_id": 5, "message": {"chat": {"id": 7, "type": "private"}, "text": "/mood"}},
           {"update_id": 6, "message": {"chat": {"id": -9, "type": "group"}, "text": "/mood"}}]
    sent = []
    n = tb.poll(fetch(), get_updates=lambda off: ups, send_fn=lambda c, t: sent.append((c, t)))
    assert n == 1 and sent[0][0] == 7 and store[tb.KEY_OFFSET] == 7
