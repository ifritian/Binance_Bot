"""
treasury_heatmap.py - "Тепловая карта" (визуал, каждый пост Treasury Index).

Визуальная замена трёх текстовых строк с процентами по каждой монете
(см. treasury_index.format_index_block) - вместо чтения 15 чисел
подряд, одна картинка в стиле привычных крипто-хитмапов (CoinMarketCap
и т.п.): площадь плашки = вес монеты в индексе (не все монеты равны -
SOL с весом 20% занимает в 13 раз больше места, чем PENDLE с весом
1.5%), а цвет/интенсивность = сила и направление движения за период.
Читается за пару секунд - сразу видно, что здесь БОЛЬШОЕ и что двигалось
СИЛЬНЕЕ, а не только "что выросло, а что упало".

Раньше (до этой версии) карта была равномерной сеткой - все 15 плашек
одного размера, сгруппированные по тирам строками. Технически понятно,
но визуально скучно и не показывает, что вес монет в индексе сильно
разный. Теперь используется squarified treemap (Bruls/Huizing/van Wijk,
2000) - стандартный алгоритм для "плиточных" визуализаций с площадью
пропорциональной значению, без сторонней библиотеки (реализация ниже,
~40 строк, только stdlib/matplotlib).

Публикуется КАЖДЫЙ пост Treasury Index (см. main.try_publish_treasury_post
через treasury_generator.generate_treasury_post) - в отличие от
treasury_composition_chart.py (диаграмма состава), которая появляется
периодически, не каждый раз.

Стиль - тот же тёмный, что и у остальных графиков бота (chart_generator.py,
treasury_chart.py), для визуальной консистентности.
"""
import logging
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

import config

logger = logging.getLogger(__name__)

_CHARTS_DIR = config.BASE_DIR / "charts"
_OUT_PATH = _CHARTS_DIR / "treasury_heatmap.png"

_BG_COLOR = "#0B0E11"
_UP_COLOR = "#29ABE2"
_DOWN_COLOR = "#E4007A"
_MISSING_COLOR = "#2B2F36"
_TEXT_COLOR = "#FFFFFF"
_MUTED_TEXT_COLOR = "#848E9C"

_TIER_ACCENT = {"tier1": "#29ABE2", "tier2": "#F0B90B", "tier3": "#E4007A"}

# Холст в условных единицах - ширина/высота выбраны под соотношение
# сторон ~2:1 (близко к типичным крипто-хитмапам, удобно для ленты
# Binance Square/Telegram). Сумма весов всех 15 монет всегда = 100
# (см. treasury_index.py, проверяется assert'ом при импорте), поэтому
# каждая единица веса = _CANVAS_W * _CANVAS_H / 100 площади холста.
_CANVAS_W = 100.0
_CANVAS_H = 50.0

# Движение сильнее этого % насыщает цвет плашки полностью - без этого
# один резкий выброс (см. "suspicious" в CoinChange) обесцветил бы всю
# остальную карту рядом с собой на фоне бледных обычных плашек.
_COLOR_SATURATION_PCT = 8.0

# Тонкий зазор между плашками (в тех же условных единицах холста) -
# создаёт эффект сетки, как в примере, вместо плашек впритык друг к другу.
_TILE_GAP = 0.45


# ============================================================
# Squarified treemap - раскладка прямоугольников площадью,
# пропорциональной значениям, с раскладкой, стремящейся к квадратным
# (не вытянутым в полоску) плашкам - см. Bruls M., Huizing K.,
# van Wijk J.J. "Squarified Treemaps" (2000).
# ============================================================

def _normalize_sizes(sizes: list, dx: float, dy: float) -> list:
    """Масштабирует sizes так, чтобы их сумма точно совпала с площадью
    холста dx*dy - без этого раскладка "не дотянет" до краёв или
    вылезет за них."""
    total = sum(sizes)
    area = dx * dy
    return [s * area / total for s in sizes]


def _layout_row(sizes: list, x: float, y: float, dx: float, dy: float) -> list:
    """Раскладывает sizes в один ряд вдоль КОРОТКОЙ стороны текущей
    свободной области - если сторона по x короче (dx < dy), ряд идёт
    вертикальной полосой (постоянная ширина, монеты друг под другом),
    иначе горизонтальной полосой (постоянная высота, монеты друг
    за другом)."""
    covered = sum(sizes)
    if dx >= dy:
        # свободная область "широкая" - ряд идёт горизонтальной полосой
        # фиксированной высоты у левого края
        width = covered / dy
        rects, cx = [], x
        for s in sizes:
            h = s / width
            rects.append((cx, y, width, h))
            cx += width
        return rects
    # свободная область "высокая" - ряд идёт вертикальной полосой
    # фиксированной ширины у верхнего края
    height = covered / dx
    rects, cy = [], y
    for s in sizes:
        w = s / height
        rects.append((x, cy, w, height))
        cy += height
    return rects


