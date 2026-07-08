#!/usr/bin/env python3
"""
Тесты кросспостинга в Bluesky:
- post_format.build_bluesky_post: хэштег + ссылки + обрезка под лимит
  300 символов + список link_facets, ссылки/тег никогда не обрезаются
  первыми.
- bluesky_publisher: is_configured(), сессия -> (опционально картинка) ->
  публикация, байтовые facets, проброс ошибок наружу без исключений
  сверх BlueskyPublishError. requests.post замокан - реальных запросов
  не идёт.
"""
import types

import config
import post_format
import bluesky_publisher


class _FakeResponse:
    def __init__(self, json_data, ok=True):
        self._json = json_data
        self.ok = ok

    def json(self):
        return self._json


def test_build_bluesky_post_includes_hashtag_and_links(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PUBLISH_CHANNEL", "@my_channel")
    text = "Короткий хук.\n\nВход: 1-2\nСтоп: 0.9\nТейк: 3\n\nИнформационный пост, не финансовая рекомендация."

    result, facets = post_format.build_bluesky_post(text, ticker="BTC")

    assert "#BTC" in result
    assert post_format.REFERRAL_LINK in result
    assert "https://t.me/my_channel" in result
    assert len(result) <= post_format.BLUESKY_CHAR_LIMIT
    assert (post_format.REFERRAL_LINK, post_format.REFERRAL_LINK) in facets
    assert ("https://t.me/my_channel", "https://t.me/my_channel") in facets


def test_build_bluesky_post_without_telegram_channel_still_has_binance_link(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PUBLISH_CHANNEL", "")
    result, facets = post_format.build_bluesky_post("Хук без канала.", ticker=None)

    assert post_format.REFERRAL_LINK in result
    assert "t.me" not in result
    assert len(facets) == 1


def test_build_bluesky_post_truncates_long_body_but_keeps_links(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_PUBLISH_CHANNEL", "@my_channel")
    long_text = "А" * 2000

    result, facets = post_format.build_bluesky_post(long_text, ticker="DOGE")

    assert len(result) <= post_format.BLUESKY_CHAR_LIMIT
    assert post_format.REFERRAL_LINK in result
    assert "https://t.me/my_channel" in result
    assert "#DOGE" in result
    assert result.count("…") >= 1


def test_is_configured_false_without_credentials(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "")
    assert bluesky_publisher.is_configured() is False


def test_is_configured_true_with_both_values(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")
    assert bluesky_publisher.is_configured() is True


def test_byte_facets_finds_substring_offsets():
    text = "Хук про $BTC.\n\nБинанс: https://www.binance.com/register?ref=X"
    facets = bluesky_publisher._byte_facets(
        text, [("https://www.binance.com/register?ref=X", "https://www.binance.com/register?ref=X")]
    )
    assert len(facets) == 1
    start = facets[0]["index"]["byteStart"]
    end = facets[0]["index"]["byteEnd"]
    recovered = text.encode("utf-8")[start:end].decode("utf-8")
    assert recovered == "https://www.binance.com/register?ref=X"


def test_byte_facets_skips_missing_substring():
    facets = bluesky_publisher._byte_facets("нет ссылки тут", [("https://example.com", "https://example.com")])
    assert facets == []


def test_publish_post_text_only_calls_session_then_create_record(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")

    calls = []

    def _fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/com.atproto.server.createSession"):
            return _FakeResponse({"accessJwt": "jwt-1", "did": "did:plc:abc"})
        if url.endswith("/com.atproto.repo.createRecord"):
            return _FakeResponse({"uri": "at://did:plc:abc/app.bsky.feed.post/1"})
        raise AssertionError(f"неожиданный вызов: {url}")

    monkeypatch.setattr(bluesky_publisher.requests, "post", _fake_post)

    result = bluesky_publisher.publish_post("текст без картинки")

    assert result["uri"].startswith("at://")
    assert len(calls) == 2
    assert calls[0][0].endswith("createSession")
    assert calls[1][0].endswith("createRecord")
    assert calls[1][1]["headers"]["Authorization"] == "Bearer jwt-1"


def test_publish_post_with_image_uploads_blob_first(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "app-pass")

    calls = []

    def _fake_post(url, **kwargs):
        calls.append(url)
        if url.endswith("/com.atproto.server.createSession"):
            return _FakeResponse({"accessJwt": "jwt-1", "did": "did:plc:abc"})
        if url.endswith("/com.atproto.repo.uploadBlob"):
            return _FakeResponse({"blob": {"ref": "fake-blob-ref"}})
        if url.endswith("/com.atproto.repo.createRecord"):
            return _FakeResponse({"uri": "at://did:plc:abc/app.bsky.feed.post/2"})
        raise AssertionError(f"неожиданный вызов: {url}")

    monkeypatch.setattr(bluesky_publisher.requests, "post", _fake_post)

    result = bluesky_publisher.publish_post("текст с картинкой", image_bytes=b"\x89PNG...", image_content_type="image/png")

    assert result["uri"].endswith("/2")
    assert any(u.endswith("uploadBlob") for u in calls)


def test_publish_post_raises_on_login_error(monkeypatch):
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "alexei.bsky.social")
    monkeypatch.setattr(config, "BLUESKY_APP_PASSWORD", "wrong-pass")

    def _fake_post(url, **kwargs):
        return _FakeResponse({"message": "Invalid identifier or password"}, ok=False)

    monkeypatch.setattr(bluesky_publisher.requests, "post", _fake_post)

    try:
        bluesky_publisher.publish_post("текст")
        raise AssertionError("ожидалось BlueskyPublishError")
    except bluesky_publisher.BlueskyPublishError:
        pass


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
        needs_mp = fn.__code__.co_argcount > 0
        mp = _MiniMonkeypatch()
        try:
            if needs_mp:
                fn(mp)
            else:
                fn()
            print(f"OK   {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        finally:
            mp.undo()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
