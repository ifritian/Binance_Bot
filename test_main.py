#!/usr/bin/env python3
"""
Тесты main._post_outcome_updates_to_bluesky - форматы "До/После" (реплай
в исходный тред на любой исход) и "Win-reveal" (отдельный пост только на
win). Всё, что реально ходит в сеть (bluesky_publisher.publish_post),
замокано - проверяем только логику принятия решений.
"""
import types

import config
import main


class _FakeResponse:
    def __init__(self, json_data, ok=True):
        self._json = json_data
        self.ok = ok

    def json(self):
        return self._json


def _closed_record(**overrides) -> dict:
    base = dict(
        ticker="BEAT", direction="short", strategy="RSI + Bollinger Touch",
        entry=2.21, stop=2.2371, target=2.1729, result="win",
        exit_price=2.1729, pnl_pct=1.72, mfe_pct=1.9,
        bluesky_ref={"uri": "at://did:plc:abc/app.bsky.feed.post/1", "cid": "bafy1"},
    )
    base.update(overrides)
    return base


def test_skips_entirely_when_bluesky_not_configured(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
    calls = []
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda *a, **k: calls.append((a, k)))

    main._post_outcome_updates_to_bluesky([_closed_record()])

    assert calls == []


def test_win_posts_both_reply_and_win_reveal(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    calls = []
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda text, **k: calls.append((text, k)))

    main._post_outcome_updates_to_bluesky([_closed_record(result="win")])

    # 2 вызова: реплай "До/После" в исходный тред + отдельный Win-reveal.
    assert len(calls) == 2
    reply_text, reply_kwargs = calls[0]
    assert "reply_to" in reply_kwargs and reply_kwargs["reply_to"] is not None
    win_text, win_kwargs = calls[1]
    assert win_kwargs.get("reply_to") is None
    assert "BEAT" in win_text


def test_loss_posts_only_reply_no_win_reveal(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    calls = []
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda text, **k: calls.append((text, k)))

    main._post_outcome_updates_to_bluesky([_closed_record(result="loss", pnl_pct=-1.15)])

    assert len(calls) == 1
    assert calls[0][1]["reply_to"] is not None


def test_no_bluesky_ref_skips_reply_but_keeps_win_reveal(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    calls = []
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda text, **k: calls.append((text, k)))

    main._post_outcome_updates_to_bluesky([_closed_record(result="win", bluesky_ref=None)])

    # Без bluesky_ref реплай "До/После" отправить некуда - только Win-reveal.
    assert len(calls) == 1
    assert calls[0][1].get("reply_to") is None


def test_one_bad_record_does_not_block_the_rest(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    calls = []

    def _fake_publish(text, **kwargs):
        if "OOPS" in text:
            raise main.bluesky_publisher.BlueskyPublishError("boom")
        calls.append(text)

    monkeypatch.setattr(main.bluesky_publisher, "publish_post", _fake_publish)
    monkeypatch.setattr(
        main.post_format, "build_bluesky_outcome_reply",
        lambda record: ("OOPS" if record["ticker"] == "BAD" else "ok-reply", []),
    )

    records = [_closed_record(ticker="BAD", result="loss"), _closed_record(ticker="BEAT", result="loss")]
    main._post_outcome_updates_to_bluesky(records)

    # Ошибка на первой записи не должна помешать обработать вторую.
    assert "ok-reply" in calls


if __name__ == "__main__":
    import sys

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
