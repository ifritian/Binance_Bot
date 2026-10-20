import time

import pytest
import requests

import binance_publisher as bp
import main
import queue_manager


@pytest.fixture
def store(monkeypatch):
    d = {}
    monkeypatch.setattr(queue_manager, "_get", lambda k, default=None: d.get(k, default))
    monkeypatch.setattr(queue_manager, "_set", lambda k, v: d.__setitem__(k, v))
    monkeypatch.setattr(bp, "_NEXT_FORMAT", None)
    return d


def _log(fmt, text, age_h):
    return {"ts": int(time.time() - age_h * 3600), "fmt": fmt, "id": "1",
            "head": " ".join(text.split())[:90], "len": len(text), "img": False}


def test_same_format_too_soon_is_skipped(store, monkeypatch):
    store["square_post_log"] = [_log("digest", "☀️ Morning market snapshot", 1.0)]
    monkeypatch.setattr(bp, "_send_content", lambda body: pytest.fail("must not send"))
    bp.tag_format("digest")
    r = bp.publish_post("☀️ Morning market snapshot (24h): compact")
    assert r["status"] == "duplicate_skipped"


def test_same_format_after_gap_is_allowed(store, monkeypatch):
    store["square_post_log"] = [_log("digest", "old", 5.0)]
    monkeypatch.setattr(bp, "_send_content", lambda body: {"id": "9", "status": "ok"})
    bp.tag_format("digest")
    assert bp.publish_post("new text")["status"] == "ok"


def test_identical_text_skipped_for_untracked_format(store, monkeypatch):
    store["square_post_log"] = [_log("hot_mover", "same text here", 2.0)]
    monkeypatch.setattr(bp, "_send_content", lambda body: pytest.fail("must not send"))
    bp.tag_format("hot_mover")
    assert bp.publish_post("same text here")["status"] == "duplicate_skipped"


def test_read_timeout_is_not_retried(monkeypatch):
    def boom(*a, **k):
        raise requests.ReadTimeout("Read timed out")
    monkeypatch.setattr(bp.requests, "post", boom)
    r = bp._send_content({"bodyTextOnly": "x"})
    assert r["status"] == "unknown_network"


def test_connect_timeout_still_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectTimeout("connect")
    monkeypatch.setattr(bp.requests, "post", boom)
    with pytest.raises(bp.PublishError):
        bp._send_content({"bodyTextOnly": "x"})


def test_crosspost_removed_before_send(store, monkeypatch):
    sent = []
    monkeypatch.setattr(main.telegram_publisher, "is_configured", lambda: True)
    monkeypatch.setattr(main, "_publish_scheduled_telegram", lambda data: sent.append(data))
    queue_manager.push_pending_crosspost("telegram", time.time() - 1, {"text": "t", "ticker": None})
    main._process_pending_crossposts()
    main._process_pending_crossposts()
    assert len(sent) == 1 and queue_manager._get("crosspost_queue") == []


def test_failed_crosspost_is_not_resent(store, monkeypatch):
    calls = []
    def fail(data):
        calls.append(1); raise RuntimeError("boom after send")
    monkeypatch.setattr(main, "_publish_scheduled_telegram", fail)
    queue_manager.push_pending_crosspost("telegram", time.time() - 1, {"text": "t", "ticker": None})
    main._process_pending_crossposts()
    main._process_pending_crossposts()
    assert len(calls) == 1
