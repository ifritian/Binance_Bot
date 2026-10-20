import random

import chart_generator
import hand_draw


def _candles(n=72):
    random.seed(3)
    p, out = 100.0, []
    for i in range(n):
        o = p; c = o * (1 + random.uniform(-.01, .012))
        out.append({"open_time": 1_700_000_000_000 + i * 3600_000, "open": o, "high": max(o, c) * 1.003,
                    "low": min(o, c) * .997, "close": c, "volume": random.uniform(500, 900) * (5 if i > 68 else 1)})
        p = c
    return out


def test_chart_with_sketch_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(chart_generator, "_CHARTS_DIR", tmp_path)
    cs = _candles()
    e = cs[-1]["close"]
    path = chart_generator.generate_trade_chart("TST", cs, entry=e, stop=e * .98, target=e * 1.04,
                                                suffix="_s", tf_label="1h", sketch_side="long")
    assert path and path.exists() and path.stat().st_size > 10_000


def test_font_is_bundled():
    assert hand_draw.FONT_PATH.exists()


def test_annotate_never_raises(monkeypatch):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    hand_draw.annotate_setup(fig, ax, None, [], 1.0, 0.9, 1.1, "long")   # пустые свечи - просто лог
    plt.close(fig)
