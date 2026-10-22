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
_DOWN = cg._NEG_COLOR
_TEXT = "#EAECEF"
_MUTED = cg._AXIS_TEXT_COLOR
_GRID = cg._GRID_COLOR


def _save(fig, path) -> None:
    """Сохраняет карточку с единым фирменным знаком проекта в правом нижнем углу."""
    fig.text(0.965, 0.018, config.PROJECT_NAME, color=_MUTED, fontsize=8, ha="right", va="bottom", alpha=0.8)
    fig.savefig(path, facecolor=_BG)


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
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку среза рынка")
        plt.close("all")
        return None


def _fng_color(v: float) -> str:
    stops = [(0, "#F6465D"), (25, "#F0662F"), (50, "#F0B90B"), (75, "#FCD535"), (100, "#FFF1B8")]
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
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку индекса страха и жадности")
        plt.close("all")
        return None


_KIND_COLORS = {"delist": "#F6465D", "network": "#F0B90B", "listing": "#FCD535", "event": "#EAECEF", "news": "#848E9C"}


def radar_card(items: list[dict]) -> Path | None:
    """Карточка "Binance Radar": по строке на объявление, цветная метка типа."""
    import textwrap

    import news_radar
    try:
        n = max(1, len(items))
        h = 1.5 + 0.95 * n
        fig = plt.figure(figsize=(8, h), dpi=150)
        fig.patch.set_facecolor(_BG)
        fig.text(0.04, 1 - 0.55 / h, "Binance Radar", color=_TEXT, fontsize=19, fontweight="bold", va="center")
        fig.text(0.96, 1 - 0.55 / h, "official announcements", color=_MUTED, fontsize=10, ha="right", va="center")
        for k, it in enumerate(items):
            y = 1 - (1.2 + 0.95 * k) / h
            color = _KIND_COLORS.get(it["kind"], _MUTED)
            fig.patches.append(Rectangle((0.04, y - 0.30 / h), 0.012, 0.72 / h, transform=fig.transFigure,
                                         facecolor=color, edgecolor="none"))
            fig.text(0.075, y + 0.28 / h, news_radar.LABELS[it["kind"]].upper(), color=color,
                     fontsize=9, fontweight="bold", va="center")
            title = "\n".join(textwrap.wrap(news_radar.clean_title(it["title"], 110), 62)[:2])
            fig.text(0.075, y - 0.05 / h, title, color=_TEXT, fontsize=11.5, va="center", linespacing=1.3)
        path = _out("radar_card.png")
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку Binance Radar")
        plt.close("all")
        return None


def listing_card(ev: dict) -> Path | None:
    """Карточка отчёта по листингу: линия цены от первого открытия + крупное изменение."""
    try:
        s = ev["stats"]
        closes = s["closes"]
        fig = plt.figure(figsize=(8, 5), dpi=150)
        fig.patch.set_facecolor(_BG)
        up = s["pct_close"] >= 0
        color = _UP if up else _DOWN
        label = "24 hours after listing" if ev["kind"] == "d1" else "7 days after listing"
        fig.text(0.05, 0.92, f"${ev['ticker']}", color=_TEXT, fontsize=22, fontweight="bold", va="center")
        fig.text(0.05, 0.85, f"Binance listing report: {label}", color=_MUTED, fontsize=11, va="center")
        fig.text(0.95, 0.90, f"{s['pct_close']:+.1f}%".replace("-", "−"), color=color, fontsize=34,
                 fontweight="bold", ha="right", va="center")
        ax = fig.add_axes([0.06, 0.24, 0.88, 0.50])
        ax.set_facecolor(_BG)
        xs = list(range(len(closes)))
        ax.plot(xs, closes, color=color, linewidth=2)
        ax.fill_between(xs, closes, s["open"], color=color, alpha=0.12)
        ax.axhline(s["open"], color=_MUTED, linewidth=1, linestyle="--")
        ax.text(0, s["open"], " first price", color=_MUTED, fontsize=9, va="bottom")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xticks([])
        ax.tick_params(colors=_MUTED, labelsize=8)
        ax.grid(color=_GRID, linewidth=0.5)
        low, high = f"{s['pct_low']:+.1f}%".replace("-", "−"), f"{s['pct_high']:+.1f}%".replace("-", "−")
        vol = s["quote_volume"]
        vol_txt = f"{vol / 1e9:.1f}B USDT" if vol >= 1e9 else (f"{vol / 1e6:.1f}M USDT" if vol >= 1e6 else f"{vol / 1e3:.0f}K USDT")
        fig.text(0.05, 0.09, f"Range {low} to {high}    |    Volume {vol_txt}", color=_TEXT, fontsize=11, va="center")
        path = _out("listing_card.png")
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку листинга")
        plt.close("all")
        return None


