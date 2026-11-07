import random

import mover_setups as ms
import trade_idea as ti


def _bars(n=60, up=True):
    return [(i, 100 + i * (0.5 if up else -0.5), 101 + i * (0.5 if up else -0.5), 99 + i * (0.5 if up else -0.5), 100 + i * (0.5 if up else -0.5), 1.0)
            for i in range(n)]


def test_fade_setup_sides():
    b = _bars()
    s = ms.build_setup("PEPE", 130.0, 12.0, b, side="short", mode="fade")
    assert s["side"] == "short" and s["stop"] > s["entry"] > s["target"] and s["mode"] == "fade"
    m = ms.build_setup("SOL", 130.0, 5.0, b)
    assert m["side"] == "long" and m["mode"] == "momentum" and m["target"] > m["entry"] > m["stop"]


def test_all_tones_have_levels_and_disclaimer():
    s = ms.build_setup("PEPE", 130.0, 12.0, _bars(), side="short", mode="fade")
    for tone in ti.TONES:
        for i in range(10):
            t = ti.build_text(s, tone, rsi={"rsi": 88.0, "kind": "high"} if i % 2 else None, rng=random.Random(i))
            assert "Entry:" in t and "Stop:" in t and "Target:" in t and "Not financial advice." in t and "http" not in t
            assert t.rstrip().split("\n")[-1].startswith("#PEPE")


def test_explosive_for_meme_and_rsi():
    rng = random.Random(0)
    picks = [ti.pick_tone("PEPE", None, "high", rng) for _ in range(300)]
    assert picks.count("explosive") > 120
    plain = [ti.pick_tone("SOL", None, None, rng) for _ in range(300)]
    assert plain.count("formal") > plain.count("explosive")
    again = [ti.pick_tone("SOL", "formal", None, random.Random(i)) for i in range(300)]
    assert again.count("formal") < plain.count("formal")
