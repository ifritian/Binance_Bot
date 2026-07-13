"""
treasury_heatmap.py - "Тепловая карта" (визуал, каждый пост Treasury Index).

Визуальная замена трёх текстовых строк с процентами по каждой монете
(см. treasury_index.format_index_block) - вместо чтения 15 чисел
подряд, одна картинка: плашка на монету, сгруппированные по тирам
рядами, цвет и интенсивность = сила и направление движения за период.
Читается за пару секунд, а не построчным разбором текста.

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
_GRID_COLOR = "#1E2329"
_UP_COLOR = "#29ABE2"
_DOWN_COLOR = "#E4007A"
_MISSING_COLOR = "#2B2F36"
_TEXT_COLOR = "#FFFFFF"
_MUTED_TEXT_COLOR = "#848E9C"

_TIER_ACCENT = {"tier1": "#29ABE2", "tier2": "#F0B90B", "tier3": "#E4007A"}

# Движение сильнее этого % насыщает цвет плашки полностью - без этого
# один резкий выброс (см. "suspicious" в CoinChange) обесцветил бы всю
# остальную карту рядом с собой на фоне бледных обычных плашек.
_COLOR_SATURATION_PCT = 8.0


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


def generate_treasury_heatmap(result) -> Optional[Path]:
    """Рисует тепловую карту по всем монетам result.tiers и сохраняет в
    PNG, перезаписывая предыдущий файл. Возвращает None при ошибке
    построения - вызывающий код публикует пост без картинки, не
    блокируется."""
    if not result.tiers:
        return None

    try:
        max_coins = max((len(t.coins) for t in result.tiers), default=0)
        if max_coins == 0:
            return None

        n_rows = len(result.tiers)
        fig, ax = plt.subplots(figsize=(2.4 * max_coins + 1.5, 1.9 * n_rows + 1.0), dpi=150)
        fig.patch.set_facecolor(_BG_COLOR)
        ax.set_facecolor(_BG_COLOR)

        for row, tier in enumerate(result.tiers):
            y = n_rows - 1 - row  # tier1 сверху

            tier_label = tier.label.split(" ", 1)[-1] if " " in tier.label else tier.label
            tier_pct_str = f"{tier.pct:+.2f}%" if tier.pct is not None else "н/д"
            ax.text(
                -0.15, y + 0.5, f"{tier_label}\n{tier_pct_str}",
                ha="right", va="center", fontsize=11, fontweight="bold",
                color=_TIER_ACCENT.get(tier.key, _TEXT_COLOR),
            )

            for col, coin in enumerate(tier.coins):
                color = _tile_color(coin.pct)
                rect = FancyBboxPatch(
                    (col + 0.06, y + 0.06), 0.88, 0.88,
                    boxstyle="round,pad=0,rounding_size=0.08",
                    linewidth=1.2 if coin.suspicious else 0,
                    edgecolor="#FFD700" if coin.suspicious else "none",
                    facecolor=color,
                )
                ax.add_patch(rect)

                pct_str = f"{coin.pct:+.1f}%" if coin.pct is not None else "н/д"
                marker = " ⚠️" if coin.suspicious else ""
                ax.text(
                    col + 0.5, y + 0.56, f"${coin.ticker}",
                    ha="center", va="center", fontsize=10.5, fontweight="bold", color=_TEXT_COLOR,
                )
                ax.text(
                    col + 0.5, y + 0.32, f"{pct_str}{marker}",
                    ha="center", va="center", fontsize=9, color=_TEXT_COLOR,
                )

        ax.set_xlim(-0.05 * max_coins - 1.3, max_coins)
        ax.set_ylim(0, n_rows)
        ax.axis("off")

        total_str = f"{result.total_pct:+.2f}%" if result.total_pct is not None else "н/д"
        ax.set_title(
            f"Treasury Index за {result.period_hours:g}ч: {total_str}",
            color=_TEXT_COLOR, fontsize=13, fontweight="bold", pad=14, loc="left",
        )

        _CHARTS_DIR.mkdir(exist_ok=True)
        fig.tight_layout()
        fig.savefig(_OUT_PATH, facecolor=fig.get_facecolor())
        plt.close(fig)
    except Exception:
        logger.exception("Не удалось построить тепловую карту Treasury Index")
        return None

    logger.info("Сгенерирована тепловая карта Treasury Index")
    return _OUT_PATH