def weekly_card(data: dict) -> Path | None:
    """Карточка недельного обзора: BTC/ETH/корзина сверху, ниже лидеры и аутсайдеры недели."""
    try:
        best, worst = data["best"][:4], data["worst"][:4]
        sections = [("Best of the week", _UP, best), ("Weakest of the week", _DOWN, worst)]
        sections = [s for s in sections if s[2]]
        rows = sum(len(s[2]) for s in sections)
        max_abs = max([abs(p) for _t, p in sum([s[2] for s in sections], [])] or [1.0])
        height_units = 40 + len(sections) * 6.0 + rows * 4.6 + 3
        fig = plt.figure(figsize=(8, height_units * 0.08), dpi=150)
        fig.patch.set_facecolor(_BG)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, 100)
        ax.set_ylim(0, height_units)
        ax.invert_yaxis()
        ax.axis("off")
        ax.text(5, 7, "Week in review", color=_TEXT, fontsize=17, fontweight="bold", va="center")
        ax.text(5, 12.5, data["period"], color=_MUTED, fontsize=10, va="center")
        tiles = [(n, v) for n, v in (("BTC", data.get("btc")), ("ETH", data.get("eth")), ("Basket", data.get("basket"))) if v is not None]
        w = 90 / max(len(tiles), 1) - 2
        x = 5
        for name, pct in tiles:
            color = _UP if pct >= 0 else _DOWN
            ax.add_patch(Rectangle((x, 17), w, 14, facecolor="#12171D", edgecolor=_GRID, linewidth=1))
            ax.text(x + 2.5, 21.5, name, color=_MUTED, fontsize=10, va="center")
            ax.text(x + 2.5, 27, f"{pct:+.1f}%".replace("-", "−"), color=color, fontsize=15, fontweight="bold", va="center")
            x += w + 3
        y = 38
        for name, color, items in sections:
            ax.text(5, y, name, color=color, fontsize=11, fontweight="bold", va="center")
            y += 4.2
            for t, pct in items:
                ax.text(5, y, f"${t}", color=_TEXT, fontsize=11, va="center")
                bw = max(abs(pct) / max_abs * 52, 1.5)
                ax.add_patch(Rectangle((30, y - 1.5), bw, 3, facecolor=color, alpha=0.85, linewidth=0))
                ax.text(95, y, f"{pct:+.1f}%".replace("-", "−"), color=color, fontsize=11, fontweight="bold", ha="right", va="center")
                y += 4.6
            y += 1.8
        path = _out("weekly_card.png")
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку недельного обзора")
        plt.close("all")
        return None


def momentum_card(event: dict) -> Path | None:
    """Карточка недельного моментума: слева сильнейшие монеты за 28 дней, справа слабейшие."""
    try:
        new = event["opened"]
        longs, shorts = new["long"][:5], new["short"][:5]
        fig = plt.figure(figsize=(8, 5), dpi=150)
        fig.patch.set_facecolor(_BG)
        fig.text(0.05, 0.92, "Momentum board", color=_TEXT, fontsize=22, fontweight="bold", va="center")
        fig.text(0.05, 0.855, "28-day return, weekly paper test", color=_MUTED, fontsize=11, va="center")
        c = event.get("closed")
        if c:
            col = _UP if c["net"] >= 0 else _DOWN
            fig.text(0.95, 0.90, f"{c['net']:+.1f}%".replace("-", "−"), color=col, fontsize=30, fontweight="bold",
                     ha="right", va="center")
            fig.text(0.95, 0.83, "last week's basket, after costs", color=_MUTED, fontsize=9, ha="right", va="center")
        for col_x, title, rows, color in ((0.05, "STRONGEST", longs, _UP), (0.54, "WEAKEST", shorts, _DOWN)):
            fig.text(col_x, 0.73, title, color=color, fontsize=11, fontweight="bold", va="center")
            for k, x in enumerate(rows):
                y = 0.63 - 0.115 * k
                fig.text(col_x, y, x["sym"].removesuffix("USDT"), color=_TEXT, fontsize=17, fontweight="bold", va="center")
                fig.text(col_x + 0.40, y, f"{x['ret28'] * 100:+.0f}%".replace("-", "−"), color=color, fontsize=17,
                         fontweight="bold", ha="right", va="center")
        path = _out("momentum_card.png")
        _save(fig, path)
        plt.close(fig)
        return path
    except Exception:
        logger.exception("Не удалось построить карточку моментума")
        plt.close("all")
        return None
