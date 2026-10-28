import level_check as lc, crowd, time
import config, queue_manager, main

def bars(lo, hi, n=30, t0=0):
    return [(t0 + i * 3600000, 1, hi, lo, 1.5, 1) for i in range(n)]

def test_compute_and_evaluate():
    lv = lc.compute(bars(10, 20))
    assert lv["hi"] == 20 and lv["lo"] == 10
    rec = {"ticker": "BTC", "hi": 20, "lo": 10, "ts": 0}
    assert lc.evaluate(rec, bars(10, 20)) == "held"
    assert lc.evaluate(rec, bars(10, 21)) == "above"
    assert lc.evaluate(rec, bars(9, 21)) == "both"

def test_text_has_one_cashtag_and_record():
    t = lc.build_text(2, "ETH", {"hi": 2, "lo": 1, "price": 1.5}, str, {"ticker": "BTC", "res": "above", "lo": 1, "hi": 2}, [{"res": "held"}])
    assert t.count("$") == 1 and "Level Check #2" in t and "broke above" in t

def test_crowd():
    assert crowd.outcome(2) == "Higher" and crowd.outcome(-3) == "Lower" and crowd.outcome(0.2) == "About the same"
    assert crowd.crowd_pick({"Higher": 1}) == (None, 1)
    assert crowd.crowd_pick({"Higher": 2, "Lower": 2}) == (None, 4)
    assert crowd.crowd_pick({"Higher": 3, "Lower": 1}) == ("Higher", 4)
    poll = {"options": [{"text": "Higher", "voter_count": 3}, {"text": "Lower", "voter_count": 0}]}
    assert crowd.tally(poll)["Higher"] == 3


def test_new_video_formats_render(tmp_path):
    import video_post as vp, shutil
    from datetime import datetime, timezone
    tiles = [("BTC", 1.0, 9e9), ("ETH", -1.0, 5e9), ("SOL", 3.0, 2e9), ("BNB", 0.1, 1e9), ("XRP", -2.0, 1e9), ("ZEC", 8.0, 5e8)]
    bars = [(i, 100, 103, 99, 100 + i * 0.5, 1) for i in range(24)]
    ts = datetime.now(timezone.utc)
    assert vp.map_frame(8, tiles, ts).size == (720, 1280)
    assert vp.replay_frame(8, "ZEC", bars, ts).size == (720, 1280)
    assert vp.map_caption(tiles).count("$") <= 3 and vp.replay_caption("ZEC", bars).count("$") == 1
    if shutil.which("ffmpeg"):
        mp4, poster = vp.render_replay("ZEC", bars, tmp_path / "r.mp4", duration=2, fps=5, ts=ts)
        assert mp4.stat().st_size > 1000 and poster.exists()


def test_brand_stamp_and_tag(tmp_path):
    import brand
    from PIL import Image
    p = tmp_path / "a.png"; Image.new("RGB", (1200, 750), (0, 0, 0)).save(p)
    out = brand.stamp(p, "hot_mover")
    assert out != p and Image.open(out).size[1] > 750
    assert brand.stamp(tmp_path / "missing.png", "x") == tmp_path / "missing.png"
    assert brand.tag_text("hi") == "hi #VektorDaily" and brand.tag_text("x #vektordaily") == "x #vektordaily"


def test_question_enrich_keeps_tags():
    import square_question as sq
    bars = [(i, 1, 2, 1, 1.5, 1) for i in range(24)]
    t = sq.enrich("$AVAX is down 2.5% in 24h. Dip?\n\n#AVAX", "AVAX", bars, str)
    assert "One word is enough: BUY" in t and t.endswith("#AVAX")


def test_receipts_page_renders():
    import site_builder as sb
    d = {"closed": [{"ticker": "PEPE", "entry_t": 1e12, "pnl": -11.0, "reason": "stop", "held": 3}], "open": {}, "listings": [],
         "snapshots": [], "posts": [], "levels": [{"ticker": "BTC", "ts": 1.7e9, "lo": 1.0, "hi": 2.0, "res": "held"}],
         "crowd": {"right": 1, "total": 2}, "followups": [{"ticker": "ZEC", "ts": 1.7e9, "price": 5.0, "done": True}]}
    html = sb.render_receipts(d, ls=[])
    assert "Receipts" in html and "PEPE" in html and "1/2" in html and "Stayed inside" in html


def test_futures_ledger():
    import futures_ledger as fl
    rec = {"side": "BUY", "original_quantity": 100.0, "quantity": 100.0, "entry_price": 1.0, "stop_price": 0.98, "take_profit_price": 1.05,
           "realized_pnl": -2.0, "close_reason": "неизвестно (оба условных ордера уже неактивны)", "closed_at": 5}
    assert abs(fl.implied_exit(rec) - 0.98) < 1e-9 and fl.reason(rec) == "Stop (derived)"
    s = fl.summary([rec, dict(rec, realized_pnl=3.0)], since_ts=0)
    assert s["n"] == 2 and s["wins"] == 1 and s["total"] == 1.0
    import site_builder as sb
    d = {"closed": [], "open": {}, "listings": [], "snapshots": [], "posts": [], "levels": [], "crowd": {}, "followups": [],
         "futures": [rec], "futures_since": 1}
    assert "Demo futures account" in sb.render_receipts(d, ls=[])


def test_rsi_detect_and_text():
    import rsi_alert as ra, random
    flat = [(i, 100, 101, 99, 100 + (i % 2), 1) for i in range(150)]
    assert ra.detect(flat) is None
    p, up = 100.0, []
    for i in range(150):
        p *= 1.012 if i > 120 else 1.0 + (0.002 if i % 2 else -0.002)
        up.append((i, p, p, p, p, 1))
    d = ra.detect(up)
    assert d and d["kind"] == "high" and d["rsi"] > 82
    down = [(i, 1, 1, 1, 200 - i, 1) for i in range(150)]
    assert ra.detect(down)["kind"] == "low"
    t = ra.build_text("WLD", d, 1.5, 12.0, str)
    assert t.count("$") == 1 and "RSI" in t
