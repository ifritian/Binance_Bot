import random
from datetime import datetime, timezone

import config
import main
import market_digest
import micro_moments as mm
import queue_manager


def dt(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_context_line_is_calendar_true():
    sat = mm.context_line(dt(2026, 10, 24), random.Random(1))
    assert sat and ("weekend" in sat.lower())
    assert mm.context_line(dt(2026, 10, 21, 20), random.Random(1)) is None        # среда вечером, не конец месяца
    fri = {mm.context_line(dt(2026, 10, 23, 20), random.Random(i)) for i in range(10)}
    assert any("expiry" in (x or "") for x in fri)
    # открытие США: летнее время 13:30, после 1 ноября 14:30
    assert "13:30" in mm.context_line(dt(2026, 10, 21, 11), random.Random(1))
    assert "14:30" in mm.context_line(dt(2026, 11, 3, 11), random.Random(1))


def test_fng_streak():
    assert mm.fng_streak_line([20, 22, 18, 40, 50]).startswith(("That's day 3", "3 days")) 
    assert mm.fng_streak_line([60, 30, 40]) is None
    assert mm.fng_streak_line([10] + [30] * 15) == "Lowest reading in 16 days."
    assert mm.fng_streak_line(None) is None


def test_followup_text_verdicts():
    px = lambda x: f"{x:g}"
    rec = {"ticker": "SUI", "price": 2.0, "ts": 0, "hi": 2.2, "lo": 1.9}
    up = mm.followup_text(rec, 2.3, px, random.Random(1))
    dn = mm.followup_text(rec, 1.8, px, random.Random(1))
    mid = mm.followup_text(rec, 2.05, px, random.Random(1))
    assert "above 2.2" in up and "below 1.9" in dn and "Neither level broke" in mid
    for t in (up, dn, mid):
        assert t.count("$") == 1 and "Not financial advice" in t
    assert mm.followup_due(rec, 7 * 3600) and not mm.followup_due(rec, 3600) and not mm.followup_due(rec, 40 * 3600)


def test_followup_published_once(monkeypatch, tmp_path):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(config, "SQUARE_TEMPLATE_LANG", "en")
    import time
    now = time.time()
    queue_manager._set("mover_followups", [{"ticker": "SUI", "price": 2.0, "ts": now - 8 * 3600, "hi": 2.2, "lo": 1.9, "done": False}])
    sent = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda text, image_paths=None: sent.append(text) or {})
    monkeypatch.setattr(market_digest, "fetch_price", lambda t: 2.3)
    monkeypatch.setattr(main, "_coin_chart", lambda *a, **k: None)
    main.try_publish_mover_followup()
    queue_manager.set_last_post_time("square_global", now - 7200)
    main.try_publish_mover_followup()
    assert len(sent) == 1 and "$SUI" in sent[0]
