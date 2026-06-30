"""
Генерация графика цены для тикера - японские свечи + MA-линии + объём
снизу, в стиле, максимально похожем на сам Binance (карточка торговой
пары + водяной знак-ромб + плашка с ценой справа).

Данные берутся из того же источника, по которому сканер (scanner.py)
и нашёл сигнал: data-api.binance.vision - публичное зеркало рыночных
данных Binance без авторизации и без гео-ограничений (в отличие от
обычного api.binance.com). Раз сканер увидел этот тикер - значит у
Binance по определению есть свечи по этой паре, так что тикер не
может "не найтись" или внезапно оказаться другой монетой с тем же
символом (раньше график рисовался через CoinGecko, где это бывало).
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
import requests

import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-api.binance.vision/api/v3"
_CHARTS_DIR = config.BASE_DIR / "charts"

# Цветокоррекция: чуть более насыщенные/яркие зелёный и красный, чем
# "сухие" официальные цвета Binance (#0ECB81/#F6465D) - так график
# выглядит живее на скриншоте/в посте, как на примере с цветокором.
_UP_COLOR = "#1FE08A"
_DOWN_COLOR = "#FF4D5E"
_BG_COLOR = "#0B0E11"
_GRID_COLOR = "#1E2329"
_AXIS_TEXT_COLOR = "#848E9C"
_WATERMARK_COLOR = "#FFFFFF"

# Цвета MA-линий - как на самом Binance (MA7 жёлтый, MA25 розовый/
# маджента, MA99 фиолетовый).
_MA_COLORS = {7: "#F0B90B", 25: "#E91E9C", 99: "#7B61FF"}

# Страховка на случай ошибок форматирования тикера и т.п. - на практике
# не должна срабатывать, раз график и сигнал берут данные из одного и
# того же места и по одному и тому же символу.
MAX_PRICE_MISMATCH_RATIO = 3.0

# (interval, limit, формат подписи времени по оси X) для каждого периода.
# Лимит klines у Binance - 1000 свечей за запрос, так что запас большой.
_INTERVAL_BY_DAYS = {
    2: ("1h", 48, "%H:%M"),
    7: ("4h", 42, "%d.%m"),
}


def fetch_klines(ticker: str, days: int = 2) -> list[dict]:
    """Возвращает свечи (open_time, open, high, low, close, volume) с Binance.
    ticker - без USDT (например, "PHB" или "BTC")."""
    clean_ticker = ticker.replace("USDT", "").upper()
    symbol = f"{clean_ticker}USDT"
    interval, limit, _ = _INTERVAL_BY_DAYS.get(days, ("1h", days * 24, "%H:%M"))

    try:
        resp = requests.get(
            f"{_BASE_URL}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.warning("Не удалось получить свечи Binance для графика %s: %s", symbol, e)
        return []

    if not isinstance(rows, list):
        # Binance отвечает {"code": ..., "msg": ...} для несуществующего символа
        logger.warning("Binance вернул неожиданный ответ для графика %s: %s", symbol, rows)
        return []

    try:
        return [
            {
                "open_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
    except (IndexError, ValueError, TypeError) as e:
        logger.warning("Не удалось разобрать свечи Binance для графика %s: %s", symbol, e)
        return []


def _format_price(price: float) -> str:
    if price >= 100:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.6f}".rstrip("0").rstrip(".")


def _moving_average(closes: list[float], period: int) -> list[float | None]:
    """MA как на Binance: точка появляется только когда накопилось
    достаточно свечей для периода (первые period-1 точек - None)."""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(closes[i + 1 - period:i + 1]) / period)
    return out


def _draw_candles(ax, candles: list[dict]) -> None:
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price_span = max(highs) - min(lows) or 1.0
    min_body_height = price_span * 0.0015  # видимая тонкая линия вместо нулевой свечи-доджи

    width = 0.6
    for i, c in enumerate(candles):
        color = _UP_COLOR if c["close"] >= c["open"] else _DOWN_COLOR
        ax.plot([i, i], [c["low"], c["high"]], color=color, linewidth=1, solid_capstyle="round")
        body_low = min(c["open"], c["close"])
        body_height = max(abs(c["close"] - c["open"]), min_body_height)
        ax.add_patch(Rectangle((i - width / 2, body_low), width, body_height, color=color, linewidth=0))


def _draw_moving_averages(ax, candles: list[dict]) -> None:
    closes = [c["close"] for c in candles]
    for period, color in _MA_COLORS.items():
        if len(closes) < period:
            continue  # недостаточно данных для этого периода - просто не рисуем линию
        ma = _moving_average(closes, period)
        xs = [i for i, v in enumerate(ma) if v is not None]
        ys = [v for v in ma if v is not None]
        ax.plot(xs, ys, color=color, linewidth=1.1, alpha=0.9, zorder=3)


def _draw_volume(ax, candles: list[dict]) -> None:
    width = 0.6
    for i, c in enumerate(candles):
        color = _UP_COLOR if c["close"] >= c["open"] else _DOWN_COLOR
        ax.add_patch(Rectangle((i - width / 2, 0), width, c["volume"], color=color, alpha=0.4, linewidth=0))


def _draw_watermark(ax) -> None:
    """Полупрозрачный ромб + надпись BINANCE по центру графика, как
    водяной знак на скриншотах с самой площадки."""
    ax.text(
        0.5, 0.52, "◆", transform=ax.transAxes, ha="center", va="center",
        fontsize=46, color=_WATERMARK_COLOR, alpha=0.05, zorder=0,
    )
    ax.text(
        0.5, 0.46, "BINANCE", transform=ax.transAxes, ha="center", va="center",
        fontsize=20, color=_WATERMARK_COLOR, alpha=0.05, fontweight="bold",
        family="monospace", zorder=0,
    )


def _draw_price_tag(ax, price: float, color: str) -> None:
    """Плашка с текущей ценой у правого края графика, на уровне
    последнего закрытия - как "Last Price" бирка на самом Binance."""
    ax.annotate(
        _format_price(price),
        xy=(1.0, price), xycoords=("axes fraction", "data"),
        xytext=(8, 0), textcoords="offset points",
        ha="left", va="center", fontsize=8.5, color="#0B0E11", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=color, edgecolor="none"),
        annotation_clip=False, zorder=5,
    )


def _style_axis(ax, show_xticks: bool = False) -> None:
    ax.set_facecolor(_BG_COLOR)
    ax.tick_params(colors=_AXIS_TEXT_COLOR, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(_GRID_COLOR)
    ax.grid(color=_GRID_COLOR, linewidth=0.6, axis="y")
    ax.yaxis.tick_right()
    if not show_xticks:
        ax.set_xticks([])


def generate_chart_image(ticker: str, days: int = 2, expected_price: float | None = None) -> Path | None:
    """
    Возвращает путь к PNG со свечным графиком тикера (MA7/25/99 +
    объём снизу + водяной знак, в стиле самого Binance), либо None.
    """
    try:
        candles = fetch_klines(ticker, days)
    except Exception as e:
        logger.warning("Ошибка при получении данных для графика %s: %s", ticker, e)
        return None

    if len(candles) < 2:
        logger.warning("Недостаточно данных для графика %s", ticker)
        return None

    last_close = candles[-1]["close"]

    if expected_price is not None and expected_price > 0 and last_close > 0:
        ratio = max(last_close, expected_price) / min(last_close, expected_price)
        if ratio > MAX_PRICE_MISMATCH_RATIO:
            logger.warning(
                "График %s отбракован: последняя цена с Binance (%.10g) сильно "
                "отличается от цены сигнала (%.10g) - %.1fx. Публикация без графика.",
                ticker, last_close, expected_price, ratio,
            )
            return None

    first_open = candles[0]["open"]
    change_pct = (last_close - first_open) / first_open * 100 if first_open else 0.0
    header_color = _UP_COLOR if change_pct >= 0 else _DOWN_COLOR
    arrow = "▲" if change_pct >= 0 else "▼"

    _, _, time_fmt = _INTERVAL_BY_DAYS.get(days, ("1h", days * 24, "%H:%M"))

    _CHARTS_DIR.mkdir(exist_ok=True)
    out_path = _CHARTS_DIR / f"{ticker}_chart.png"

    fig = plt.figure(figsize=(8, 5), dpi=150)
    fig.patch.set_facecolor(_BG_COLOR)
    gs = fig.add_gridspec(2, 1, height_ratios=(3.2, 1), hspace=0.05, left=0.04, right=0.90, top=0.83, bottom=0.08)
    ax_price = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_price)

    _draw_watermark(ax_price)
    _draw_candles(ax_price, candles)
    _draw_moving_averages(ax_price, candles)
    _style_axis(ax_price, show_xticks=False)
    ax_price.set_xlim(-1, len(candles))
    _draw_price_tag(ax_price, last_close, header_color)

    _draw_volume(ax_vol, candles)
    _style_axis(ax_vol, show_xticks=True)
    ax_vol.set_xlim(-1, len(candles))
    ax_vol.set_yticks([])

    tick_count = min(5, len(candles))
    tick_positions = [int(i * (len(candles) - 1) / (tick_count - 1)) for i in range(tick_count)] if tick_count > 1 else [0]
    tick_labels = [
        datetime.fromtimestamp(candles[i]["open_time"] / 1000, tz=timezone.utc).strftime(time_fmt)
        for i in tick_positions
    ]
    ax_vol.set_xticks(tick_positions)
    ax_vol.set_xticklabels(tick_labels)

    # Заголовок в духе карточки монеты на Binance: тикер + текущая цена +
    # изменение за период, цветом по направлению.
    fig.text(0.04, 0.96, f"{ticker}/USDT", color="white", fontsize=15, fontweight="bold", va="top")
    fig.text(
        0.04, 0.915,
        f"{_format_price(last_close)}  {arrow} {change_pct:+.2f}%",
        color=header_color, fontsize=11, fontweight="bold", va="top",
    )
    # Легенда MA-линий, как в шапке графика на Binance.
    last_ma7 = _moving_average([c["close"] for c in candles], 7)[-1] or last_close
    fig.text(
        0.40, 0.918,
        f"MA(7) {_format_price(last_ma7)}",
        color=_MA_COLORS[7], fontsize=8, va="top",
    )
    fig.text(0.04, 0.875, f"{days}d", color=_AXIS_TEXT_COLOR, fontsize=8, va="top")

    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)

    logger.info("График сохранён: %s", out_path)
    return out_path
