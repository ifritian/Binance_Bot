"""Видео-новость ("Crypto news", ~30 с): выпуск в стиле новостной сводки для Binance Square.

Что в ролике: заставка канала, плашка CRYPTO NEWS, заголовок и источники, анимированный график монеты, фон
рынка (BTC и индекс страха и жадности), бегущая строка цен снизу, субтитры по словам, голос диктора (Kokoro,
офлайн-модель, без ключей) и новостной музыкальный фон, который синтезируется кодом (без чужих треков).

Честность: заголовок и источники - чужие, показываем с атрибуцией; цифры (цена, изменение, индекс) - реальные с биржи;
причин и прогнозов не выдумываем. Если голос недоступен, ролик собирается с субтитрами и музыкой.
"""
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import config
import news_radar
import video_post as vp
from video_post import BG, GOLD, GOLD2, GRID, MUTED, PANEL, RED, WHITE, H, W, _font, _text, ease

logger = logging.getLogger(__name__)

FPS = 15
LEAD_S = 0.9            # тишина/заставка перед первой фразой
TAIL_S = 1.0
SR = 44100
VOICE = "am_michael"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")

_MAJOR_RE = re.compile(r"\b(ETFs?|SEC|CFTC|Fed|FOMC|regulat\w+|law|bill|Senate|Congress|approv\w+|ban(?:s|ned)?|lawsuit|sues?|"
                       r"bankrupt\w*|hack(?:ed|s)?|exploit(?:ed)?|stolen|acqui\w+|merger|record high|all-time high|stablecoin|"
                       r"reserve|treasury|IPO|mainnet|upgrade|halving|rate (?:cut|hike)|tariffs?)\b", re.IGNORECASE)
_NAMES = {v: k for k, v in news_radar.COIN_NAMES.items() if k not in ("Ether", "Ripple")}
_ACRONYMS = {"SEC": "S E C", "ETF": "E T F", "ETFs": "E T Fs", "CFTC": "C F T C", "FOMC": "F O M C", "IPO": "I P O", "USDT": "U S D T",
             "USDC": "U S D C", "DeFi": "dee fi", "NFT": "N F T", "NFTs": "N F Ts", "CEO": "C E O", "US": "U S", "U.S.": "U S", "UK": "U K",
             "AI": "A I", "BTC": "Bitcoin", "ETH": "Ether", "XRP": "X R P", "SOL": "Solana", "BNB": "B N B", "DOGE": "Doge"}


# ---------------- Выбор сюжета ----------------

def pick_story(news: list, seen_titles: list, now_ts: float, max_age_h: float = 12.0) -> dict | None:
    """Сюжет, достойный отдельного видео: значимая тема (регулятор, ETF, взлом, ставка...) И подтверждён >= 2 изданиями.
    -> {"title", "sources", "ticker", "ts"} или None."""
    items = [n for n in news if now_ts - n["ts"] <= max_age_h * 3600]
    best = None
    for n in items:
        if not _MAJOR_RE.search(n["title"]) or news_radar.is_repeat(n["title"], seen_titles):
            continue
        group = [m for m in items if m is n or news_radar.is_repeat(m["title"], [n["title"]])]
        sources = list(dict.fromkeys(m["source"] for m in group))
        if len(sources) < 2:
            continue
        score = len(sources) * 2 + (1 if news_radar._URGENT_RE.search(n["title"]) else 0) + n["ts"] / 1e10
        if best is None or score > best[0]:
            best = (score, {"title": n["title"], "sources": sources[:3], "ticker": news_radar.coin_in_headline(n["title"]), "ts": n["ts"]})
    return best[1] if best else None


# ---------------- Сценарий ----------------

def _say(text: str) -> str:
    out = text
    for k, v in sorted(_ACRONYMS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"(?<![\w$]){re.escape(k)}(?![\w])", v, out)
    out = re.sub(r"\$(\w+)", r"\1", out).replace("%", " percent").replace("&", " and ").replace("—", ", ").replace("–", ", ")
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def _dir(p: float) -> str:
    return "up" if p >= 0 else "down"


def _price_words(p: float) -> str:
    if p >= 1000:
        return f"{p / 1000:.1f} thousand dollars".replace(".0 ", " ")
    return f"{p:.2f} dollars"


