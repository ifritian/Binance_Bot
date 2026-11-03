import math
from datetime import datetime, timezone

import video_voice as vv

TS = datetime(2026, 11, 3, tzinfo=timezone.utc)
DATA = {"majors": [("BTC", 70000.0, 2.0), ("ETH", 2200.0, 1.0)], "gainers": [("SOL", 180.0, 6.0)], "losers": [("PEPE", 0.000005, -4.0)]}
FNG = {"value": 40, "label": "Fear"}
TILES = [(s, p, v) for s, p, v in [("BTC", 2.0, 9e9), ("ETH", 1.0, 5e9), ("SOL", 6.0, 3e9), ("XRP", -2.0, 1e9), ("DOGE", 3.0, 9e8), ("ADA", 1.0, 8e8), ("TRX", -1.0, 6e8)]]
BARS = [(i, 100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + math.sin(i / 3) + i * 0.1, 1.0) for i in range(24)]


def test_scripts_have_no_disclaimer_and_speakable():
    for segs in (vv.market_script(DATA, FNG), vv.map_script(TILES), vv.replay_script("SOL", BARS)):
        for s in segs:
            assert "financial advice" not in s["text"].lower() and "$" not in s.get("say", "")
    seg = vv.market_script(DATA, FNG)
    assert [s["scene"] for s in seg] == ["majors", "gainers", "losers", "mood"]
    assert "Solana" in seg[1]["say"] and "up 6.0 percent" in seg[1]["say"]


def test_render_all_without_voice(tmp_path):
    for name, fn in (("m", lambda p: vv.render_market(DATA, FNG, p, ts=TS, tts=None)),
                     ("p", lambda p: vv.render_map(TILES, p, ts=TS, tts=None)),
                     ("r", lambda p: vv.render_replay("SOL", BARS, p, ts=TS, tts=None))):
        mp4, poster, total, voiced = fn(tmp_path / f"{name}.mp4")
        assert mp4.exists() and mp4.stat().st_size > 30_000 and poster.exists() and not voiced and total >= 12.0
