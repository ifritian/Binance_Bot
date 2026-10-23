import random
from datetime import datetime, timezone

import pytest

import binance_publisher as bp
import config
import event_posts
import main
import market_digest
import queue_manager
import video_post as vp

DATA = {"majors": [("BTC", 82146.0, -1.3), ("ETH", 2427.0, -1.1)],
        "gainers": [("FIL", 3.1, 8.0), ("ONDO", 1.1, 7.7)], "losers": [("RUNE", 5.0, -6.3)]}
FNG = {"value": 68, "label": "Greed", "prev": 63}


@pytest.fixture(autouse=True)
def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(queue_manager, "should_retry_now", lambda k: True)
    monkeypatch.setattr(queue_manager, "set_retry_backoff", lambda k, h: store.__setitem__("backoff:" + k, h))
    monkeypatch.setattr(queue_manager, "seconds_since_last_post", lambda k="x": 10 ** 9)
    monkeypatch.setattr(queue_manager, "set_last_post_time", lambda *a, **k: None)
    return store


def test_caption_has_at_most_three_cashtags_and_no_promises():
    for s in range(20):
        t = vp.build_caption(DATA, FNG, random.Random(s))
        assert 1 <= t.count("$") <= 3 and "guarantee" not in t.lower()


def test_render_makes_a_playable_vertical_video(tmp_path):
    mp4, poster = vp.render(DATA, FNG, tmp_path / "v.mp4", duration=1.5, fps=6)
    assert mp4.exists() and mp4.stat().st_size > 1000 and poster.exists()
    from PIL import Image
    assert Image.open(poster).size == (720, 1280)


def test_publish_video_follows_official_flow(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(bp, "_recent_duplicate", lambda t: None)
    monkeypatch.setattr(bp, "_api_v2", lambda ep, body: calls.append((ep, body)) or {"presignedUrl": "u", "fileTicket": "T1"})
    monkeypatch.setattr(bp, "_upload_to_s3", lambda *a: calls.append(("s3", a[2])))
    monkeypatch.setattr(bp, "_poll_processing", lambda t: calls.append(("poll", t)) or {})
    monkeypatch.setattr(bp, "upload_image", lambda p: "https://cover")
    sent = []
    monkeypatch.setattr(bp, "_send_content", lambda body: sent.append(body) or {"id": "9", "status": "ok"})
    monkeypatch.setattr(bp, "_log_post", lambda *a: None)
    v = tmp_path / "v.mp4"; v.write_bytes(b"x" * 10)
    res = bp.publish_video("caption $BTC", v, 15, tmp_path / "c.png")
    assert res["id"] == "9" and calls[0][0] == "/video/preSign" and ("s3", "video/mp4") in calls and ("poll", "T1") in calls
    b = sent[0]
    assert b["contentType"] == 3 and b["fileTicket"] == "T1" and b["cover"] == "https://cover" and b["videoTimeSeconds"] == 15.0 and b["isPublish"] is True


def test_try_publish_video_once_a_day_and_survives_failures(monkeypatch, _mem, tmp_path):
    monkeypatch.setattr(config, "VIDEO_ENABLED", True)
    monkeypatch.setattr(config, "VIDEO_HOUR_UTC", 15)
    monkeypatch.setattr(event_posts, "now_utc", lambda: datetime(2026, 10, 24, 16, tzinfo=timezone.utc))
    monkeypatch.setattr(market_digest, "fetch_movers", lambda rows=None: DATA)
    monkeypatch.setattr(event_posts, "fetch_fng", lambda: FNG)
    monkeypatch.setattr(main, "_square_slot_available", lambda: True)
    monkeypatch.setattr(vp, "render", lambda *a, **k: (tmp_path / "v.mp4", tmp_path / "p.png"))
    sent = []
    monkeypatch.setattr(bp, "publish_video", lambda text, v, d, c: sent.append(text) or {"id": "1", "status": "ok"})
    main.try_publish_video()
    main.try_publish_video()
    assert len(sent) == 1 and _mem["video_done"] == "2026-10-24"
    # ошибка API: слот не считается выполненным, ставится пауза
    _mem.pop("video_done")
    monkeypatch.setattr(bp, "publish_video", lambda *a: (_ for _ in ()).throw(bp.PublishError("boom")))
    monkeypatch.setattr(main, "_record_publish_error", lambda *a: None)
    main.try_publish_video()
    assert "video_done" not in _mem and _mem["backoff:video"] == 3
    # вне окна - ничего
    monkeypatch.setattr(event_posts, "now_utc", lambda: datetime(2026, 10, 24, 9, tzinfo=timezone.utc))
    n = len(sent); main.try_publish_video(); assert len(sent) == n
