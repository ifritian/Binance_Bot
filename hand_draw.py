"""
Рукописные пометки поверх графика: кривые стрелки, кружок, подписи шрифтом
Caveat (fonts/Caveat-Bold.ttf, лицензия OFL). Всё нарочно "неровное" - как будто
человек обвёл и подписал график пальцем на телефоне. Подписи несут смысл
(откуда пошло движение, где вход, где стоп/цель, всплеск объёма), а не украшение.
"""
import logging
import math
import random
from pathlib import Path

from matplotlib import patheffects
from matplotlib.font_manager import FontProperties

logger = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parent / "fonts" / "Caveat-Bold.ttf"
INK = "#FFFFFF"        # белый "маркер" для линий и стрелок
INK2 = "#FCD535"       # золотой для подписей


def _font(size: float) -> FontProperties:
    if FONT_PATH.exists():
        return FontProperties(fname=str(FONT_PATH), size=size)
    return FontProperties(family="DejaVu Sans", style="italic", weight="bold", size=size * 0.8)


class Pen:
    """Рисует в осях ax. Координаты - доли осей (0..1); расчёты углов - в дюймах,
    чтобы стрелки не кособочило из-за разного масштаба по x и y."""

    def __init__(self, fig, ax, rng: random.Random):
        self.fig, self.ax, self.rng = fig, ax, rng
        pos = ax.get_position()
        self.W = pos.width * fig.get_size_inches()[0]
        self.H = pos.height * fig.get_size_inches()[1]
        self.boxes: list = []   # занятые прямоугольники подписей (доли осей)

    # --- примитивы
    def _noise(self, n: int, amp: float) -> list:
        out, v = [], 0.0
        for _ in range(n):
            v = 0.7 * v + self.rng.uniform(-1, 1) * amp
            out.append(v)
        return out

    def _stroke(self, pts_in: list, lw: float = 2.6, color: str = INK, wob: float = 0.012) -> None:
        nx, ny = self._noise(len(pts_in), wob), self._noise(len(pts_in), wob)
        xs = [(x + dx) / self.W for (x, _), dx in zip(pts_in, nx)]
        ys = [(y + dy) / self.H for (_, y), dy in zip(pts_in, ny)]
        line, = self.ax.plot(xs, ys, transform=self.ax.transAxes, color=color, linewidth=lw,
                             solid_capstyle="round", solid_joinstyle="round", zorder=20, clip_on=False)
        line.set_path_effects([patheffects.withStroke(linewidth=lw + 2.2, foreground="#0B0E11", alpha=0.65)])

    def _bezier(self, p0, p1, bend: float, n: int = 26) -> list:
        (x0, y0), (x1, y1) = p0, p1
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy) or 1e-9
        cx, cy = mx - dy / L * bend * L, my + dx / L * bend * L
        pts = []
        for i in range(n):
            t = i / (n - 1)
            pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1,
                        (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1))
        return pts

    def arrow(self, p0_frac, p1_frac) -> None:
        """Кривая стрелка от p0 к p1 (доли осей), наконечник - два штриха."""
        p0 = (p0_frac[0] * self.W, p0_frac[1] * self.H)
        p1 = (p1_frac[0] * self.W, p1_frac[1] * self.H)
        bend = self.rng.choice([-1, 1]) * self.rng.uniform(0.10, 0.22)
        pts = self._bezier(p0, p1, bend)
        self._stroke(pts)
        (xa, ya), (xb, yb) = pts[-4], pts[-1]
        ang = math.atan2(yb - ya, xb - xa)
        for s in (-1, 1):
            a = ang + math.pi + s * math.radians(self.rng.uniform(24, 34))
            ln = self.rng.uniform(0.14, 0.19)
            self._stroke([(xb, yb), (xb + math.cos(a) * ln, yb + math.sin(a) * ln)], wob=0.004)

    def circle(self, c_frac, rx_in: float = 0.22, ry_in: float = 0.2) -> None:
        cx, cy = c_frac[0] * self.W, c_frac[1] * self.H
        start = self.rng.uniform(0, 2 * math.pi)
        turns = 1.12
        n = 44
        pts = []
        for i in range(n):
            t = start + turns * 2 * math.pi * i / (n - 1)
            k = 1 + 0.08 * math.sin(2 * t) + 0.05 * (i / n)
            pts.append((cx + math.cos(t) * rx_in * k, cy + math.sin(t) * ry_in * k))
        self._stroke(pts, wob=0.006)

    def text(self, x: float, y: float, s: str, size: float = 19, color: str = INK2) -> tuple:
        rot = self.rng.uniform(-5, 5)
        t = self.ax.text(x, y, s, transform=self.ax.transAxes, fontproperties=_font(size), color=color,
                         ha="center", va="center", rotation=rot, zorder=21, clip_on=False)
        t.set_path_effects([patheffects.withStroke(linewidth=3.2, foreground="#0B0E11", alpha=0.85)])
        self.fig.canvas.draw()
        bb = t.get_window_extent().transformed(self.ax.transAxes.inverted())
        self.boxes.append((bb.x0, bb.y0, bb.x1, bb.y1))
        return bb.x0, bb.y0, bb.x1, bb.y1


