from datetime import datetime, timezone

import story_script as ss
import story_video as sv

TS = datetime(2026, 11, 3, 12, tzinfo=timezone.utc).timestamp()
SRC = ("[The Paypers, Nov 03 2026] EU entity may have lost USD 106 million in crypto theft, ZachXBT says. The attacker withdrew the funds "
       "as USDC and USDT. USDC was swapped for ether through WellWallet and Kyrrex, and Wagyu converted part to Monero. "
       "ZachXBT said the fund movements pointed away from North Korea's Lazarus Group. Hackers stole about $6.3 billion from Binance days earlier. "
       "The funds were taken from several exchanges on Sunday. The losses have not been independently confirmed. " * 2)
STORY = {"title": "EU entity may have lost USD 106 million in crypto theft, ZachXBT says", "sources": ["The Paypers", "Cointelegraph"], "ticker": None, "ts": TS,
         "items": [{"title": "EU entity may have lost USD 106 million in crypto theft, ZachXBT says", "source": "The Paypers", "ts": TS, "link": "https://x.test/a"},
                   {"title": "ZachXBT flags suspected theft", "source": "Cointelegraph", "ts": TS - 3600, "link": "https://x.test/b"}]}
PLAN = {
    "amount": {"text": "$106M+", "label": "reported loss"}, "regions": ["European Union"],
    "map_line": "Taken from several exchanges on Sunday.",
    "steps": [{"title": "Exchange accounts", "sub": "EU entity", "say": "Money left the accounts."},
              {"title": "Withdrawn as USDC", "sub": "and USDT", "say": "Withdrawn as USDC and USDT."},
              {"title": "Swapped to ether", "sub": "USDC to ether", "say": "Then USDC became ether."},
              {"title": "Wagyu to Monero", "sub": "part of funds", "say": "Part went into Monero via Wagyu."}],
    "statement": {"who": "ZachXBT", "label": "What ZachXBT says", "headline": "Fund movements pointed away from North Korea's Lazarus Group",
                  "say": "ZachXBT says it points away from Lazarus Group.", "note": "Attacker unidentified"},
    "compare": {"a": {"label": "Binance hack", "sub": "days earlier", "value": 1, "text": "$6.3B"},
                "b": {"label": "This theft", "sub": "reported", "value": 1, "text": "$106M+"},
                "say_a": "Binance lost about $6.3 billion days earlier.", "say_b": "This one is far smaller."},
    "key_phrase": "USD 106 million", "intro": "A European entity may have lost over $106 million, says ZachXBT.", "confirmed": False}


def test_supported_blocks_invented_facts():
    assert ss.supported("Binance lost about $6.3 billion.", SRC)
    assert not ss.supported("Binance lost about $9 billion.", SRC)             # число не из источника
    assert not ss.supported("Funds went to Kraken.", SRC)                      # имя не из источника
    assert not ss.supported("", SRC)


def test_assemble_full_plan_and_value_scale():
    out = ss.assemble(STORY, PLAN, SRC, "Vektor Daily")
    kinds = [s["scene"] for s in out["scenes"]]
    assert kinds[0] == "clip" and "map" in kinds and kinds.count("route") == 3 and kinds.count("compare") == 2 and kinds[-1] == "outro"
    cmp = next(s for s in out["scenes"] if s["scene"] == "compare")
    assert cmp["items"][0]["value"] == 6.3e9 and cmp["items"][1]["value"] == 106e6   # шкала считается из текста, не из ответа нейросети
    assert out["scenes"][0]["tag"] == "UNCONFIRMED" and out["scenes"][1]["tag"] == "UNCONFIRMED"


def test_hallucinated_scenes_are_dropped_and_few_scenes_means_fallback():
    bad = dict(PLAN, amount={"text": "$999M", "label": "loss"}, steps=[{"title": "Kraken", "sub": "x", "say": "Sent to Kraken."}] * 3, compare=None, statement=None)
    assert ss.assemble(STORY, bad, SRC, "Vektor Daily") is None                # осталась одна сцена - обычное видео-новость
    assert ss.assemble(STORY, None, SRC, "Vektor Daily") is None


def test_regions_cam_and_amount_value():
    assert "Germany" in ss.resolve_regions(["European Union"], SRC) and ss.resolve_regions(["Atlantis"], SRC) == []
    cam, pulse = ss.cam_for(["Japan"])
    assert 4 <= cam[2] <= 34 and pulse is not None
    assert ss.amount_value("$6.3B") == 6.3e9 and ss.amount_value("106 million") == 106e6 and ss.amount_value("n/a") is None


def test_parse_json_tolerates_fences():
    assert ss.parse_json('```json\n{"a": 1}\n```') == {"a": 1} and ss.parse_json("nope") is None


def test_build_uses_injected_fetch_and_llm_and_frames_render():
    out = ss.build(STORY, "Vektor Daily", fetch=lambda link: SRC, llm=lambda src: PLAN)
    assert out and len(out["scenes"]) >= 8
    segs = sv.build_script(out)
    from news_video import estimate_duration, timeline
    tl = timeline(segs, [estimate_duration(s["text"]) for s in segs])
    for seg in tl[::2]:
        im = sv.frame(seg["start"] + 1.0, tl, datetime(2026, 11, 3, tzinfo=timezone.utc), tl[-1]["end"] + 1)
        assert im.size == (720, 1280)


def test_build_story_video_wiring(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main.config, "STORY_VIDEO_ENABLED", False)
    assert main._build_story_video(STORY) is None
    monkeypatch.setattr(main.config, "STORY_VIDEO_ENABLED", True)
    monkeypatch.setattr(ss, "build", lambda story, brand: None)
    assert main._build_story_video(STORY) is None                              # мало проверенных сцен -> обычное видео-новость
    monkeypatch.setattr(ss, "build", lambda story, brand: {"scenes": []})
    monkeypatch.setattr(sv, "render", lambda plan, out, ts=None: (tmp_path / "a.mp4", tmp_path / "a.png", 33.0, True))
    mp4, poster, total, voiced, caption = main._build_story_video(STORY)
    assert total == 33.0 and voiced and "ZachXBT" in caption
    monkeypatch.setattr(sv, "render", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert main._build_story_video(STORY) is None                              # сбой сборки -> запасной вариант
