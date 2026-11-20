import math
import random

import smart_money_research as sm

H = 3600_000


def _bars(n, start=0):
    px, out = 100.0, []
    rng = random.Random(1)
    for i in range(n):
        px *= 1 + rng.uniform(-0.01, 0.01)
        out.append((start + i * H, px, px, px, px, 1.0))
    return out


def test_hourly_metrics_alignment_and_parse():
    rows = [{"create_time": "2026-01-01 00:00:00", "sum_open_interest_value": "10", "sum_toptrader_long_short_ratio": "1.5",
             "count_toptrader_long_short_ratio": "1.2", "count_long_short_ratio": "2.0", "sum_taker_long_short_vol_ratio": "0.9"},
            {"create_time": "2026-01-01 00:55:00", "sum_open_interest_value": "11", "sum_toptrader_long_short_ratio": "1.6",
             "count_toptrader_long_short_ratio": "1.2", "count_long_short_ratio": "2.0", "sum_taker_long_short_vol_ratio": "0.9"},
            {"create_time": "bad", "sum_open_interest_value": "x"}]
    hm = sm.hourly_metrics(rows)
    prev_hour = 1767225600000 - H                       # 2025-12-31 23:00 UTC: строка 00:00 относится к закрывшемуся часу
    assert hm[prev_hour][0] == 10 and hm[1767225600000][0] == 11 and len(hm) == 2


def test_zscore_uses_only_past():
    s = [1.0] * 200
    s[190] = 5.0
    assert sm.zscore(s, 100) is None                      # мало истории
    s2 = [math.sin(i) for i in range(400)]
    z1 = sm.zscore(s2, 300)
    s2[301:] = [99.0] * 99                                # будущее не влияет
    assert sm.zscore(s2, 300) == z1


def test_collect_and_cluster_stats_on_noise_find_nothing():
    rng = random.Random(7)
    data, feats = {}, {}
    for k in range(6):
        bars = _bars(1500, start=k)                       # разные ряды цен
        data[f"C{k}USDT"] = bars
        hm = {b[0]: (1.0, 1 + rng.uniform(-0.3, 0.3), 1 + rng.uniform(-0.3, 0.3), 1 + rng.uniform(-0.3, 0.3), 1 + rng.uniform(-0.3, 0.3)) for b in bars}
        feats[f"C{k}USDT"] = sm.build_features(bars, hm)
    trades, base = sm.collect(data, feats)
    rows = sm.evaluate(trades)
    assert rows                                           # конфигурации набрались
    good = [r for r in rows if r["m1"] > 0 and r["m2"] > 0 and r["t"] >= 3.5 and r["n"] >= 200 and r["net"] > 0]
    assert good == []                                     # на шуме жёсткий отбор ничего не пропускает
    text = sm.report(rows, data, base, 365)
    assert "прошедших жёсткий отбор" in text


def test_cluster_t_detects_real_edge():
    trades = [(d * 86400_000 + i, "X", 0.5, 0.5 + (i % 3) * 0.1) for d in range(60) for i in range(3)]
    days, mean, t = sm.cluster_t(trades)
    assert days == 60 and mean > 0.5 and t > 5
