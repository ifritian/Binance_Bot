"""
post_cards.py - картинки-карточки для текстовых форматов Square (в ленте пост
с картинкой заметнее текстового): "Срез рынка" и "Индекс страха и жадности".
Стиль - как у графиков бота (chart_generator): тёмный фон, циан для роста,
маджента для падения. Подписи только английские. Любая ошибка -> None, пост
просто уходит без картинки.
"""
import logging
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Wedge

import config
import chart_generator as cg

logger = logging.getLogger(__name__)

_BG = cg._BG_COLOR
_UP = cg._UP_COLOR
_DOWN = cg._DOWN_COLOR
_TEXT = "#EAECEF"
_MUTED = cg._AXIS_TEXT_COLOR
_GRID = cg._GRID_COLOR


def _out(name: str) -> Path:
    cg._CHARTS_DIR.mkdir(exist_ok=True)
    return cg._CHARTS_DIR / name


def _price(p: float) -> str:
    return f"{p:,.0f}" if p >= 1000 else cg._format_price(p)


def digest_card(data: dict, slot_hour_utc: int) -> Path | None:
    """Карточка среза: мейджоры сверху, ниже лидеры роста и падения с полосами."""
    try:
        title = "Morning market snapshot" if slot_hour_utc < 12 else "Evening market snapshot"
        majors = data["majors"][:2]
        sections = []
        if data["gainers"]:
            sections.append(("Top gainers", _UP, data["gainers"]))
        if data["losers"]:
            sections.append(("Top losers", _DOWN, data["losers"]))
        rows = sum(len(s[2]) for s in sections)
        max_abs = max([abs(c) for _t, _p, c in sum([s[2] for s in sections], [])] or [1.0])

        # Высота по содержимому: 1 условная единица = 0.08 дюйма (как по X).
        height_units = 34 + len(sections) * 6.0 + rows * 4.6 + 3
        fig = plt.figure(figsize=(8, height_units * 0.08), dpi=150)
        fig.patch.set_facecolor(_BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 100)
        ax.set_ylim(0, height_units)
        ax.invert_yaxis()
        ax.axis("off")
        ax.set_facecolor(_BG)

        ax.text(5, 7, title, color=_TEXT, fontsize=17, fontweight="bold", va="center")
        ax.text(5, 12.5, "last 24h", color=_MUTED, fontsize=10, va="center")
        ax.text(95, 7, "BINANCE", color=_TEXT, alpha=0.18, fontsize=13, fontweight="bold",
                family="monospace", ha="right", va="center")

        x = 5
        for t, price, pct in majors:
            color = _UP if pct >= 0 else _DOWN
            ax.add_patch(Rectangle((x, 17), 43, 11, facecolor="#12171D", edgecolor=_GRID, linewidth=1))
            ax.text(x + 2.5, 20.5, f"${t}", color=_TEXT, fontsize=12, fontweight="bold", va="center")
            ax.text(x + 2.5, 25, _price(price), color=_MUTED, fontsize=10, va="center")
            ax.text(x + 40.5, 22.5, f"{pct:+.1f}%", color=color, fontsize=14, fontweight="bold", ha="right", va="center")
            x += 47

        y = 34
        bar_x0, bar_w_max = 30, 52
        for name, color, items in sections:
            ax.text(5, y, name, color=color, fontsize=11, fontweight="bold", va="center")
            y += 4.2
            for t, _p, pct in items:
                ax.text(5, y, f"${t}", color=_TEXT, fontsize=11, va="center")
                w = max(abs(pct) / max_abs * bar_w_max, 1.5)
                ax.add_patch(Rectangle((bar_x0, y - 1.5), w, 3, facecolor=color, alpha=0.85, linewidth=0))
                ax.text(95, y, f"{pct:+.1f}%", color=color, fontsize=11, fontweight="bold", ha="right", va="center")
                y += 4.6
            y += 1.8

        path = _out("digest_card.png")
        fig.savefig(path, facecolor=_BG)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку среза рынка")
        plt.close("all")
        return None


def _fng_color(v: float) -> str:
    stops = [(0, "#E4007A"), (25, "#F0662F"), (50, "#F0B90B"), (75, "#7FCB4B"), (100, "#29ABE2")]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if v <= b:
            t = (v - a) / (b - a)
            ra, rb = [int(ca[i:i + 2], 16) for i in (1, 3, 5)], [int(cb[i:i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{int(x + (y - x) * t):02X}" for x, y in zip(ra, rb))
    return stops[-1][1]


def fng_card(data: dict) -> Path | None:
    """Шкала страха и жадности 0..100 со стрелкой, значением и вчерашним числом."""
    try:
        v = max(0, min(100, int(data["value"])))
        fig = plt.figure(figsize=(8, 5), dpi=150)
        fig.patch.set_facecolor(_BG)
        ax = fig.add_axes([0.05, 0.16, 0.9, 0.66])
        ax.set_xlim(-1.25, 1.25)
        ax.set_ylim(-0.25, 1.1)
        ax.set_aspect("equal")
        ax.axis("off")
        n = 50
        for i in range(n):
            a0, a1 = 180 - 180 * i / n, 180 - 180 * (i + 1) / n
            ax.add_patch(Wedge((0, 0), 1.0, a1, a0, width=0.28, facecolor=_fng_color(100 * (i + 0.5) / n), linewidth=0))
        ang = math.radians(180 - 180 * v / 100)
        ax.plot([0, 0.72 * math.cos(ang)], [0, 0.72 * math.sin(ang)], color=_TEXT, linewidth=4, solid_capstyle="round", zorder=5)
        ax.add_patch(plt.Circle((0, 0), 0.06, color=_TEXT, zorder=6))
        for val, lab, ha in ((0, "Extreme fear", "left"), (100, "Extreme greed", "right")):
            a = math.radians(180 - 180 * val / 100)
            ax.text(0.86 * math.cos(a), -0.08, lab, color=_MUTED, fontsize=9, ha="center", va="top")
        fig.text(0.5, 0.075, str(v), color=_fng_color(v), fontsize=44, fontweight="bold", ha="center", va="center")
        fig.text(0.5, 0.93, "Crypto Fear & Greed Index", color=_TEXT, fontsize=17, fontweight="bold", ha="center", va="center")
        sub = str(data.get("label", ""))
        if data.get("prev") is not None:
            sub += f"   |   yesterday {data['prev']} ({v - data['prev']:+d})"
        fig.text(0.5, 0.86, sub, color=_MUTED, fontsize=11, ha="center", va="center")
        path = _out("fng_card.png")
        fig.savefig(path, facecolor=_BG)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку индекса страха и жадности")
        plt.close("all")
        return None
