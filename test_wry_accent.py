import random

import emoji_accent as ea
import wry


def test_wry_silent_on_big_moves():
    assert wry.build({"b24": -5.0, "loss_streak": 5}) is None
    assert wry.build({"b24": 4.5}) is None


def test_wry_situations_and_cashtag():
    for ctx in ({"b24": 0.2}, {"b24": -1, "loss_streak": 3}, {"b24": 2, "fng": {"value": 15, "label": "Extreme Fear"}},
                {"b24": 1, "level": {"held": 1, "total": 7, "fresh": True}}, {"b24": 2, "fng": {"value": 85, "label": "Extreme Greed"}}):
        for i in range(10):
            t = wry.build(ctx, rng=random.Random(i))
            assert t and "$BTC" in t and "http" not in t and "Not financial advice" in t and "{" not in t


def test_wry_avoids_recent_line():
    first = wry.build({"b24": 0.1}, rng=random.Random(0))
    for i in range(20):
        assert wry.build({"b24": 0.1}, [first.split("\n")[0]], random.Random(i)).split("\n")[0][:30] != first.split("\n")[0][:30]


def test_accent_rules():
    assert not ea.should_apply("video", [])
    assert not ea.should_apply("digest", [True], random.Random(0))
    assert ea.apply("digest", "👀 already", random.Random(0)) == "👀 already"
    assert ea.apply("news_move", "$SOL +6% today", random.Random(0))[0] in "".join(ea.UP)
    assert ea.apply("market_shock", "STOP. $BTC −5% down", random.Random(1)).split(" ")[0][0] in "".join(ea.ALERT)
    n = sum(ea.should_apply("digest", [], random.Random(i)) for i in range(200))
    assert 60 < n < 140
