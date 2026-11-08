import random
from pathlib import Path

import volume_alert as va


def _bars(spike_x=1.0, move=0.0, n=192, drought=False):
    out, p = [], 1.0
    r = random.Random(1)
    for i in range(n):
        o = p
        p *= 1 + r.gauss(0, 0.002)
        v = 1000 * (1 + r.random())
        if i >= n - 4:
            v *= spike_x
            p *= 1 + move / 400
        if drought and i >= n - 12:
            v *= 0.05
        out.append((i * 900000, o, max(o, p), min(o, p), p, v))
    return out


def test_detect_meme_vs_major_thresholds():
    b = _bars(spike_x=5.0, move=3.0)                       # 5x: для мема достаточно (4x), для крупной монеты нет (6x)
    assert va.detect("PEPE", b)["kind"] == "spike"
    assert va.detect("LINK", b) is None


def test_detect_needs_price_move_and_history():
    assert va.detect("PEPE", _bars(spike_x=10.0, move=0.0)) is None
    assert va.detect("PEPE", _bars(n=50)) is None


def test_drought_only_for_majors():
    b = _bars(drought=True)
    assert va.detect("BTC", b)["kind"] == "drought"
    assert va.detect("PEPE", b) is None


def test_text_and_chart(tmp_path):
    b = _bars(spike_x=12.0, move=4.0)
    d = va.detect("PEPE", b)
    for i in range(12):
        t = va.build_text("PEPE", d, 0.00001234, 22.0, lambda x: f"{x:.8f}", rng=random.Random(i), levels=["Entry: 1"])
        assert "Not financial advice." in t and "http" not in t and t.rstrip().split("\n")[-1].startswith("#PEPE") and "Entry: 1" in t
    assert va.chart("PEPE", b, d, Path(tmp_path) / "c.png").exists()
    dr = va.detect("BTC", _bars(drought=True))
    assert "BTC" in va.build_text("BTC", dr, 70000.0, 0.5, lambda x: f"{x:.0f}", rng=random.Random(0))
