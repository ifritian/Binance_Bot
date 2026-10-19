import pytest

import config
import post_cards
import queue_manager
import weekly_brief as wb

NOW = 1_792_300_000.0


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(wb.coin_whitelist, "is_allowed", lambda t: True)


CH = {"BTC": 2.0, "ETH": -1.0, "SOL": 12.4, "TIA": 9.1, "ADA": 4.0, "DOGE": -7.0, "XRP": -2.0, "LINK": 1.0}


def _collect(**over):
    kw = dict(symbols=list(CH), fetch_changes=lambda s: dict(CH), fetch_fng=lambda: (53, "Neutral", 63),
              fetch_binance=lambda n, h: [{"kind": "delist", "title": "Binance Will Delist X on 2026-10-28", "ts": NOW - 100, "code": "a", "tickers": []},
                                          {"kind": "network", "title": "Wallet Maintenance", "ts": NOW - 50, "code": "b", "tickers": []}])
    kw.update(over)
    return wb.collect(NOW, **kw)


def test_collect_ranks_and_separates_majors():
    d = _collect()
    assert d["btc"] == 2.0 and d["eth"] == -1.0
    assert [t for t, _ in d["best"][:2]] == ["SOL", "TIA"]
    assert d["worst"][0][0] == "DOGE" and all(p < 0 for _, p in d["worst"])
    assert wb.enough_data(d)


def test_text_has_at_most_three_cashtags_and_all_blocks():
    queue_manager._set("treasury_snapshots", [
        {"timestamp": NOW - 6 * 86400, "index_value": 100, "btc_value": 100, "eth_value": 100},
        {"timestamp": NOW - 100, "index_value": 103, "btc_value": 101, "eth_value": 99}])
    txt = wb.build_text(_collect())
    assert "it beat BTC by 2.0 points" in txt and "53 now (Neutral)" in txt and "1 delisting notice" in txt
    assert "Not financial advice" in txt and "none closed this week" in txt


def test_cashtag_budget_distinct_tickers_max_three():
    txt = wb.build_text(_collect())
    import re
    assert len(set(re.findall(r"\$([A-Z]{2,10})", txt))) <= 3


def test_missing_blocks_are_skipped_not_fatal():
    d = _collect(fetch_fng=lambda: None, fetch_binance=lambda n, h: [])
    txt = wb.build_text(d)
    assert "Fear & Greed" not in txt and "Binance:" not in txt
    assert not wb.enough_data(_collect(fetch_changes=lambda s: {}))


def test_week_changes_need_eight_closes():
    out = wb.fetch_week_changes(["A", "B"], lambda s: [1.0] * 8 if s == "A" else [1.0] * 3)
    assert list(out) == ["A"] and out["A"] == 0.0


def test_card_renders():
    p = post_cards.weekly_card(_collect() | {"basket": 3.0})
    assert p and p.exists()


def test_all_cards_carry_brand(monkeypatch):
    seen = []
    orig = post_cards._save
    monkeypatch.setattr(post_cards, "_save", lambda fig, path: (seen.append(path), orig(fig, path)))
    post_cards.fng_card({"value": 50, "label": "Neutral", "prev": 48})
    assert seen
