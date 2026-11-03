"""Озвучка обычных видео (рынок за 15 секунд, карта рынка, реплей): голос Kokoro + музыка + короткие субтитры внизу.

Границы сцен берутся из длительности фраз, поэтому слова и картинка совпадают. Если голос недоступен, видео собирается
с субтитрами и музыкой и теми же сценами по оценке длительности. Любая ошибка выше по стеку откатывается к тихой версии.
"""
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import ImageDraw

import news_video as nv
import video_post as vp

logger = logging.getLogger(__name__)
LEAD = vp.STING_S          # голос начинается после заставки
TAIL = 0.8


def _strip(img, words_state) -> None:
    """Одна строка субтитров в свободной полосе под контентом (y 1202-1258)."""
    words, active = words_state
    d = ImageDraw.Draw(img)
    f = vp._font(32)
    text = " ".join(words)
    wid = f.getlength(text)
    x = (vp.W - wid) / 2
    d.rounded_rectangle([x - 22, 1202, x + wid + 22, 1258], radius=14, fill=(8, 10, 13))
    for i, w in enumerate(words):
        d.text((x, 1209), w, font=f, fill=vp.GOLD2 if i == active else vp.WHITE)
        x += f.getlength(w + " ")


def _encode(frame_fn, tl, total, out: Path, voice, fps: int = vp.FPS):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден")
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    nv.write_wav(wav, nv.mix(voice, nv.music(total), LEAD))

    def with_subs(t):
        img = frame_fn(t)
        st = nv._subtitle_state(tl, t, chunk=4)
        if st and t > LEAD:
            _strip(img, st)
        return img

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{vp.W}x{vp.H}", "-r", str(fps), "-i", "-",
           "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
           "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(int(total * fps)):
            proc.stdin.write(with_subs(i / fps).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
        wav.unlink(missing_ok=True)
    return with_subs


def _prepare(segs: list, tts, min_total: float):
    for s in segs:
        s.setdefault("say", nv._say(s["text"]))
    engine = nv.ensure_tts() if tts == "auto" else tts
    voice, durs = nv.synth(segs, engine)
    tl = nv.timeline(segs, durs, LEAD)
    total = max(min_total, tl[-1]["end"] + TAIL)
    return voice, tl, total


def _name(t: str) -> str:
    return nv._say(nv._NAMES.get(t, t))


def _mv(p: float) -> str:
    return f"{'up' if p >= 0 else 'down'} {abs(p):.1f} percent"


def _disp(p: float) -> str:
    return f"{'up' if p >= 0 else 'down'} {abs(p):.1f}%"


# ---------- Рынок за 15 секунд ----------

def market_script(data: dict, fng: dict | None) -> list:
    maj = {t: p for t, _pr, p in data.get("majors", [])}
    segs = []
    if "BTC" in maj:
        txt = f"Crypto market check. Bitcoin is {_disp(maj['BTC'])} in 24 hours"
        txt += f", Ether {_disp(maj['ETH'])}." if "ETH" in maj else "."
        say = f"Crypto market check. Bitcoin is {_mv(maj['BTC'])} in 24 hours" + (f", Ether {_mv(maj['ETH'])}." if "ETH" in maj else ".")
    else:
        txt = say = "Crypto market check. Here is the last 24 hours."
    segs.append({"scene": "majors", "text": txt, "say": say})
    if data.get("gainers"):
        t, _p, c = data["gainers"][0]
        segs.append({"scene": "gainers", "text": f"Leading the board: {t}, {_disp(c)}.", "say": f"Leading the board: {_name(t)}, {_mv(c)}."})
    else:
        segs.append({"scene": "gainers", "text": "No big gainers right now.", "say": "No big gainers right now."})
    if data.get("losers"):
        t, _p, c = data["losers"][0]
        segs.append({"scene": "losers", "text": f"Lagging: {t}, {_disp(c)}.", "say": f"Lagging: {_name(t)}, {_mv(c)}."})
    else:
        segs.append({"scene": "losers", "text": "No big losers right now.", "say": "No big losers right now."})
    if fng and fng.get("value") is not None:
        segs.append({"scene": "mood", "text": f"Fear and Greed Index: {fng['value']}, {fng.get('label', '')}."})
    else:
        segs.append({"scene": "mood", "text": "That is the market right now."})
    return segs


def render_market(data: dict, fng: dict | None, out: Path, ts: datetime | None = None, tts="auto", fps: int = vp.FPS):
    """-> (mp4, poster, длительность, голос?)."""
    ts = ts or datetime.now(timezone.utc)
    voice, tl, total = _prepare(market_script(data, fng), tts, 15.0)
    names = ["majors", "gainers", "losers", "mood"]
    sc = {n: (0.0 if i == 0 else tl[i]["start"], tl[i]["end"] if i < 3 else total) for i, n in enumerate(names)}
    for i in range(1, 4):                                    # без щелей между сценами
        sc[names[i]] = (sc[names[i - 1]][1], sc[names[i]][1])
    frame_fn = lambda t: vp.frame(t, data, fng, ts, total, sc)
    out = Path(out)
    poster = out.with_suffix(".png")
    frame_fn(sc["gainers"][0] + 2.0).save(poster)
    _encode(frame_fn, tl, total, out, voice, fps)
    return out, poster, total, voice is not None


# ---------- Карта рынка ----------

def map_script(tiles: list) -> list:
    up = sum(1 for _s, p, _v in tiles if p > 0)
    best = max(tiles, key=lambda x: x[1])
    worst = min(tiles, key=lambda x: x[1])
    return [{"text": "Here is the whole market on one map."},
            {"text": f"{up} of the {len(tiles)} biggest coins are up.", "say": f"{up} of the {len(tiles)} biggest coins are up."},
            {"text": f"Strongest: {best[0]}, {_disp(best[1])}. Weakest: {worst[0]}, {_disp(worst[1])}.",
             "say": f"Strongest: {_name(best[0])}, {_mv(best[1])}. Weakest: {_name(worst[0])}, {_mv(worst[1])}."},
            {"text": "Bigger tile, more volume. Color shows the 24 hour move."}]


def render_map(tiles: list, out: Path, ts: datetime | None = None, tts="auto", fps: int = vp.FPS):
    ts = ts or datetime.now(timezone.utc)
    voice, tl, total = _prepare(map_script(tiles), tts, 12.0)
    frame_fn = lambda t: vp.map_frame(t, tiles, ts, total)
    out = Path(out)
    poster = out.with_suffix(".png")
    frame_fn(total - 0.5).save(poster)
    _encode(frame_fn, tl, total, out, voice, fps)
    return out, poster, total, voice is not None


# ---------- Реплей ----------

def replay_script(ticker: str, bars: list) -> list:
    chg = (bars[-1][4] / bars[0][1] - 1) * 100
    n = len(bars)
    return [{"text": f"Replay: how {nv._NAMES.get(ticker, ticker)} moved over the last {n} hours.",
             "say": f"Replay: how {_name(ticker)} moved over the last {n} hours."},
            {"text": f"Overall it finished {_disp(chg)}.", "say": f"Overall it finished {_mv(chg)}."},
            {"text": "That is what happened. Not a prediction."}]


def render_replay(ticker: str, bars: list, out: Path, ts: datetime | None = None, tts="auto", fps: int = vp.FPS):
    ts = ts or datetime.now(timezone.utc)
    voice, tl, total = _prepare(replay_script(ticker, bars), tts, 12.0)
    frame_fn = lambda t: vp.replay_frame(t, ticker, bars, ts, total)
    out = Path(out)
    poster = out.with_suffix(".png")
    frame_fn(total - 0.5).save(poster)
    _encode(frame_fn, tl, total, out, voice, fps)
    return out, poster, total, voice is not None
