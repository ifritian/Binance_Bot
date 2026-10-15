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
