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
    return f"{p:,.2f}" if p >= 1 else f"{p:.4g}"


def _text(d, xy, s, font, fill, anchor="la", alpha=1.0):
    if alpha < 1.0:
        fill = tuple(int(BG[i] + (fill[i] - BG[i]) * alpha) for i in range(3))
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _header(d, ts: datetime, a=1.0):
    _text(d, (48, 60), "MARKET RADAR", _font(30), GOLD, alpha=a)
    _text(d, (W - 48, 66), ts.strftime("%b %d, %H:%M UTC"), _font(24, False), MUTED, "ra", a)
    d.line([(48, 112), (W - 48, 112)], fill=GRID, width=2)


def scenes(duration: float = 15.0) -> dict:
    k = duration / 15.0
    return {"majors": (0, 4.0 * k), "gainers": (4.0 * k, 8.5 * k), "losers": (8.5 * k, 12.0 * k), "mood": (12.0 * k, duration)}


def frame(t: float, data: dict, fng: dict | None, ts: datetime, duration: float = 15.0) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    sc = scenes(duration)
    _header(d, ts)
    name = next(n for n, (a, b) in sc.items() if t < b) if t < duration else "mood"
    a0, b0 = sc[name]
    lt = t - a0                                   # время внутри сцены
    fade = ease(min(lt / 0.4, 1.0)) * (1.0 if b0 - t > 0.3 else max((b0 - t) / 0.3, 0.0))
    if name == "majors":
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
    return img


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
