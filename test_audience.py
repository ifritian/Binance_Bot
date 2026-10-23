import audience
import queue_manager


def test_record_summary_and_goal(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    import time
    now = time.time()
    audience.record(100, now - 6 * 86400)
    log = audience.record(130, now)
    assert len(log) == 2 and audience.record(131, now)[-1][1] == 131      # одна запись в сутки
    s = audience.summary(log)
    assert "130 подписчиков" in s and "осталось 870" in s and "+30" in s
    assert "порог 1000 пройден" in audience.summary([["2026-10-22", 1200]])
    assert "нет данных" in audience.summary([])


def test_site_cta_and_counter(monkeypatch):
    import config, site_builder as sb
    monkeypatch.setattr(config, "GOATCOUNTER_CODE", "mysite")
    out = sb.render({"snapshots": [], "closed": [], "open": {}, "listings": [], "listings_waiting": [], "posts": []}, 1_792_300_000, ls=[("Telegram", "https://t.me/x")])
    assert "Open a Binance account" in out and "sponsored" in out and "mysite.goatcounter.com" in out and "referral links" in out
