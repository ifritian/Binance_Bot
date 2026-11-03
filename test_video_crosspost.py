from pathlib import Path

import bluesky_publisher as bs
import telegram_publisher as tg


class _Resp:
    def __init__(self, data, ok=True):
        self._d, self.ok, self.content, self.text = data, ok, b"x", str(data)

    def json(self):
        return self._d


def test_telegram_video_long_caption_split(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tg, "_post", lambda method, **kw: calls.append((method, kw["data"])) or {"message_id": 1})
    v = tmp_path / "v.mp4"
    v.write_bytes(b"0")
    tg.publish_video("line\n" * 400, v, None, 20.4)
    assert [c[0] for c in calls] == ["sendVideo", "sendMessage"]
    assert len(calls[0][1]["caption"]) <= 1024 and calls[0][1]["duration"] == 20


def test_bluesky_trim():
    t = "First line.\n" + "word " * 100
    assert len(bs._trim(t)) <= 295
    assert bs._trim("short") == "short"


def test_bluesky_video_flow(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(bs, "_create_session", lambda: {"accessJwt": "jwt", "did": "did:plc:x",
                        "didDoc": {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example.host"}]}})

    def fget(url, **kw):
        if url.endswith("getServiceAuth"):
            seen["aud"] = kw["params"]["aud"]
            return _Resp({"token": "svc"})
        return _Resp({"jobStatus": {"jobId": "j1", "state": "JOB_STATE_COMPLETED", "blob": {"ref": "blob1"}}})

    def fpost(url, **kw):
        if "uploadVideo" in url:
            return _Resp({"jobId": "j1"})
        seen["record"] = kw["json"]["record"]
        return _Resp({"uri": "at://x"})

    monkeypatch.setattr(bs.requests, "get", fget)
    monkeypatch.setattr(bs.requests, "post", fpost)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"0")
    bs.publish_video("Caption text", v)
    assert seen["aud"] == "did:web:pds.example.host"
    assert seen["record"]["embed"]["$type"] == "app.bsky.embed.video" and seen["record"]["embed"]["video"] == {"ref": "blob1"}
