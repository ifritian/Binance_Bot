import math
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import news_video as nv

NOW = 1_000_000.0


def _n(i, title, src, age_min=30):
    return {"id": i, "title": title, "source": src, "ts": NOW - age_min * 60}


def test_pick_story_needs_two_sources_and_major_topic():
    news = [_n("1", "SEC approves first spot Solana ETF", "CoinDesk"), _n("2", "SEC approves first Solana ETF in landmark decision", "The Block"),
            _n("3", "Analyst likes the weekend vibe", "Decrypt"), _n("4", "Exchange hacked for millions", "Decrypt")]
    s = nv.pick_story(news, [], NOW)
    assert s and set(s["sources"]) == {"CoinDesk", "The Block"} and s["ticker"] == "SOL"
    assert nv.pick_story(news, [s["title"]], NOW) is None       # уже делали
    assert nv.pick_story(news[3:], [], NOW) is None             # один источник


def test_script_and_spoken_text():
    story = {"title": "SEC approves Solana ETF", "sources": ["CoinDesk", "The Block"], "ticker": "SOL", "ts": 0}
    segs = nv.build_script(story, {"ticker": "SOL", "pct": 6.4, "btc_price": 73412.0, "btc_pct": -1.2, "fng": {"value": 34, "label": "Fear"}})
    assert [s["scene"] for s in segs] == ["story", "story", "chart", "context", "outro"]
    assert "S E C" in segs[0]["say"] and "E T F" in segs[0]["say"] and "$" not in segs[2]["say"]
    assert "financial advice" not in segs[-1]["text"].lower() and "Vektor" in segs[-1]["text"]
    assert "73.4 thousand dollars" in segs[3]["say"] and "down 1.2 percent" in segs[3]["say"]


def test_music_and_mix_levels():
    m = nv.music(6.0)
    assert len(m) == 6 * nv.SR and np.max(np.abs(m)) > 0.05
    out = nv.mix(None, m, 0.9)
    assert 0.5 < np.max(np.abs(out)) <= 0.9


def test_render_without_voice(tmp_path):
    story = {"title": "SEC approves Solana ETF", "sources": ["CoinDesk", "The Block"], "ticker": "SOL", "ts": 0}
    bars = [(i, 100, 0, 0, 100 + math.sin(i / 4) * 3, 0) for i in range(48)]
    ctx = {"ticker": "SOL", "pct": 3.0, "bars": bars, "btc_price": 70000.0, "btc_pct": 1.0, "fng": {"value": 50, "label": "Neutral"}}
    mp4, poster, total, voiced = nv.render(story, ctx, tmp_path / "n.mp4", [("BTC", 70000.0, 1.0), ("SOL", 180.0, 3.0)],
                                           ts=datetime(2026, 11, 3, tzinfo=timezone.utc), tts=None)
    assert mp4.exists() and mp4.stat().st_size > 50_000 and poster.exists() and not voiced and total > 10


def test_caption_no_links():
    story = {"title": "SEC approves Solana ETF", "sources": ["CoinDesk", "The Block"], "ticker": "SOL", "ts": 0}
    c = nv.build_caption(story, {"ticker": "SOL", "pct": 3.0, "btc_pct": 1.0}, random.Random(1))
    assert "http" not in c and "$SOL" in c and "#CryptoNews" in c
