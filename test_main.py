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


def test_try_publish_hot_take_skips_when_bluesky_not_configured(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
    calls = []
    monkeypatch.setattr(main.hot_take_generator, "generate_hot_take", lambda theme: calls.append(theme))

    main.try_publish_hot_take()

    # Не должны даже пытаться сгенерировать текст - незачем тратить LLM-вызов,
    # если публиковать всё равно некуда.
    assert calls == []


def test_try_publish_hot_take_respects_interval(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda post_type: 10)
    monkeypatch.setattr(main.queue_manager, "get_jitter_seconds", lambda post_type: 0)
    calls = []
    monkeypatch.setattr(main.hot_take_generator, "generate_hot_take", lambda theme: calls.append(theme))

    main.try_publish_hot_take()

    assert calls == []


def test_try_publish_hot_take_publishes_only_to_bluesky(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda post_type: 10 ** 9)
    monkeypatch.setattr(main.queue_manager, "get_jitter_seconds", lambda post_type: 0)
    monkeypatch.setattr(main.queue_manager, "should_retry_now", lambda post_type: True)
    monkeypatch.setattr(main.queue_manager, "get_last_hot_take_theme", lambda: None)
    monkeypatch.setattr(main.hot_take_generator, "pick_theme", lambda last: "BTC")
    monkeypatch.setattr(main.hot_take_generator, "generate_hot_take", lambda theme: ("текст хот-тейка", {5.5}))
    monkeypatch.setattr(main.hot_take_generator, "validate_hot_take", lambda text, nums: (True, ""))

    binance_calls = []
    telegram_calls = []
    bluesky_calls = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda *a, **k: binance_calls.append(1))
    monkeypatch.setattr(main.telegram_publisher, "publish_post", lambda *a, **k: telegram_calls.append(1))
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda *a, **k: bluesky_calls.append(1))

    main.try_publish_hot_take()

    assert bluesky_calls == [1]
    assert binance_calls == []
    assert telegram_calls == []


def test_try_publish_mini_lesson_skips_when_bluesky_not_configured(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
    calls = []
    monkeypatch.setattr(main.mini_lesson_generator, "generate_mini_lesson", lambda topic: calls.append(topic))

    main.try_publish_mini_lesson()

    assert calls == []


def test_try_publish_mini_lesson_publishes_only_to_bluesky(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(main.queue_manager, "seconds_since_last_post", lambda post_type: 10 ** 9)
    monkeypatch.setattr(main.queue_manager, "get_jitter_seconds", lambda post_type: 0)
    monkeypatch.setattr(main.queue_manager, "should_retry_now", lambda post_type: True)
    monkeypatch.setattr(main.queue_manager, "get_last_mini_lesson_topic", lambda: None)
    monkeypatch.setattr(main.mini_lesson_generator, "pick_topic", lambda last: "rsi")
    monkeypatch.setattr(main.mini_lesson_generator, "generate_mini_lesson", lambda topic: "текст мини-урока")
    monkeypatch.setattr(main.mini_lesson_generator, "validate_mini_lesson", lambda text: (True, ""))

    binance_calls = []
    bluesky_calls = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda *a, **k: binance_calls.append(1))
    monkeypatch.setattr(main.bluesky_publisher, "publish_post", lambda *a, **k: bluesky_calls.append(1))

    main.try_publish_mini_lesson()

    assert bluesky_calls == [1]
    assert binance_calls == []


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
