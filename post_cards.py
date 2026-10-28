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
from matplotlib.patches import FancyBboxPatch, Rectangle, Wedge

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


def _chip(ax, x, y, w, h, text, fg, bg="#161B22", fs=11):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", facecolor=bg, edgecolor="none"))
    ax.text(x + w / 2, y + h / 2, text, color=fg, fontsize=fs, fontweight="bold", ha="center", va="center")


def digest_card(data: dict, slot_hour_utc: int) -> Path | None:
    """Карточка среза: два крупных блока (BTC, ETH), ниже две колонки - лидеры роста и падения."""
    try:
        title = "Morning market snapshot" if slot_hour_utc < 12 else "Evening market snapshot"
        majors = data["majors"][:2]
        up, dn = data["gainers"][:4], data["losers"][:4]
        cols = [c for c in (("Gainers", _UP, up), ("Losers", _DOWN, dn)) if c[2]]
        max_abs = max([abs(c) for _t, _p, c in up + dn] or [1.0])
        rows = max((len(c[2]) for c in cols), default=0)
        H = 48 + rows * 9.5 + 3
        fig = plt.figure(figsize=(8, H * 0.0802), dpi=150)
        fig.patch.set_facecolor(_BG)
        ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, H); ax.invert_yaxis(); ax.axis("off")
        ax.text(5, 8, title, color=_TEXT, fontsize=20, fontweight="bold", va="center")
        _chip(ax, 80, 5, 15, 6, "LAST 24H", _MUTED, fs=9)
        x = 5
        for t, price, pct in majors:
            color = _UP if pct >= 0 else _DOWN
            ax.add_patch(FancyBboxPatch((x, 15), 43.5, 18, boxstyle="round,pad=0,rounding_size=1.6", facecolor="#12171D", edgecolor="none"))
            ax.text(x + 3, 20.5, t, color=_MUTED, fontsize=12, fontweight="bold", va="center")
            ax.text(x + 3, 28, _price(price), color=_TEXT, fontsize=19, fontweight="bold", va="center")
            ax.text(x + 40.5, 24, f"{pct:+.1f}%".replace("-", "−"), color=color, fontsize=19, fontweight="bold", ha="right", va="center")
            x += 46.5
        colw = 43.5 if len(cols) == 2 else 90
        x = 5
        for name, color, items in cols:
            ax.text(x, 40, name.upper(), color=color, fontsize=11, fontweight="bold", va="center")
            ax.plot([x, x + colw], [43, 43], color=_GRID, linewidth=1)
            y = 48
            for t, _p, pct in items:
                ax.text(x, y, t, color=_TEXT, fontsize=15, fontweight="bold", va="center")
                ax.text(x + colw, y, f"{pct:+.1f}%".replace("-", "−"), color=color, fontsize=15, fontweight="bold", ha="right", va="center")
                ax.add_patch(Rectangle((x, y + 3.2), colw, 0.9, facecolor=_GRID, linewidth=0))
                ax.add_patch(Rectangle((x, y + 3.2), max(abs(pct) / max_abs * colw, 1.0), 0.9, facecolor=color, linewidth=0))
                y += 9.5
            x += 46.5
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


_FNG_ZONES = [(0, 25, "Extreme fear"), (25, 45, "Fear"), (45, 55, "Neutral"), (55, 75, "Greed"), (75, 100, "Extreme greed")]


def fng_card(data: dict) -> Path | None:
    """Индекс страха и жадности: крупное число, подпись и цветная шкала с маркером (без стрелки)."""
    try:
        v = max(0, min(100, int(data["value"])))
        fig = plt.figure(figsize=(8, 4.5), dpi=150)
        fig.patch.set_facecolor(_BG)
        ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, 56.25); ax.invert_yaxis(); ax.axis("off")
        ax.text(5, 8, "Fear & Greed Index", color=_TEXT, fontsize=20, fontweight="bold", va="center")
        ax.text(5, 14, "Crypto market mood", color=_MUTED, fontsize=11, va="center")
        col = _fng_color(v)
        ax.text(5, 31, str(v), color=col, fontsize=76, fontweight="bold", va="center")
        label = str(data.get("label", "")) or next(n for a, b, n in _FNG_ZONES if v <= b)
        ax.text(41, 28, label, color=_TEXT, fontsize=24, fontweight="bold", va="center")
        if data.get("prev") is not None:
            d = v - int(data["prev"])
            ax.text(41, 35.5, f"Yesterday {data['prev']}   {d:+d}".replace("-", "−"), color=_MUTED, fontsize=13, va="center")
        x0, x1, y = 5, 95, 43
        n = 90
        for i in range(n):
            ax.add_patch(Rectangle((x0 + (x1 - x0) * i / n, y), (x1 - x0) / n + 0.05, 2.4, facecolor=_fng_color(100 * (i + 0.5) / n), linewidth=0))
        mx = x0 + (x1 - x0) * v / 100
        ax.add_patch(plt.Circle((mx, y + 1.2), 1.9, facecolor=_TEXT, edgecolor=_BG, linewidth=3, zorder=5))
        for a, b, name in _FNG_ZONES:
            active = a <= v <= b and not (v == a and a != 0 and any(z[1] == a and z[0] < a for z in _FNG_ZONES) and v != a)
            ax.text(x0 + (x1 - x0) * (a + b) / 200, y + 6.5, name, color=_TEXT if (a <= v < b or (v == 100 and b == 100)) else _MUTED,
                    fontsize=8.5, ha="center", va="center", fontweight="bold" if (a <= v < b or (v == 100 and b == 100)) else "normal")
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
            ax.text(x + 2.5, 27, f"{pct:+.1f}%".replace("-", "−").replace("-", "−"), color=color, fontsize=15, fontweight="bold", va="center")
            x += w + 3
        y = 38
        for name, color, items in sections:
            ax.text(5, y, name, color=color, fontsize=11, fontweight="bold", va="center")
            y += 4.2
            for t, pct in items:
                ax.text(5, y, f"${t}", color=_TEXT, fontsize=11, va="center")
                bw = max(abs(pct) / max_abs * 52, 1.5)
                ax.add_patch(Rectangle((30, y - 1.5), bw, 3, facecolor=color, alpha=0.85, linewidth=0))
                ax.text(95, y, f"{pct:+.1f}%".replace("-", "−").replace("-", "−"), color=color, fontsize=11, fontweight="bold", ha="right", va="center")
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