def _leftover(sizes: list, x: float, y: float, dx: float, dy: float) -> tuple:
    """Область, оставшаяся свободной ПОСЛЕ того, как sizes уложены
    текущим рядом (см. _layout_row) - именно в неё укладывается
    следующий ряд при рекурсии."""
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy
        return (x + width, y, dx - width, dy)
    height = covered / dx
    return (x, y + height, dx, dy - height)


def _worst_ratio(sizes: list, x: float, y: float, dx: float, dy: float) -> float:
    """Худшее (максимальное) отношение сторон среди прямоугольников,
    которые получатся, если уложить sizes текущим рядом - чем ближе к
    1.0, тем "квадратнее" плашки. Squarify на каждом шаге сравнивает
    это значение до/после добавления следующего элемента в ряд, чтобы
    решить, стоит ли его добавлять (см. squarify ниже)."""
    rects = _layout_row(sizes, x, y, dx, dy)
    return max(max(w / h, h / w) for _, _, w, h in rects)


def _squarify(sizes: list, x: float, y: float, dx: float, dy: float) -> list:
    """Возвращает список (x, y, w, h) в ТОМ ЖЕ порядке, что и sizes.
    sizes должны быть отсортированы по убыванию (не обязательно строго,
    но так раскладка получается заметно аккуратнее) и в сумме давать
    dx*dy (см. _normalize_sizes)."""
    sizes = [s for s in sizes if s > 0]
    if not sizes:
        return []
    if len(sizes) == 1:
        return _layout_row(sizes, x, y, dx, dy)

    # Наращиваем текущий ряд, пока добавление следующего элемента
    # улучшает (уменьшает) худшее соотношение сторон - как только
    # начинает ухудшаться, закрываем ряд и уходим в оставшуюся область.
    i = 1
    while i < len(sizes) and _worst_ratio(sizes[:i], x, y, dx, dy) >= _worst_ratio(sizes[:i + 1], x, y, dx, dy):
        i += 1

    current, remaining = sizes[:i], sizes[i:]
    rects = _layout_row(current, x, y, dx, dy)
    nx, ny, ndx, ndy = _leftover(current, x, y, dx, dy)
    rects.extend(_squarify(remaining, nx, ny, ndx, ndy))
    return rects


def _tile_color(pct: Optional[float]) -> tuple:
    if pct is None:
        return mcolors.to_rgb(_MISSING_COLOR)

    clipped = max(min(pct, _COLOR_SATURATION_PCT), -_COLOR_SATURATION_PCT)
    intensity = abs(clipped) / _COLOR_SATURATION_PCT  # 0..1
    base = mcolors.to_rgb(_UP_COLOR if pct >= 0 else _DOWN_COLOR)
    bg = mcolors.to_rgb(_BG_COLOR)
    # Слабое движение - тайл почти сливается с фоном (мало насыщенности,
    # смысл в том, что взгляд сразу цепляется за сильные движения, а не
    # за все 15 плашек одинаково ярко).
    return tuple(bg[i] + (base[i] - bg[i]) * (0.15 + 0.85 * intensity) for i in range(3))


def _flatten_coins(result) -> list:
    """Собирает все монеты всех тиров в один плоский список (тикер,
    вес, %, tier_key, suspicious), отсортированный по весу по убыванию -
    и порядок для эстетики squarify (см. _squarify), и он же естественно
    ставит самые весомые монеты индекса (SOL, AVAX...) в левый верхний
    угол карты, на самые заметные плашки - ровно то же место, которое в
    привычных крипто-хитмапах занимает BTC/ETH."""
    flat = []
    for tier in result.tiers:
        for coin in tier.coins:
            flat.append({
                "ticker": coin.ticker,
                "weight": coin.weight,
                "pct": coin.pct,
                "tier_key": tier.key,
                "suspicious": coin.suspicious,
            })
    flat.sort(key=lambda c: c["weight"], reverse=True)
    return flat


