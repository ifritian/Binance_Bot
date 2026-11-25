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
