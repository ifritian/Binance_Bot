import pytest
import binance_publisher, config, queue_manager


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))


def test_publish_logs(monkeypatch):
    monkeypatch.setattr(binance_publisher, "_send_content", lambda body: {"id": "123", "status": "ok"})
    binance_publisher.publish_post("Привет $BTC\n\nтекст")
    log = queue_manager._get("square_post_log")
    assert log[-1]["id"] == "123" and log[-1]["head"].startswith("Привет $BTC")


def test_log_failure_does_not_break(monkeypatch):
    monkeypatch.setattr(binance_publisher, "_send_content", lambda body: {})
    monkeypatch.setattr(queue_manager, "_set", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert binance_publisher.publish_post("t")["image_urls"] == []


def test_limit_cashtags_keeps_first_n():
    import binance_publisher as bp
    t = "$BTC 81 616\n$TIA +7%\n$SOL 3%\n$ADA 2%\n$XRP 1%\n#TIA $5 price"
    out = bp.limit_cashtags(t, 3)
    assert out.startswith("$BTC") and "$TIA" in out and "$SOL" in out
    assert "$ADA" not in out and "ADA 2%" in out and "XRP 1%" in out and "$5" in out
    assert bp.limit_cashtags("$BTC only", 3) == "$BTC only"


def test_publish_retries_with_fewer_cashtags(monkeypatch):
    import binance_publisher as bp
    sent = []

    def fake_send(body):
        sent.append(body["bodyTextOnly"])
        if len(sent) == 1:
            raise bp.PublishError("Binance Square вернул ошибку: 220095 Coin pair count exceeds the allowed limit")
        return {"id": "1"}

    monkeypatch.setattr(bp, "_send_content", fake_send)
    res = bp.publish_post("$BTC $ETH $SOL $ADA $XRP $DOGE")
    assert res["id"] == "1" and len(sent) == 2
    assert sent[1].count("$") == 3
