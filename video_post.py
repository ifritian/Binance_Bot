"""Короткое видео "Рынок за 15 секунд" для Binance Square: данные Binance -> кадры (PIL) -> mp4 (ffmpeg).

Видео без звука (тихая дорожка добавляется, чтобы формат был стандартным), вертикальное 720x1280, 15 секунд:
BTC/ETH за 24ч, лидеры и аутсайдеры (полосы растут), индекс страха и жадности. Всё считается из реальных данных,
ничего не обещает. Публикация - binance_publisher.publish_video.
"""
import math
import random
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 720, 1280, 15
BG, PANEL, GRID = (11, 14, 17), (18, 23, 29), (30, 35, 41)
GOLD, GOLD2, RED, WHITE, MUTED = (240, 185, 11), (252, 213, 53), (246, 70, 93), (234, 236, 239), (132, 142, 156)
_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def _font(size: int, bold: bool = True):
    return ImageFont.truetype(str(_FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _pct(p: float) -> str:
    return f"{p:+.1f}%".replace("-", "−")


def _price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    return f"{p:,.2f}" if p >= 1 else __import__("numfmt").small_price(p)


def _text(d, xy, s, font, fill, anchor="la", alpha=1.0):
    if alpha < 1.0:
        fill = tuple(int(BG[i] + (fill[i] - BG[i]) * alpha) for i in range(3))
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _header(d, ts: datetime, a=1.0):
    _text(d, (48, 60), __import__("config").PROJECT_NAME.upper(), _font(30), GOLD, alpha=a)
    _text(d, (W - 48, 66), ts.strftime("%b %d, %H:%M UTC"), _font(24, False), MUTED, "ra", a)
    d.line([(48, 112), (W - 48, 112)], fill=GRID, width=2)


def _progress(d, t: float, duration: float):
    d.rectangle([0, H - 8, W, H], fill=GRID)
    d.rectangle([0, H - 8, int(W * min(t / duration, 1.0)), H], fill=GOLD)


STING_S = 1.2


def _sting(img: Image.Image, t: float) -> Image.Image:
    """Заставка ~1.2 с: имя канала на тёмном фоне, быстро растворяется в первую сцену."""
    if t >= STING_S:
        return img
    a = 1.0 if t < 0.7 else max(0.0, (STING_S - t) / (STING_S - 0.7))
    cover = Image.new("RGB", img.size, BG)
    d = ImageDraw.Draw(cover)
    import config
    k = 0.92 + 0.08 * ease(t / 0.5)
    _text(d, (W // 2, H // 2 - 20), config.PROJECT_NAME.upper(), _font(int(58 * k)), GOLD, "mm", min(1.0, t / 0.25))
    d.rectangle([W // 2 - int(150 * ease(t / 0.6)), H // 2 + 40, W // 2 + int(150 * ease(t / 0.6)), H // 2 + 44], fill=GOLD)
    return Image.blend(img, cover, a)


def scenes(duration: float = 15.0) -> dict:
    k = duration / 15.0
    return {"majors": (0, 4.0 * k), "gainers": (4.0 * k, 8.5 * k), "losers": (8.5 * k, 12.0 * k), "mood": (12.0 * k, duration)}


def _frame(t: float, data: dict, fng: dict | None, ts: datetime, duration: float = 15.0) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    sc = scenes(duration)
    _header(d, ts)
    name = next(n for n, (a, b) in sc.items() if t < b) if t < duration else "mood"
    a0, b0 = sc[name]
    lt = t - a0                                   # время внутри сцены
    fade = ease(min(lt / 0.4, 1.0)) * (1.0 if b0 - t > 0.3 else max((b0 - t) / 0.3, 0.0))
    if name == "majors":
        btc = next((p for tk, _pr, p in (data.get("majors") or []) if tk == "BTC"), None)
        if btc is not None:
            _text(d, (48, 190), f"Bitcoin is {'up' if btc >= 0 else 'down'}", _font(46), WHITE, alpha=fade)
            _text(d, (48, 250), f"{abs(btc):.1f}% in 24 hours", _font(46), GOLD2 if btc >= 0 else RED, alpha=fade)
        else:
            _text(d, (48, 190), "Crypto market,", _font(46), WHITE, alpha=fade)
            _text(d, (48, 250), "last 24 hours", _font(46), GOLD2, alpha=fade)
        y = 400
        for i, (tk, price, pct) in enumerate((data.get("majors") or [])[:2]):
            p = ease((lt - 0.3 - i * 0.5) / 1.2)
            col = GOLD2 if pct >= 0 else RED
            d.rounded_rectangle([48, y, W - 48, y + 230], 24, fill=PANEL)
            _text(d, (84, y + 28), f"${tk}", _font(54), WHITE, alpha=p)
            cur = price * (0.985 + 0.015 * p)
            _text(d, (84, y + 108), _price(cur), _font(60), WHITE, alpha=p)
            _text(d, (W - 84, y + 40), _pct(pct * p), _font(62), col, "ra", p)
            y += 270
    elif name in ("gainers", "losers"):
        up = name == "gainers"
        items = (data.get("gainers" if up else "losers") or [])[:5]
        _text(d, (48, 190), "Leading" if up else "Lagging", _font(56), GOLD2 if up else RED, alpha=fade)
        _text(d, (48, 262), "biggest moves among liquid coins" if up else "weakest among liquid coins", _font(26, False), MUTED, alpha=fade)
        top = max([abs(c) for _, _, c in items] + [1.0])
        y = 360
        for i, (tk, price, pct) in enumerate(items):
            p = ease((lt - 0.3 - i * 0.25) / 1.0)
            _text(d, (48, y), f"${tk}", _font(40), WHITE, alpha=p)
            bw = int((W - 96 - 20) * abs(pct) / top * p)
            d.rounded_rectangle([48, y + 62, 48 + max(bw, 6), y + 100], 10, fill=GOLD if up else RED)
            _text(d, (W - 48, y + 4), _pct(pct * p), _font(40), GOLD2 if up else RED, "ra", p)
            y += 150
    else:
        f = fng or {}
        _text(d, (48, 190), "Market mood", _font(56), WHITE, alpha=fade)
        if f.get("value") is not None:
            v = f["value"] * ease(lt / 1.4)
            _text(d, (W // 2, 520), f"{int(round(v))}", _font(190), GOLD2, "ma", fade)
            _text(d, (W // 2, 740), str(f.get("label", "")), _font(44), WHITE, "ma", fade)
            x0, x1, y = 70, W - 70, 860
            for i in range(x1 - x0):          # градиент страх -> жадность
                r = i / (x1 - x0)
                c = tuple(int(a + (b - a) * r) for a, b in zip((246, 70, 93), (252, 213, 53)))
                d.line([(x0 + i, y), (x0 + i, y + 22)], fill=c)
            mx = x0 + (x1 - x0) * v / 100
            d.rounded_rectangle([mx - 5, y - 14, mx + 5, y + 36], 4, fill=WHITE)
            _text(d, (x0, y + 56), "Fear", _font(26, False), MUTED)
            _text(d, (x1, y + 56), "Greed", _font(26, False), MUTED, "ra")
        _text(d, (W // 2, 1100), "Not financial advice", _font(28, False), MUTED, "ma", fade)
        _text(d, (W // 2, 1160), "Full numbers in the post", _font(28, False), GOLD, "ma", fade)
    _progress(d, t, duration)
    return img


def _encode(frame_fn, poster_t: float, out: Path, duration: float, fps: int) -> tuple[Path, Path]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    poster = out.with_suffix(".png")
    frame_fn(poster_t).save(poster)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-crf", "24", "-c:a", "aac", "-b:a", "32k", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(int(duration * fps)):
            proc.stdin.write(frame_fn(i / fps).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return out, poster


def _tile_color(pct: float) -> tuple[int, int, int]:
    t = min(abs(pct) / 8.0, 1.0)
    lo, hi = ((58, 47, 8), (252, 213, 53)) if pct >= 0 else ((59, 18, 24), (246, 70, 93))
    return tuple(round(lo[i] + (hi[i] - lo[i]) * t) for i in range(3))


def _map_frame(t: float, tiles: list, ts: datetime, duration: float = 12.0) -> Image.Image:
    """Анимированная карта рынка: плитки (размер - объём, цвет - 24ч) по очереди вырастают."""
    import site_heatmap
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _header(d, ts)
    _text(d, (48, 160), "Market map, 24h", _font(44), WHITE)
    _text(d, (48, 222), "size = volume, color = change", _font(24, False), MUTED)
    items = [(s, max(v, 1.0) ** 0.6) for s, _p, v in tiles]
    lay = site_heatmap.squarify(items, 48, 290, W - 96, 860)
    pct = {s: p for s, p, _v in tiles}
    for i, (s, x, y, w, h) in enumerate(lay):
        p = ease((t - 0.3 - i * 0.18) / 0.9)
        if p <= 0:
            continue
        cx, cy = x + w / 2, y + h / 2
        w2, h2 = (w - 4) * p, (h - 4) * p
        col = _tile_color(pct[s])
        d.rectangle([cx - w2 / 2, cy - h2 / 2, cx + w2 / 2, cy + h2 / 2], fill=col)
        if p > 0.9 and w > 90 and h > 70:
            dark = sum(col) > 330
            tc = BG if dark else WHITE
            _text(d, (cx, cy - 4), s, _font(max(18, min(40, int(w / 4.2)))), tc, "ms")
            _text(d, (cx, cy + 6), _pct(pct[s]), _font(max(15, min(28, int(w / 6)))), tc, "ma")
    _progress(d, t, duration)
    return img


def render_map(tiles: list, out: Path, duration: float = 12.0, fps: int = FPS, ts: datetime | None = None):
    ts = ts or datetime.now(timezone.utc)
    return _encode(lambda t: map_frame(t, tiles, ts, duration), duration - 0.5, out, duration, fps)


def _replay_frame(t: float, ticker: str, bars: list, ts: datetime, duration: float = 12.0) -> Image.Image:
    """Свечи 1ч "проигрываются" слева направо; цена и изменение считаются вживую."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _header(d, ts)
    n = len(bars)
    shown = max(1, min(n, int(1 + (n - 1) * ease(t / (duration * 0.8)))))
    cur = bars[shown - 1][4]
    first = bars[0][1]
    chg = (cur / first - 1) * 100
    _text(d, (48, 160), f"${ticker}", _font(64), WHITE)
    _text(d, (48, 245), _price(cur), _font(58), GOLD2 if chg >= 0 else RED)
    _text(d, (W - 48, 262), _pct(chg), _font(48), GOLD2 if chg >= 0 else RED, "ra")
    _text(d, (48, 340), f"replay of the last {n} hours", _font(24, False), MUTED)
    hi, lo = max(b[2] for b in bars), min(b[3] for b in bars)
    x0, x1, y0, y1 = 48, W - 48, 420, 1100
    sy = lambda p: y1 - (p - lo) / max(hi - lo, 1e-12) * (y1 - y0)
    for k in range(5):
        d.line([(x0, y0 + k * (y1 - y0) / 4), (x1, y0 + k * (y1 - y0) / 4)], fill=GRID, width=1)
    cw = (x1 - x0) / n
    for i, (_t, o, h, l, c, _v) in enumerate(bars[:shown]):
        col = GOLD if c >= o else RED
        cx = x0 + cw * (i + 0.5)
        d.line([(cx, sy(h)), (cx, sy(l))], fill=col, width=2)
        d.rectangle([cx - cw * 0.32, min(sy(o), sy(c)), cx + cw * 0.32, max(sy(o), sy(c)) + 2], fill=col)
    _text(d, (W // 2, 1180), "Not financial advice", _font(26, False), MUTED, "ma")
    _progress(d, t, duration)
    return img


def render_replay(ticker: str, bars: list, out: Path, duration: float = 12.0, fps: int = FPS, ts: datetime | None = None):
    ts = ts or datetime.now(timezone.utc)
    return _encode(lambda t: replay_frame(t, ticker, bars, ts, duration), duration - 0.5, out, duration, fps)


def map_caption(tiles: list, rng: random.Random | None = None) -> str:
    import voice_variety
    rng = rng or random
    up = sum(1 for _s, p, _v in tiles if p > 0)
    best = max(tiles, key=lambda x: x[1]); worst = min(tiles, key=lambda x: x[1])
    return "\n".join([rng.choice(["The whole market on one map", "Crypto heat map, last 24h", "Who is green and who is red"]), "",
                      f"{up} of {len(tiles)} biggest coins are up.", f"Strongest: ${best[0]} {_pct(best[1])}",
                      f"Weakest: ${worst[0]} {_pct(worst[1])}", "", rng.choice(["Anything on the map surprise you?", "Which tile would you look at first?"]),
                      "", voice_variety.disclaimer(rng), "", "#Crypto"])


def replay_caption(ticker: str, bars: list, rng: random.Random | None = None) -> str:
    import voice_variety
    rng = rng or random
    chg = (bars[-1][4] / bars[0][1] - 1) * 100
    return "\n".join([rng.choice([f"Replay: how ${ticker} moved", f"${ticker} over the last {len(bars)} hours, replayed"]), "",
                      f"{_pct(chg)} from {_price(bars[0][1])} to {_price(bars[-1][4])}.", "",
                      rng.choice(["Where does it go from here?", "What would you have done at the start?"]),
                      "", voice_variety.disclaimer(rng), "", f"#{ticker}"])


def frame(t, data, fng, ts, duration=15.0):
    return _sting(_frame(t, data, fng, ts, duration), t)


def map_frame(t, tiles, ts, duration=12.0):
    return _sting(_map_frame(t, tiles, ts, duration), t)


def replay_frame(t, ticker, bars, ts, duration=12.0):
    return _sting(_replay_frame(t, ticker, bars, ts, duration), t)


def render(data: dict, fng: dict | None, out: Path, duration: float = 15.0, fps: int = FPS,
           ts: datetime | None = None) -> tuple[Path, Path]:
    """-> (mp4, poster.png). Бросает RuntimeError, если нет ffmpeg или кодирование не удалось."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден")
    ts = ts or datetime.now(timezone.utc)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    poster = out.with_suffix(".png")
    frame(5.5 * duration / 15.0, data, fng, ts, duration).save(poster)   # обложка: сцена с лидерами в полном виде
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-crf", "24", "-c:a", "aac", "-b:a", "32k", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(int(duration * fps)):
            proc.stdin.write(frame(i / fps, data, fng, ts, duration).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return out, poster


def build_caption(data: dict, fng: dict | None, rng: random.Random | None = None) -> str:
    """Подпись к видео: не больше 3 кликабельных тикеров (BTC, лидер, аутсайдер), без обещаний."""
    import voice_variety
    rng = rng or random
    maj = {t: p for t, _price, p in data.get("majors", [])}
    head = rng.choice(["The crypto market in 15 seconds", "Last 24 hours in crypto, in 15 seconds", "15-second market check"])
    parts = [head]
    if "BTC" in maj:
        parts.append(f"$BTC {_pct(maj['BTC'])}" + (f", ETH {_pct(maj['ETH'])}" if "ETH" in maj else ""))
    if data.get("gainers"):
        t, _p, c = data["gainers"][0]
        parts.append(f"Leading: ${t} {_pct(c)}")
    if data.get("losers"):
        t, _p, c = data["losers"][0]
        parts.append(f"Lagging: ${t} {_pct(c)}")
    if fng and fng.get("value") is not None:
        parts.append(f"Fear & Greed: {fng['value']} ({fng.get('label', '')})")
    q = rng.choice(["Which move surprised you?", "Anything here you didn't expect?", "Where do you see the next move?"])
    tag = rng.choice(["#Crypto", "#CryptoMarket", "#Market"])
    return "\n".join([parts[0], ""] + parts[1:] + ["", q, "", voice_variety.disclaimer(rng), "", tag])