def build_script(story: dict, ctx: dict) -> list:
    """[{scene, text (для субтитров), say (для голоса)}]. ctx: ticker, pct, btc_price, btc_pct, fng."""
    title = news_radar.clean_title(story["title"], 150).rstrip(".")
    segs = [{"scene": "story", "text": f"Crypto news. {title}."}]
    srcs = story["sources"]
    segs.append({"scene": "story", "text": "Reported by " + (" and ".join(srcs[:2]) if len(srcs) > 1 else srcs[0]) + "."})
    t, pct = ctx.get("ticker"), ctx.get("pct")
    if t and pct is not None:
        name = _NAMES.get(t, t)
        if abs(pct) < 0.5:
            segs.append({"scene": "chart", "text": f"{name} has barely moved in 24 hours: {pct:+.1f}%.".replace("-", "−"),
                         "say": f"{_say(name)} has barely moved in 24 hours."})
        else:
            segs.append({"scene": "chart", "text": f"{name} is {_dir(pct)} {abs(pct):.1f}% over the last 24 hours.",
                         "say": f"{_say(name)} is {_dir(pct)} {abs(pct):.1f} percent over the last 24 hours."})
    if ctx.get("btc_price"):
        fng = ctx.get("fng")
        txt = f"Bitcoin trades near ${ctx['btc_price']:,.0f}, {_dir(ctx['btc_pct'])} {abs(ctx['btc_pct']):.1f}% on the day."
        say = f"Bitcoin trades near {_price_words(ctx['btc_price'])}, {_dir(ctx['btc_pct'])} {abs(ctx['btc_pct']):.1f} percent on the day."
        if fng:
            txt += f" Fear and Greed Index: {fng['value']}, {fng['label']}."
            say += f" The Fear and Greed Index reads {fng['value']}, {fng['label']}."
        segs.append({"scene": "context", "text": txt, "say": say})
    closings = [f"That is where things stand. This is {config.PROJECT_NAME}, back with more when the market moves.",
                f"We will keep watching this one. {config.PROJECT_NAME}, your crypto news in thirty seconds.",
                f"More updates as the story develops. This is {config.PROJECT_NAME}.",
                f"That was the story. Stay sharp out there. {config.PROJECT_NAME}."]
    segs.append({"scene": "outro", "text": closings[sum(map(ord, story["title"])) % len(closings)]})
    for s in segs:
        s.setdefault("say", _say(s["text"]))
    return segs


# ---------------- Голос ----------------

def _tts_dir() -> Path:
    return Path(os.environ.get("VEKTOR_TTS_DIR") or Path.home() / ".cache" / "vektor_tts")


