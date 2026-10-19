import pytest

import config
import queue_manager
import site_builder as sb


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")


def _data(**kw):
    d = {"snapshots": [], "closed": [], "open": {}, "listings": [], "listings_waiting": [], "posts": []}
    d.update(kw)
    return d


def test_empty_state_renders_without_errors():
    out = sb.render(_data(), 1_792_300_000)
    assert "Market Radar" in out and "No idea has closed yet" in out and "A crypto desk that shows its work" in out


def test_losing_trades_are_listed_and_stats_are_honest():
    closed = [{"ticker": "AAA", "entry_t": 1_792_000_000_000, "pnl": -3.2, "reason": "stop", "held": 6},
              {"ticker": "BBB", "entry_t": 1_792_100_000_000, "pnl": 5.0, "reason": "time", "held": 30}]
    out = sb.render(_data(closed=closed), 1_792_300_000)
    assert "$AAA" in out and "−3.2%" in out and "+5.0%" in out
    st = sb.stats_ideas(closed)
    assert st["n"] == 2 and st["wins"] == 1 and abs(st["avg"] - 0.9) < 1e-9


def test_html_is_escaped_and_legacy_posts_hidden():
    posts = [{"ts": 1_792_000_000, "id": "1", "head": "<script>alert(1)</script> $BTC", "fmt": "digest"},
             {"ts": 1_792_000_001, "id": "2", "head": "Старый русский пост", "fmt": None}]
    out = sb.render(_data(posts=posts), 1_792_300_000)
    assert "<script>alert" not in out and "</script> $BTC" not in out and "&lt;/script&gt;" in out and "Старый русский" not in out


def test_chart_needs_two_snapshots_and_draws_three_lines():
    assert "appears after" in sb.basket_chart([])
    snaps = [{"timestamp": 1000 + i * 3600, "index_value": 100 - i, "btc_value": 100 - i / 2, "eth_value": 100 - i * 1.5} for i in range(4)]
    assert sb.basket_chart(snaps).count("<polyline") == 3


def test_build_writes_files(tmp_path):
    queue_manager._set("treasury_snapshots", [])
    p = sb.build(tmp_path / "site")
    assert p.exists() and (tmp_path / "site" / "data.json").exists() and (tmp_path / "site" / ".nojekyll").exists()


def test_wire_cleans_heads_and_chart_uses_defined_colors():
    assert sb._clean_head("🌙 Evening market snapshot (24h): $BTC 82 006 (+1.0%) $ETH 2 403 (+1.3%) 🚀 Top gainers: $WLD") .startswith("Evening")
    assert not sb._clean_head("x " * 80).endswith(" …") and len(sb._clean_head("word " * 40)) <= 80
    snaps = [{"timestamp": 1000 + i * 3600, "index_value": 100 - i, "btc_value": 100, "eth_value": 100} for i in range(3)]
    assert "--accent" not in sb.basket_chart(snaps) and "var(--cyan)" in sb.basket_chart(snaps)


def test_links_only_configured(monkeypatch):
    monkeypatch.setattr(config, "SQUARE_PROFILE_URL", "")
    monkeypatch.setattr(config, "TELEGRAM_PUBLISH_CHANNEL", "-100123")
    monkeypatch.setattr(config, "BLUESKY_HANDLE", "")
    assert sb.links() == []
    monkeypatch.setattr(config, "TELEGRAM_PUBLISH_CHANNEL", "@chan")
    assert sb.links() == [("Telegram", "https://t.me/chan")]
    out = sb.render(_data(), 1_792_300_000, ls=[])
    assert "Follow on" not in out


def test_empty_blocks_explain_what_will_appear():
    out = sb.render(_data(), 1_792_300_000, live={})
    assert "No listing has been followed through yet" in out and "could not be loaded" in out
