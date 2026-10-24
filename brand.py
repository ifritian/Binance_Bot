"""Узнаваемый стиль Market Radar: единая плашка под каждой картинкой поста и единый брендовый хештег."""
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import matplotlib

logger = logging.getLogger(__name__)
import config
TAG = "#" + config.PROJECT_NAME.replace(" ", "")
BG, GOLD, MUTED = (11, 14, 17), (240, 185, 11), (132, 142, 156)
LABELS = {"digest": "MARKET SNAPSHOT", "fear_greed": "FEAR & GREED", "breakout": "BREAKOUT WATCH", "hot_mover": "HOT MOVER",
          "mover_result": "RESULT", "mover_followup": "FOLLOW-UP", "level_check": "LEVEL CHECK", "funding": "FUNDING RADAR", "anchor": "MACRO DAY", "binance_radar": "BINANCE RADAR",
          "news_move": "NEWS + PRICE", "listing": "NEW LISTING", "listing_report": "LISTING REPORT", "altcoin_basket": "ALT BASKET",
          "weekly_brief": "WEEKLY REVIEW", "momentum": "MOMENTUM LOG", "question": "COMMUNITY"}


def _font(size: int, bold: bool):
    d = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    return ImageFont.truetype(str(d / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


def stamp(path, fmt: str):
    """Копия картинки с брендовой плашкой снизу. При любой ошибке возвращает исходный путь."""
    try:
        src = Path(path)
        im = Image.open(src).convert("RGB")
        w, h = im.size
        bar = max(44, round(w * 0.045))
        out = Image.new("RGB", (w, h + bar), BG)
        out.paste(im, (0, 0))
        d = ImageDraw.Draw(out)
        d.rectangle([0, h, w, h + 2], fill=GOLD)
        fs = round(bar * 0.42)
        d.ellipse([bar * 0.45, h + bar * 0.34, bar * 0.45 + bar * 0.32, h + bar * 0.66], fill=GOLD)
        d.text((bar * 0.45 + bar * 0.5, h + bar / 2 + 1), config.PROJECT_NAME.upper(), font=_font(fs, True), fill=GOLD, anchor="lm")
        label = LABELS.get(fmt)
        if label:
            d.text((w - bar * 0.45, h + bar / 2 + 1), label, font=_font(round(fs * 0.9), False), fill=MUTED, anchor="rm")
        dst = src.with_name(src.stem + "_brand.png")
        out.save(dst)
        return dst
    except Exception:
        logger.exception("Не удалось нанести брендовую плашку на %s", path)
        return path


def tag_text(text: str) -> str:
    """Добавляет брендовый хештег в конец поста (один раз)."""
    if not text or TAG.lower() in text.lower():
        return text
    return text.rstrip() + " " + TAG