def generate_treasury_heatmap(result) -> Optional[Path]:
    """Рисует тепловую карту-treemap по всем монетам result.tiers и
    сохраняет в PNG, перезаписывая предыдущий файл. Возвращает None при
    ошибке построения - вызывающий код публикует пост без картинки, не
    блокируется."""
    coins = _flatten_coins(result)
    if not coins:
        return None

    try:
        sizes = _normalize_sizes([c["weight"] for c in coins], _CANVAS_W, _CANVAS_H)
        rects = _squarify(sizes, 0.0, 0.0, _CANVAS_W, _CANVAS_H)

        fig, ax = plt.subplots(figsize=(13, 6.8), dpi=150)
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)

        for coin, (x, y, w, h) in zip(coins, rects):
            # Небольшой зазор со всех сторон вместо плашек впритык -
            # но не больше половины меньшей стороны, иначе совсем
            # маленькие плашки (низкий вес) схлопнутся в точку.
            gap = min(_TILE_GAP, w * 0.15, h * 0.15)
            rx, ry, rw, rh = x + gap, y + gap, w - 2 * gap, h - 2 * gap
            if rw <= 0 or rh <= 0:
                continue

            color = _tile_color(coin["pct"])
            rounding = max(min(rw, rh) * 0.06, 0.15)
            rect = FancyBboxPatch(
                (rx, ry), rw, rh,
                boxstyle=f"round,pad=0,rounding_size={rounding}",
                linewidth=1.3 if coin["suspicious"] else 0.8,
                edgecolor="#FFD700" if coin["suspicious"] else _TIER_ACCENT.get(coin["tier_key"], _BG_COLOR),
                facecolor=color,
            )
            ax.add_patch(rect)

            # Размер шрифта и то, что вообще влезает (тикер / тикер+%),
            # зависят от МЕНЬШЕЙ стороны плашки - крупные монеты (SOL,
            # AVAX) получают крупный текст как на образце, мелкие -
            # компактную подпись без потери читаемости.
            short_side = min(rw, rh)
            cx, cy = rx + rw / 2, ry + rh / 2
            pct_str = f"{coin['pct']:+.1f}%" if coin["pct"] is not None else "н/д"
            marker = " ⚠️" if coin["suspicious"] else ""

            if short_side >= 9:
                ticker_fs = min(15 + short_side * 0.9, 34)
                pct_fs = min(9 + short_side * 0.35, 16)
                ax.text(cx, cy + short_side * 0.14, f"${coin['ticker']}",
                        ha="center", va="center", fontsize=ticker_fs, fontweight="bold", color=_TEXT_COLOR)
                ax.text(cx, cy - short_side * 0.22, f"{pct_str}{marker}",
                        ha="center", va="center", fontsize=pct_fs, color=_TEXT_COLOR)
            elif short_side >= 4:
                ax.text(cx, cy + rh * 0.12, f"${coin['ticker']}",
                        ha="center", va="center", fontsize=max(short_side * 1.15, 7),
                        fontweight="bold", color=_TEXT_COLOR)
                ax.text(cx, cy - rh * 0.22, pct_str,
                        ha="center", va="center", fontsize=max(short_side * 0.9, 6), color=_TEXT_COLOR)
            elif short_side >= 1.8:
                # Совсем маленькая плашка (низковесные монеты tier3) -
                # только тикер, без % (не влезет читаемо в обе строки).
                ax.text(cx, cy, coin["ticker"], ha="center", va="center",
                        fontsize=max(short_side * 1.4, 5.5), fontweight="bold", color=_TEXT_COLOR)
            # Ещё меньше - оставляем плашку голой (только цвет), подпись
            # была бы нечитаемой кашей - площадь и цвет уже несут сигнал.

        ax.set_xlim(0, _CANVAS_W)
        ax.set_ylim(0, _CANVAS_H)
        ax.invert_yaxis()  # самые крупные монеты - сверху, как в примере, а не снизу
        ax.axis("off")

        total_str = f"{result.total_pct:+.2f}%" if result.total_pct is not None else "н/д"
        ax.set_title(
            f"Treasury Index за {result.period_hours:g}ч: {total_str}",
            color=_TEXT_COLOR, fontsize=14, fontweight="bold", pad=12, loc="left",
        )

        tier_bits = []
        for tier in result.tiers:
            label = tier.label.split(" ", 1)[-1] if " " in tier.label else tier.label
            tier_pct_str = f"{tier.pct:+.2f}%" if tier.pct is not None else "н/д"
            tier_bits.append(f"{label} {tier_pct_str}")
        if tier_bits:
            fig.text(
                0.01, 0.955, "  ·  ".join(tier_bits),
                color=_MUTED_TEXT_COLOR, fontsize=10, ha="left", va="top",
            )

        _CHARTS_DIR.mkdir(exist_ok=True)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        fig.savefig(_OUT_PATH, facecolor=fig.get_facecolor())
        plt.close(fig)
    except Exception:
        logger.exception("Не удалось построить тепловую карту Treasury Index")
        return None

    logger.info("Сгенерирована тепловая карта Treasury Index (treemap)")
    return _OUT_PATH