def _free(box, candles_frac, taken) -> bool:
    x0, y0, x1, y1 = box
    if x0 < 0.02 or x1 > 0.98 or y0 < 0.03 or y1 > 0.97:
        return False
    for cx, lo, hi in candles_frac:
        if x0 - 0.01 <= cx <= x1 + 0.01 and lo <= y1 and hi >= y0:
            return False
    for tx0, ty0, tx1, ty1 in taken:
        if not (x1 < tx0 or x0 > tx1 or y1 < ty0 or y0 > ty1):
            return False
    return True


def annotate_setup(fig, ax, ax_vol, candles: list, entry: float, stop: float, target: float,
                   side: str, pct24: float | None = None, seed: str = "") -> None:
    """Пометки для сетапа вход/стоп/цель. Любая ошибка - картинка остаётся без пометок."""
    try:
        _annotate(fig, ax, ax_vol, candles, entry, stop, target, side, pct24, seed)
    except Exception:
        logger.exception("Рукописные пометки не нарисовались")


def _annotate(fig, ax, ax_vol, candles, entry, stop, target, side, pct24, seed):
    rng = random.Random(f"{seed}{entry:.6f}")
    n = len(candles)
    y0, y1 = ax.get_ylim()
    x0, x1 = ax.get_xlim()

    def fx(i): return (i - x0) / (x1 - x0)
    def fy(p): return (p - y0) / (y1 - y0)

    cf = [(fx(i), fy(c["low"]), fy(c["high"])) for i, c in enumerate(candles)]
    pen = Pen(fig, ax, rng)
    long = side == "long"

    def place(anchor, label, size=19, arrow=True, min_d=0.09, max_d=0.42):
        """Подпись в ближайшем свободном месте рядом с точкой anchor (доли осей) + стрелка к ней."""
        ax_, ay_ = anchor
        hw = 0.0105 * len(label) * (size / 19) + 0.012
        hh = 0.05 * (size / 19)
        cands = []
        steps = [i * 0.03 for i in range(-14, 15)]
        for dx in steps:
            for dy in steps:
                d = math.hypot(dx * 0.6, dy)   # по x "дальше" допустимо: график шире, чем выше
                if min_d <= d <= max_d:
                    cands.append((d + rng.uniform(0, 0.05), dx, dy))
        cands.sort()
        for _, dx, dy in cands:
            cx, cy = ax_ + dx, ay_ + dy
            if _free((cx - hw, cy - hh, cx + hw, cy + hh), cf, pen.boxes):
                bb = pen.text(cx, cy, label, size)
                if arrow:
                    bx0, by0, bx1, by1 = bb
                    if ax_ > bx1:
                        sx, sy = bx1 + 0.012, (by0 + by1) / 2 - 0.01
                    elif ax_ < bx0:
                        sx, sy = bx0 - 0.012, (by0 + by1) / 2 - 0.01
                    else:
                        sx, sy = ax_, (by0 - 0.012) if ay_ < by0 else (by1 + 0.012)
                    pen.arrow((sx, sy), (ax_ - (ax_ - sx) * 0.08, ay_ - (ay_ - sy) * 0.08))
                return True
        return False

    # 1) откуда началось движение
    win = candles[-min(30, n):]
    base = n - len(win)
    if long:
        k = min(range(len(win)), key=lambda i: win[i]["low"]); p = win[k]["low"]
    else:
        k = max(range(len(win)), key=lambda i: win[i]["high"]); p = win[k]["high"]
    place((fx(base + k), fy(p)), "move started here")

    # 2) вход - кружок вокруг последней цены
    last = (fx(n - 1), fy(entry))
    pen.circle(last, 0.2, 0.17)
    place(last, "we're here", min_d=0.13)

    # 3) цель и стоп - короткие пометки у линий
    place((0.86, fy(target)), "aim here", size=18, min_d=0.06)
    place((0.86, fy(stop)), "wrong if it breaks", size=17, min_d=0.06)

    # 4) всплеск объёма (если объём настоящий)
    vols = [c.get("volume", 0.0) for c in candles]
    if ax_vol is not None and n > 26 and max(vols) > 0:
        base_v = sum(vols[-26:-4]) / 22
        recent = max(vols[-4:])
        if base_v > 0 and recent > 1.8 * base_v:
            vp = Pen(fig, ax_vol, rng)
            vmax = max(vols)
            bb = vp.text(0.55, 0.62, "volume spike", 17)
            vp.arrow((bb[2] + 0.005, (bb[1] + bb[3]) / 2), (fx(n - 1) - 0.02, min(0.9, recent / vmax * 0.85)))


