import json
import sqlite3
import time

import pytest
from PIL import Image

import config
import peer
import theme


def test_theme_off_is_noop(monkeypatch):
    monkeypatch.setattr(config, "THEME", "")
    img = Image.new("RGB", (4, 4), (240, 185, 11))
    assert theme.apply(img).getpixel((0, 0)) == (240, 185, 11)


def test_theme_violet_recolors_gold_keeps_red_and_light_gold(monkeypatch):
    monkeypatch.setattr(config, "THEME", "violet")
    img = Image.new("RGB", (3, 1))
    img.putpixel((0, 0), (240, 185, 11))     # основное золото
    img.putpixel((1, 0), (246, 70, 93))      # красный
    img.putpixel((2, 0), (252, 213, 53))     # светлое золото
    out = theme.apply(img)
    r, g, b = out.getpixel((0, 0))
    assert b > g and r > g                    # фиолетовый
    assert all(abs(a - b) <= 4 for a, b in zip(out.getpixel((1, 0)), (246, 70, 93)))
    assert all(abs(a - b) <= 4 for a, b in zip(out.getpixel((2, 0)), (252, 213, 53)))


def _peer_db(tmp_path, log):
    p = tmp_path / "peer.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO state VALUES('square_post_log', ?)", (json.dumps(log),))
    con.commit()
    con.close()
    return p


def test_peer_clash_and_spacing(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr(config, "PEER_DB_PATH", _peer_db(tmp_path, [{"ts": now - 60, "fmt": "quick_setup", "head": "$NEAR just jumped"}]))
    assert peer.clashes("$NEAR entry") and not peer.clashes("$UNI entry")
    assert peer.seconds_since_last_post() < 120


def test_peer_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEER_DB_PATH", tmp_path / "none.db")
    assert peer.recent_heads() == "" and not peer.clashes("$NEAR")
    assert peer.seconds_since_last_post() > 10 ** 6


def test_profile_b_does_not_require_telegram(monkeypatch):
    monkeypatch.setattr(config, "PROFILE", "b")
    monkeypatch.setattr(config, "BINANCE_SQUARE_API_KEY", "k")
    monkeypatch.setattr(config, "GROQ_API_KEY", "g")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "FOLLOWUP_CHANNEL_USERNAME", "")
    assert config.validate_config() == []
    monkeypatch.setattr(config, "PROFILE", "")
    assert set(config.validate_config()) == {"TELEGRAM_BOT_TOKEN", "FOLLOWUP_CHANNEL_USERNAME"}


def test_peer_news_heads_only_for_profile_b(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr(config, "PEER_DB_PATH", _peer_db(tmp_path, [{"ts": now - 600, "fmt": "breaking_news", "head": "UK regulator plans tighter DeFi rules"},
                                                                 {"ts": now - 600, "fmt": "quick_setup", "head": "$NEAR"}]))
    monkeypatch.setattr(config, "PROFILE", "")
    assert peer.recent_post_heads(36) == []
    monkeypatch.setattr(config, "PROFILE", "b")
    assert peer.recent_post_heads(36, ("news_move", "breaking_news")) == ["UK regulator plans tighter DeFi rules"]


def test_post_skips_coin_already_posted_by_main(tmp_path, monkeypatch):
    import main
    now = time.time()
    monkeypatch.setattr(config, "PEER_DB_PATH", _peer_db(tmp_path, [{"ts": now - 60, "fmt": "quick_setup", "head": "$NEAR just jumped"}]))
    monkeypatch.setattr(config, "PROFILE", "b")
    called = []
    monkeypatch.setattr(main.binance_publisher, "publish_post", lambda *a, **k: called.append(1) or {"status": "ok"})
    assert main._post("quick_setup", "$NEAR long entry")["status"] == "duplicate_skipped"
    assert not called
    assert main._post("quick_setup", "$UNI long entry")["status"] == "ok"