def ensure_tts():
    """Kokoro или None (нет сети/пакета/модели). Пакет и модель (~120 МБ) ставятся один раз, только когда видео реально собирается."""
    try:
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kokoro-onnx", "soundfile"], check=True, timeout=300)
        from kokoro_onnx import Kokoro
        d = _tts_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f in MODEL_FILES:
            if not (d / f).exists() or (d / f).stat().st_size < 1_000_000:
                import requests
                with requests.get(MODEL_URL + f, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(d / f, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
        return Kokoro(str(d / MODEL_FILES[0]), str(d / MODEL_FILES[1]))
    except Exception as e:
        logger.warning("Голос Kokoro недоступен, видео будет без озвучки: %s", e)
        return None


def estimate_duration(text: str) -> float:
    return max(2.4, len(text.split()) * 0.38 + 0.4)


def synth(segs: list, tts=None) -> tuple:
    """-> (голос float32 mono @SR или None, длительности сегментов). Паузы между фразами ~0.3 с."""
    if tts is None:
        return None, [estimate_duration(s["text"]) for s in segs]
    chunks, durs = [], []
    gap = np.zeros(int(0.3 * SR), dtype=np.float32)
    for s in segs:
        try:
            audio, sr = tts.create(s["say"], voice=VOICE, speed=1.06, lang="en-us")
            audio = np.asarray(audio, dtype=np.float32)
            n = int(len(audio) * SR / sr)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        except Exception as e:
            logger.warning("Озвучка фразы не удалась (%s): %s", s["say"][:40], e)
            return None, [estimate_duration(x["text"]) for x in segs]
        chunks.append(np.concatenate([audio, gap]))
        durs.append(len(audio) / SR + 0.3)
    return np.concatenate(chunks), durs


# ---------------- Музыка ----------------

def music(duration: float, seed: int = 7) -> np.ndarray:
    """Новостной бит ~104 BPM (Am-F-C-G): бас, арпеджио, хэт, удар в начале. Синтез numpy, mono float32."""
    n = int(duration * SR)
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(seed)
    beat = 60 / 104
    t_all = np.arange(n) / SR
    prog = [(110.0, [220.0, 261.63, 329.63]), (87.31, [174.61, 220.0, 261.63]), (130.81, [261.63, 329.63, 392.0]), (98.0, [196.0, 246.94, 293.66])]

    def add(start, sig):
        i = int(start * SR)
        if i < n:
            out[i:i + len(sig)] += sig[:n - i]

    def env(L, a=0.005, r=0.1):
        e = np.ones(L)
        ai, ri = max(1, int(a * SR)), max(1, int(r * SR))
        e[:ai] = np.linspace(0, 1, ai)
        e[-ri:] = np.linspace(1, 0, ri) if L > ri else e[-ri:]
        return e

    bar = 0
    t = 0.0
    while t < duration:
        root, chord = prog[bar % 4]
        for k in range(8):                                        # бас восьмыми
            L = int(beat / 2 * SR * 0.9)
            tt = np.arange(L) / SR
            sig = (np.sin(2 * np.pi * root * tt) + 0.35 * np.sin(4 * np.pi * root * tt)) * env(L, 0.004, 0.06) * 0.30
            add(t + k * beat / 2, sig.astype(np.float32))
        for k in range(16):                                       # арпеджио шестнадцатыми
            f = chord[(k * 2 + (k // 4)) % 3] * (2 if k % 4 == 3 else 1)
            L = int(beat / 4 * SR * 0.8)
            tt = np.arange(L) / SR
            sig = (2 * np.abs(2 * ((f * tt) % 1) - 1) - 1) * env(L, 0.003, 0.05) * 0.075
            add(t + k * beat / 4, sig.astype(np.float32))
        for k in range(8):                                        # хэт
            L = int(0.04 * SR)
            add(t + k * beat / 2 + (beat / 4 if k % 2 else 0) * 0, (rng.standard_normal(L) * np.linspace(1, 0, L) * (0.05 if k % 2 else 0.09)).astype(np.float32))
        pad = sum(np.sin(2 * np.pi * f * np.arange(int(4 * beat * SR)) / SR) for f in chord) / 3
        add(t, (pad * env(len(pad), 0.4, 0.5) * 0.06).astype(np.float32))
        t += 4 * beat
        bar += 1
    hit_len = int(0.9 * SR)                                       # удар-заставка
    tt = np.arange(hit_len) / SR
    sweep = np.sin(2 * np.pi * (80 + 600 * tt ** 2) * tt) * np.exp(-tt * 3.5) * 0.35
    add(0.0, sweep.astype(np.float32))
    fade = np.minimum(1.0, np.minimum(t_all / 0.3, (duration - t_all) / 1.0)).clip(0, 1)
    return (out * fade).astype(np.float32)


def mix(voice, mus: np.ndarray, lead: float) -> np.ndarray:
    """Голос + музыка с приглушением музыки под речью. -> float32 mono @SR, нормализовано до -1 дБ."""
    total = len(mus)
    v = np.zeros(total, dtype=np.float32)
    if voice is not None:
        i = int(lead * SR)
        seg = voice[:max(0, total - i)]
        v[i:i + len(seg)] = seg
        win = int(0.15 * SR)
        e = np.convolve(np.abs(v), np.ones(win) / win, mode="same")
        duck = 1.0 - 0.65 * np.clip(e / 0.05, 0, 1)
        duck = np.convolve(duck, np.ones(int(0.2 * SR)) / int(0.2 * SR), mode="same")
    else:
        duck = np.ones(total, dtype=np.float32) * 0.8
    out = v * 1.0 + mus * duck * 0.9
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.89).astype(np.float32)


def write_wav(path: Path, mono: np.ndarray) -> None:
    pcm = (np.clip(mono, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------- Кадры ----------------

def _wrap(text: str, font, width: int) -> list:
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if font.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def tape_strip(items: list) -> Image.Image:
    """Горизонтальная лента 'BTC 73 412 +2.1%  •  ETH ...' для бегущей строки. items: [(ticker, price, pct)]."""
    f = _font(26)
    d0 = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    parts, x = [], 20
    for t, price, pct in items:
        a, b = f"{t} ", f"{vp._price(price)}  "
        c = vp._pct(pct)
        parts.append((x, a, b, c, pct))
        x += int(d0.textlength(a + b + c, font=f)) + 60
    base = x
    reps = max(1, math.ceil((W * 2) / max(base, 1)))
    img = Image.new("RGB", (base * reps, 64), PANEL)
    d = ImageDraw.Draw(img)
    parts = [(x0 + k * base, a, b, c, pct) for k in range(reps) for x0, a, b, c, pct in parts]
    img.info["base"] = base
    for x0, a, b, c, pct in parts:
        d.text((x0, 32), a, font=f, fill=GOLD, anchor="lm")
        xa = x0 + d0.textlength(a, font=f)
        d.text((xa, 32), b, font=f, fill=WHITE, anchor="lm")
        d.text((xa + d0.textlength(b, font=f), 32), c, font=f, fill=GOLD2 if pct >= 0 else RED, anchor="lm")
    return img


def _chrome(img: Image.Image, d, T: float, ts: datetime, strip: Image.Image | None):
    _text(d, (48, 54), config.PROJECT_NAME.upper(), _font(28), GOLD)
    _text(d, (W - 48, 60), ts.strftime("%b %d, %H:%M UTC"), _font(22, False), MUTED, "ra")
    d.rounded_rectangle([48, 110, 300, 158], radius=8, fill=RED)
    pulse = 0.55 + 0.45 * (math.sin(T * 5) > 0)
    d.ellipse([64, 126, 80, 142], fill=tuple(int(255 * pulse) if i == 0 else int(255 * pulse) for i in range(3)))
    _text(d, (92, 134), "CRYPTO NEWS", _font(24), WHITE, "lm")
    d.line([(48, 176), (W - 48, 176)], fill=GRID, width=2)
    if strip is not None:
        d.rectangle([0, 1170, W, 1234], fill=PANEL)
        off = int(T * 95) % strip.info.get("base", strip.width // 2)
        img.paste(strip.crop((off, 0, off + W, 64)), (0, 1170))
        d.line([(0, 1170), (W, 1170)], fill=GOLD, width=2)


def _scene_story(d, st: float, story: dict, ctx: dict | None = None):
    _text(d, (48, 230), "TOP STORY", _font(26), GOLD, alpha=ease(st / 0.4))
    lines = _wrap(story["title"].rstrip("."), _font(50), W - 96)[:7]
    for i, ln in enumerate(lines):
        a = ease((st - 0.25 - i * 0.18) / 0.4)
        _text(d, (48 + int((1 - a) * 40), 290 + i * 70), ln, _font(50), WHITE, alpha=a)
    y = 290 + len(lines) * 70 + 40
    ctx = ctx or {}
    cards = [(f"${ctx['ticker']}", ctx.get("pct")) for _ in [0] if ctx.get("ticker") and ctx.get("pct") is not None]
    if ctx.get("btc_pct") is not None:
        cards.append(("$BTC", ctx["btc_pct"]))
    for j, (lab, pv) in enumerate(cards):
        a = ease((st - 2.0 - j * 0.35) / 0.5)
        if a <= 0:
            continue
        x = 48 + j * 316
        d.rounded_rectangle([x, 800, x + 296, 930], radius=16, fill=PANEL)
        _text(d, (x + 24, 830), lab + " 24h", _font(24), MUTED, "lm", a)
        _text(d, (x + 24, 885), vp._pct(pv), _font(52), GOLD2 if pv >= 0 else RED, "lm", a)
    for j, s in enumerate(story["sources"]):
        a = ease((st - 1.2 - j * 0.3) / 0.4)
        if a <= 0:
            continue
        w = int(_font(26).getlength(s)) + 40
        x = 48 + sum(int(_font(26).getlength(x_)) + 56 for x_ in story["sources"][:j])
        d.rounded_rectangle([x, y, x + w, y + 48], radius=24, outline=GOLD, width=2)
        _text(d, (x + 20, y + 24), s, _font(26, False), GOLD2, "lm", a)


def _scene_chart(d, st: float, ctx: dict):
    bars = ctx.get("bars") or []
    t = ctx.get("ticker") or "BTC"
    pct = ctx.get("pct") or 0.0
    col = GOLD2 if pct >= 0 else RED
    _text(d, (48, 230), f"${t}", _font(64), WHITE)
    _text(d, (48, 310), vp._price(bars[-1][4]) if bars else "", _font(50), col)
    _text(d, (W - 48, 326), vp._pct(pct), _font(48), col, "ra")
    _text(d, (48, 380), "last 48 hours, 1h candles", _font(22, False), MUTED)
    if len(bars) < 3:
        return
    closes = [b[4] for b in bars]
    hi, lo = max(closes), min(closes)
    x0, x1, y0, y1 = 48, W - 48, 450, 900
    sy = lambda p: y1 - (p - lo) / max(hi - lo, 1e-12) * (y1 - y0)
    for k in range(5):
        d.line([(x0, y0 + k * (y1 - y0) / 4), (x1, y0 + k * (y1 - y0) / 4)], fill=GRID, width=1)
    n = len(closes)
    shown = max(2, int(2 + (n - 2) * ease(st / 1.6)))
    pts = [(x0 + (x1 - x0) * i / (n - 1), sy(c)) for i, c in enumerate(closes[:shown])]
    fill = tuple(int(BG[i] + (col[i] - BG[i]) * 0.22) for i in range(3))
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        d.polygon([(xa, ya), (xb, yb), (xb, y1), (xa, y1)], fill=fill)
    d.line(pts, fill=col, width=5, joint="curve")
    d.ellipse([pts[-1][0] - 9, pts[-1][1] - 9, pts[-1][0] + 9, pts[-1][1] + 9], fill=col)


def _scene_context(d, st: float, ctx: dict):
    bp, bc = ctx.get("btc_price"), ctx.get("btc_pct", 0.0)
    d.rounded_rectangle([48, 240, W - 48, 470], radius=18, fill=PANEL)
    _text(d, (80, 280), "BITCOIN", _font(26), MUTED)
    _text(d, (80, 330), vp._price(bp) if bp else "", _font(72), WHITE)
    _text(d, (W - 80, 345), vp._pct(bc), _font(52), GOLD2 if bc >= 0 else RED, "ra")
    fng = ctx.get("fng")
    if fng:
        _text(d, (48, 540), "FEAR & GREED INDEX", _font(26), MUTED)
        v = fng["value"] * ease(st / 1.2)
        _text(d, (48, 590), f"{round(v)}", _font(110), GOLD if fng["value"] >= 50 else RED)
        _text(d, (W - 48, 650), fng["label"], _font(40), WHITE, "ra")
        x0, x1, y = 48, W - 48, 760
        for i in range(x1 - x0):
            f = i / (x1 - x0)
            c = tuple(int(RED[k] + (GOLD[k] - RED[k]) * f) for k in range(3))
            d.line([(x0 + i, y), (x0 + i, y + 26)], fill=c)
        mx = x0 + (x1 - x0) * v / 100
        d.polygon([(mx, y - 8), (mx - 14, y - 34), (mx + 14, y - 34)], fill=WHITE)
        _text(d, (x0, y + 44), "Fear", _font(22, False), MUTED)
        _text(d, (x1, y + 44), "Greed", _font(22, False), MUTED, "ra")


def _scene_outro(d, st: float):
    a = ease(st / 0.5)
    _text(d, (W // 2, 520), config.PROJECT_NAME.upper(), _font(64), GOLD, "mm", a)
    d.rectangle([W // 2 - int(170 * a), 570, W // 2 + int(170 * a), 574], fill=GOLD)
    _text(d, (W // 2, 640), "Market news, charts and honest results", _font(28, False), WHITE, "mm", a)
    _text(d, (W // 2, 700), "Follow for more market news", _font(24, False), MUTED, "mm", a)


def _subtitle(img: Image.Image, d, words_state: tuple):
    """words_state: (список слов чанка, индекс активного)."""
    words, active = words_state
    f = _font(36)
    lines, cur = [], []
    for i, w in enumerate(words):
        trial = " ".join(x for x, _ in cur + [(w, i)])
        if f.getlength(trial) <= W - 130 or not cur:
            cur.append((w, i))
        else:
            lines.append(cur)
            cur = [(w, i)]
    lines.append(cur)
    box_h = 20 + len(lines) * 52
    y0 = 1130 - box_h
    d.rounded_rectangle([40, y0, W - 40, 1130], radius=16, fill=(8, 10, 13))
    for li, ln in enumerate(lines):
        text = " ".join(w for w, _ in ln)
        x = (W - f.getlength(text)) / 2
        y = y0 + 10 + li * 52
        for w, i in ln:
            d.text((x, y), w, font=f, fill=GOLD2 if i == active else WHITE)
            x += f.getlength(w + " ")


def timeline(segs: list, durs: list, lead: float = LEAD_S) -> list:
    t, out = lead, []
    for s, d in zip(segs, durs):
        out.append({**s, "start": t, "end": t + d})
        t += d
    return out


def _subtitle_state(tl: list, T: float):
    for seg in tl:
        if seg["start"] <= T < seg["end"]:
            words = seg["text"].split()
            chunks = [words[i:i + 6] for i in range(0, len(words), 6)]
            speech = seg["end"] - seg["start"] - 0.3
            tot = sum(len(w) + 1 for w in words)
            pos = (T - seg["start"]) / max(speech, 0.1) * tot
            acc = 0
            for ch in chunks:
                n = sum(len(w) + 1 for w in ch)
                if pos < acc + n or ch is chunks[-1]:
                    inner, k = pos - acc, 0
                    for i, w in enumerate(ch):
                        if inner < len(w) + 1:
                            k = i
                            break
                        inner -= len(w) + 1
                        k = i
                    return ch, k
                acc += n
    return None


def frame(T: float, tl: list, story: dict, ctx: dict, ts: datetime, strip: Image.Image | None, total: float) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # фоновая сетка с медленным сдвигом
    off = int(T * 12) % 60
    for k in range(-1, 22):
        d.line([(0, 190 + k * 60 + off), (W, 190 + k * 60 + off)], fill=(16, 20, 25), width=1)
    _chrome(img, d, T, ts, strip)
    seg = next((s for s in tl if s["start"] <= T < s["end"]), tl[-1] if T >= tl[-1]["end"] else tl[0])
    first = next(s for s in tl if s["scene"] == seg["scene"])
    st = max(0.0, T - first["start"])
    {"story": lambda: _scene_story(d, st, story, ctx), "chart": lambda: _scene_chart(d, st, ctx),
     "context": lambda: _scene_context(d, st, ctx), "outro": lambda: _scene_outro(d, st)}[seg["scene"]]()
    if st < 0.3 and T > LEAD_S:                                           # смена сцены: золотой "свуш"
        x = int(W * ease(st / 0.3))
        d.rectangle([x - 40, 190, x, 1160], fill=GOLD)
    sub = _subtitle_state(tl, T) if seg["scene"] != "outro" else None
    if sub:
        _subtitle(img, d, sub)
    d.rectangle([0, H - 8, W, H], fill=GRID)
    d.rectangle([0, H - 8, int(W * min(T / total, 1.0)), H], fill=GOLD)
    return vp._sting(img, T)


# ---------------- Сборка ----------------

def render(story: dict, ctx: dict, out: Path, tape: list, ts: datetime | None = None, tts="auto") -> tuple:
    """-> (mp4, poster, длительность, есть_ли_голос). RuntimeError, если нет ffmpeg."""
    if not shutil.which("ffmpeg"):
        try:
            subprocess.run("sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg", shell=True, check=True, timeout=240)
        except Exception as e:
            logger.warning("Не удалось установить ffmpeg: %s", e)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg не найден")
    ts = ts or datetime.now(timezone.utc)
    segs = build_script(story, ctx)
    engine = ensure_tts() if tts == "auto" else tts
    voice, durs = synth(segs, engine)
    tl = timeline(segs, durs)
    total = tl[-1]["end"] + TAIL_S
    audio = mix(voice, music(total), LEAD_S)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    write_wav(wav, audio)
    strip = tape_strip(tape) if tape else None
    poster = out.with_suffix(".png")
    frame(tl[2]["start"] + 1.8 if len(tl) > 2 else 2.5, tl, story, ctx, ts, strip, total).save(poster)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
           "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(int(total * FPS)):
            proc.stdin.write(frame(i / FPS, tl, story, ctx, ts, strip, total).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {err[:300]}")
    finally:
        if proc.poll() is None:
            proc.kill()
        wav.unlink(missing_ok=True)
    return out, poster, total, voice is not None


def build_caption(story: dict, ctx: dict, rng: random.Random | None = None) -> str:
    rng = rng or random
    t = ctx.get("ticker")
    head = rng.choice(["Crypto news in 30 seconds", "The story everyone is reading, in 30 seconds", "News brief: what happened and what the market did"])
    lines = [head, "", news_radar.clean_title(story["title"], 160) + f" ({', '.join(story['sources'][:2])})"]
    if t and ctx.get("pct") is not None:
        lines.append(f"${t} {vp._pct(ctx['pct'])} in 24h.")
    if ctx.get("btc_pct") is not None:
        lines.append(f"$BTC {vp._pct(ctx['btc_pct'])}.")
    lines += ["", rng.choice(["Does this change anything for you?", "Noise or a real shift? Your read?", "Priced in, or not yet?"]),
              "", "#CryptoNews"]
    return "\n".join(lines)