def annotate_levels(fig, ax, ax_vol, candles: list, lv: dict, seed: str = "") -> None:
    """Пометки для поста "уровни": откуда пошло, максимум/минимум суток, где сейчас, всплеск объёма.
    lv = {"hi": цена, "lo": цена, "now": цена, "up": bool}. Любая ошибка - картинка без пометок."""
    try:
        _annotate_levels(fig, ax, ax_vol, candles, lv, seed)
    except Exception:
        logger.exception("Рукописные пометки (уровни) не нарисовались")


def _annotate_levels(fig, ax, ax_vol, candles, lv, seed):
    rng = random.Random(f"{seed}{lv['now']:.6f}")
    n = len(candles)
    y0, y1 = ax.get_ylim()
    x0, x1 = ax.get_xlim()

    def fx(i): return (i - x0) / (x1 - x0)
    def fy(p): return (p - y0) / (y1 - y0)

    cf = [(fx(i), fy(c["low"]), fy(c["high"])) for i, c in enumerate(candles)]
    pen = Pen(fig, ax, rng)

    def place(anchor, label, size=19, min_d=0.09, max_d=0.42):
        ax_, ay_ = anchor
        hw = 0.0105 * len(label) * (size / 19) + 0.012
        hh = 0.05 * (size / 19)
        steps = [i * 0.03 for i in range(-14, 15)]
        cands = sorted((math.hypot(dx * 0.6, dy) + rng.uniform(0, 0.05), dx, dy)
                       for dx in steps for dy in steps if min_d <= math.hypot(dx * 0.6, dy) <= max_d)
        for _, dx, dy in cands:
            cx, cy = ax_ + dx, ay_ + dy
            if _free((cx - hw, cy - hh, cx + hw, cy + hh), cf, pen.boxes):
                bb = pen.text(cx, cy, label, size)
                bx0, by0, bx1, by1 = bb
                if ax_ > bx1:
                    sx, sy = bx1 + 0.012, (by0 + by1) / 2 - 0.01
                elif ax_ < bx0:
                    sx, sy = bx0 - 0.012, (by0 + by1) / 2 - 0.01
                else:
                    sx, sy = ax_, (by0 - 0.012) if ay_ < by0 else (by1 + 0.012)
                pen.arrow((sx, sy), (ax_ - (ax_ - sx) * 0.08, ay_ - (ay_ - sy) * 0.08))
                return True
        return False

    day = range(max(0, n - 24), n)       # "суточные" максимум/минимум - только последние 24 свечи
    hi_i = max(day, key=lambda i: candles[i]["high"])
    lo_i = min(day, key=lambda i: candles[i]["low"])
    place((fx(hi_i), fy(candles[hi_i]["high"])), "bulls need this")
    place((fx(lo_i), fy(candles[lo_i]["low"])), "bears target this")
    last = (fx(n - 1), fy(lv["now"]))
    pen.circle(last, 0.2, 0.17)
    place(last, "price now", min_d=0.13)
    vols = [c.get("volume", 0.0) for c in candles]
    if ax_vol is not None and n > 26 and max(vols) > 0:
        base_v = sum(vols[-26:-4]) / 22
        if base_v > 0 and max(vols[-4:]) > 1.8 * base_v:
            vp = Pen(fig, ax_vol, rng)
            bb = vp.text(0.55, 0.62, "volume spike", 17)
            vp.arrow((bb[2] + 0.005, (bb[1] + bb[3]) / 2), (fx(n - 1) - 0.02, min(0.9, max(vols[-4:]) / max(vols) * 0.85)))
