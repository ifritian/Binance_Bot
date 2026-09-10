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
import math
import random
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

# Неоновая палитра (по референсу пользователя - коктейль на тёмно-синем
# фоне): циан для роста, маджента для падения - вместо стандартных
# Binance-цветов (#0ECB81/#F6465D).
_UP_COLOR = "#29ABE2"
_DOWN_COLOR = "#E4007A"
_BG_COLOR = "#0B0E11"
_GRID_COLOR = "#1E2329"
_AXIS_TEXT_COLOR = "#848E9C"
_WATERMARK_COLOR = "#FFFFFF"

# Цвета MA-линий. MA25 намеренно НЕ маджента/розовый (как раньше) -
# на фоне такого же по тону down-цвета свечей (#E4007A) линия бы
# сливалась с телами свечей и переставала читаться. Светлый
# нейтральный цвет держит контраст с обоими цветами свечей сразу.
_MA_COLORS = {7: "#F0B90B", 25: "#E8E8E8", 99: "#7B61FF"}

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


def symbol_exists(ticker: str) -> bool:
    """Быстрая проверка (без скачивания свечей - один короткий запрос
    цены), торгуется ли SYMBOLUSDT на Binance вообще.

    Нужна, чтобы отсеивать сигналы для тикеров НЕ с Binance ещё на
    этапе парсинга (main.check_for_new_signals), а не только когда
    generate_chart_image уже упадёт с 400 в момент публикации. Такие
    сигналы иногда приходят от стороннего бота (@syndicateproobot) -
    например, однобуквенный тикер вроде \"O\", который на самом деле
    NYSE-акция, а не крипто-пара, и на Binance его, конечно, нет.

    При сетевой ошибке ИЛИ неожиданном статусе - возвращает True (не
    блокируем сигнал из-за временного сбоя самой проверки, а не
    реального отсутствия пары); False - только при точном 400/404 от
    Binance, означающем \"такого символа не существует\"."""
    clean_ticker = ticker.replace("USDT", "").upper()
    symbol = f"{clean_ticker}USDT"

    try:
        resp = requests.get(f"{_BASE_URL}/ticker/price", params={"symbol": symbol}, timeout=10)
    except requests.RequestException as e:
        logger.warning("Не удалось проверить существование пары %s: %s - пропускаю проверку, разрешаю", symbol, e)
        return True

    if resp.status_code in (400, 404):
        logger.info("Пара %s не торгуется на Binance (статус %s) - тикер будет отсеян", symbol, resp.status_code)
        return False

    if not resp.ok:
        logger.warning("Неожиданный статус %s при проверке пары %s - пропускаю проверку, разрешаю", resp.status_code, symbol)
        return True

    return True


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
    """Объём снизу - столбики цветом по свече (рост/падение), с отступом
    сверху (чтобы самый высокий бар не упирался в границу панели) и
    тонкой горизонтальной сеткой на паре уровней в цвет сетки основного
    графика - без этого плоские полупрозрачные бруски выглядели как
    отдельная, "приклеенная" диаграмма, а не органичное продолжение
    свечного графика, как на настоящих биржевых терминалах."""
    width = 0.6
    volumes = [c["volume"] for c in candles]
    max_vol = max(volumes) if volumes else 1.0

    for i, c in enumerate(candles):
        color = _UP_COLOR if c["close"] >= c["open"] else _DOWN_COLOR
        ax.add_patch(Rectangle((i - width / 2, 0), width, c["volume"], color=color, alpha=0.55, linewidth=0))

    # Отступ сверху ~25% - бары "дышат", не упираются в край панели
    ax.set_ylim(0, max_vol * 1.25)

    # Лёгкая горизонтальная сетка на 2 уровнях - визуально связывает
    # панель объёма с основным графиком (та же сетка, тот же цвет).
    for frac in (0.25, 0.75):
        ax.axhline(max_vol * frac, color=_GRID_COLOR, linewidth=0.5, zorder=0)


def _draw_watermark(ax, text: str | None = "BINANCE") -> None:
    """Полупрозрачный ромб + надпись по центру графика, как водяной
    знак на скриншотах с самой площадки. text=None - не рисовать
    вообще (используется для графиков под чужие площадки, где
    водяной знак BINANCE неуместен)."""
    if text is None:
        return
    ax.text(
        0.5, 0.52, "◆", transform=ax.transAxes, ha="center", va="center",
        fontsize=46, color=_WATERMARK_COLOR, alpha=0.05, zorder=0,
    )
    ax.text(
        0.5, 0.46, text, transform=ax.transAxes, ha="center", va="center",
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


def _draw_percent_tag(ax, value: float, color: str) -> None:
    """Плашка со значением в % у правого края графика - тот же стиль,
    что и _draw_price_tag (цена), но для % вместо абсолютной цены (см.
    generate_cumulative_pnl_chart)."""
    ax.annotate(
        f"{value:+.2f}%",
        xy=(1.0, value), xycoords=("axes fraction", "data"),
        xytext=(8, 0), textcoords="offset points",
        ha="left", va="center", fontsize=8.5, color="#0B0E11", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=color, edgecolor="none"),
        annotation_clip=False, zorder=5,
    )


def generate_cumulative_pnl_chart(records: list[dict], out_name: str = "weekly_digest_pnl") -> Path | None:
    """Линейный график накопленного результата по закрытым сигналам
    (C1 в роадмапе: "визуальное подтверждение результата воспринимается
    убедительнее текстовых цифр"), в том же визуальном стиле, что и
    generate_chart_image (тёмный фон, водяной знак, плашка со значением
    справа) - чтобы смотрелось частью той же серии графиков, а не
    отдельным, чужеродным типом картинки.

    records - список словарей с полями "closed_at" (unix-время закрытия)
    и "pnl_pct" (% результат конкретного сигнала) - обычно
    queue_manager.get_closed_outcomes(), уже отфильтрованный по периоду
    вызывающим кодом (см. accuracy_report_generator.generate_accuracy_report_post).
    Сортируются по closed_at здесь же - порядок на входе не важен.

    ВАЖНО про смысл кривой: это ПРОСТАЯ СУММА pnl_pct по сигналам одного
    за другим ("что было бы, если бы на каждый сигнал ставилась примерно
    одна и та же доля капитала, БЕЗ реинвестирования прибыли") - та же
    намеренно простая договорённость, что уже используется в
    outcome_tracker.get_accuracy_stats (avg_pnl_pct - обычное среднее, не
    взвешенное и не сложный процент). Это НЕ график реального баланса
    счёта - подпись на графике явно говорит "сумма % результата", а не
    "доходность депозита", чтобы не создавать ложное впечатление
    точности там, где её нет.

    Возвращает None, если точек меньше 2 (линию из одной точки рисовать
    не за чем - как и generate_chart_image, который тоже требует
    минимум 2 свечи)."""
    if len(records) < 2:
        logger.info("Недостаточно точек (%d) для кумулятивного графика PnL - пропускаю", len(records))
        return None

    sorted_records = sorted(records, key=lambda r: r.get("closed_at", 0))
    cumulative: list[float] = []
    running = 0.0
    for r in sorted_records:
        running += r.get("pnl_pct", 0) or 0.0
        cumulative.append(running)

    xs = list(range(len(cumulative)))
    final = cumulative[-1]
    line_color = _UP_COLOR if final >= 0 else _DOWN_COLOR

    _CHARTS_DIR.mkdir(exist_ok=True)
    out_path = _CHARTS_DIR / f"{out_name}.png"

    fig = plt.figure(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor(_BG_COLOR)
    ax = fig.add_axes((0.05, 0.12, 0.87, 0.68))

    _draw_watermark(ax)
    ax.axhline(0, color=_GRID_COLOR, linewidth=0.8, zorder=1)
    ax.fill_between(xs, cumulative, 0, color=line_color, alpha=0.12, zorder=2)
    ax.plot(xs, cumulative, color=line_color, linewidth=1.8, zorder=3)
    _style_axis(ax, show_xticks=False)
    ax.set_xlim(0, len(cumulative) - 1)
    _draw_percent_tag(ax, final, line_color)

    fig.text(0.05, 0.94, "Накопленный результат сигналов", color="white", fontsize=15, fontweight="bold", va="top")
    fig.text(
        0.05, 0.895,
        f"{len(cumulative)} закрытых сигналов подряд, сумма % результата: {final:+.2f}%",
        color=line_color, fontsize=10, fontweight="bold", va="top",
    )

    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)

    logger.info("Кумулятивный график PnL сохранён (%d точек, итог %+.2f%%): %s", len(cumulative), final, out_path)
    return out_path


# Цвет рукописных пометок - тёплый жёлтый (не совпадает ни с _UP_COLOR/
# _DOWN_COLOR свечей, ни с MA-линиями), как маркер/выделение поверх
# графика, а не часть самих данных.
_ANNOTATION_COLOR = "#F0B90B"


def _wobbly_ellipse_points(cx: float, cy: float, rx: float, ry: float,
                            n: int = 60, wobble: float = 0.10,
                            rng: random.Random | None = None) -> tuple[list[float], list[float]]:
    """Точки эллипса вокруг (cx, cy) с плавным "дрожанием" радиуса -
    имитация линии, обведённой от руки маркером, а не идеальной
    геометрической фигуры (как рисует ax.add_patch(Ellipse(...))).

    Дрожание строится не через независимый шум на КАЖДОЙ точке (это
    дало бы рваную, "пиксельную" линию), а через небольшое число
    опорных точек (control points) по кругу с линейной интерполяцией
    между ними - так колебания получаются плавными, как у настоящей
    руки, а не как шум.
    """
    rng = rng or random.Random()
    n_control = 8
    controls = [rng.uniform(1 - wobble, 1 + wobble) for _ in range(n_control)]
    controls.append(controls[0])  # замыкаем кольцо без разрыва в стыке

    xs: list[float] = []
    ys: list[float] = []
    for i in range(n + 1):
        t = i / n
        angle = t * 2 * math.pi
        pos = t * n_control
        idx = int(pos) % n_control
        local_t = pos - int(pos)
        r_mult = controls[idx] * (1 - local_t) + controls[idx + 1] * local_t
        xs.append(cx + rx * r_mult * math.cos(angle))
        ys.append(cy + ry * r_mult * math.sin(angle))
    return xs, ys


def _wobbly_line_points(x0: float, y0: float, x1: float, y1: float,
                         n: int = 16, wobble: float = 0.08,
                         rng: random.Random | None = None) -> tuple[list[float], list[float]]:
    """Точки отрезка от (x0,y0) до (x1,y1) со сглаженным поперечным
    дрожанием (перпендикулярно направлению линии) - тот же приём "рука
    дрожит плавно, не рвано", что и в _wobbly_ellipse_points, только
    вдоль прямой, а не по кругу. Используется для стрелок и штрихов
    галочки."""
    rng = rng or random.Random()
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    # Смещение по перпендикуляру задаём в единицах длины самой линии,
    # чтобы короткие штрихи (галочка) дрожали меньше в абсолютных
    # величинах, чем длинные (стрелка через весь график).
    amp = length * wobble
    perp_x, perp_y = -(y1 - y0) / length, (x1 - x0) / length

    n_control = 4
    controls = [rng.uniform(-amp, amp) for _ in range(n_control)]

    xs: list[float] = []
    ys: list[float] = []
    for i in range(n + 1):
        t = i / n
        base_x = x0 + (x1 - x0) * t
        base_y = y0 + (y1 - y0) * t
        pos = t * (n_control - 1)
        idx = min(int(pos), n_control - 2)
        local_t = pos - idx
        offset = controls[idx] * (1 - local_t) + controls[idx + 1] * local_t
        xs.append(base_x + perp_x * offset)
        ys.append(base_y + perp_y * offset)
    return xs, ys


def _nearest_candle_index(candles: list[dict], target_time_ms: float) -> int | None:
    """Индекс свечи, чей open_time ближе всего к target_time_ms, либо
    None, если target_time_ms дальше от края диапазона свечей, чем шаг
    между свечами (например, вход был раньше, чем начинается сам
    график) - в этом случае лучше не рисовать пометку в случайном
    месте, а пропустить её."""
    if not candles:
        return None

    best_i, best_diff = 0, abs(candles[0]["open_time"] - target_time_ms)
    for i, c in enumerate(candles):
        diff = abs(c["open_time"] - target_time_ms)
        if diff < best_diff:
            best_i, best_diff = i, diff

    if len(candles) >= 2:
        step = candles[1]["open_time"] - candles[0]["open_time"]
    else:
        step = 0
    # Запас в один шаг свечи - вход мог случиться чуть раньше первой
    # свечи на графике (границы диапазона не совпадают ровно), это ещё
    # нормально привязать к первой свече, а не отбрасывать пометку.
    if step and best_diff > step * 1.5:
        return None
    return best_i


def draw_win_annotations(ax, candles: list[dict], entry_price: float, entry_time: float,
                          exit_price: float, seed: int | None = None) -> bool:
    """Рисует рукописные пометки формата "Забрали профит!": обведённая
    от руки точка входа + галочка у точки выхода. Возвращает True, если
    что-то нарисовано (вызывающий код может это игнорировать - пометка
    декоративная, отсутствие не должно ронять публикацию).

    entry_time - unix-время в СЕКУНДАХ (как record["published_at"] у
    outcome_tracker) - переводится в мс здесь же, чтобы вызывающему
    коду не нужно было об этом помнить.
    """
    rng = random.Random(seed)
    entry_idx = _nearest_candle_index(candles, entry_time * 1000)
    if entry_idx is None:
        logger.info("Точка входа вне диапазона графика - пометки 'вход' не будет, только галочка у выхода")

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price_span = max(highs) - min(lows) or 1.0
    x_span = len(candles) or 1

    drew_something = False

    if entry_idx is not None:
        rx = max(x_span * 0.045, 1.2)
        ry = price_span * 0.09
        ex, ey = _wobbly_ellipse_points(entry_idx, entry_price, rx, ry, rng=rng)
        ax.plot(ex, ey, color=_ANNOTATION_COLOR, linewidth=2.0, alpha=0.9,
                solid_capstyle="round", solid_joinstyle="round", zorder=6)
        drew_something = True

    # Галочка (✓) у точки выхода - два штриха от руки: короткий вниз-
    # вправо, затем длинный вверх-вправо, классическая форма "check".
    exit_idx = len(candles) - 1
    check_w = x_span * 0.05
    check_h = price_span * 0.07
    cx, cy = exit_idx + x_span * 0.03, exit_price

    short_x, short_y = _wobbly_line_points(
        cx - check_w * 0.9, cy - check_h * 0.05,
        cx - check_w * 0.35, cy - check_h * 0.55,
        n=8, wobble=0.12, rng=rng,
    )
    long_x, long_y = _wobbly_line_points(
        cx - check_w * 0.35, cy - check_h * 0.55,
        cx + check_w * 0.65, cy + check_h * 0.55,
        n=8, wobble=0.10, rng=rng,
    )
    ax.plot(short_x, short_y, color=_ANNOTATION_COLOR, linewidth=2.4, alpha=0.95,
            solid_capstyle="round", zorder=6, clip_on=False)
    ax.plot(long_x, long_y, color=_ANNOTATION_COLOR, linewidth=2.4, alpha=0.95,
            solid_capstyle="round", zorder=6, clip_on=False)
    drew_something = True

    return drew_something


def draw_signal_annotations(ax, candles: list[dict], entry_price: float, direction: str,
                             seed: int | None = None) -> bool:
    """Рисует рукописную пометку у зоны входа для СВЕЖЕГО (ещё не
    закрытого) сигнала: обведённая от руки точка + стрелка в сторону
    сделки (вверх для long, вниз для short). В отличие от
    draw_win_annotations - без галочки, результат ещё не известен."""
    if not candles:
        return False

    rng = random.Random(seed)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price_span = max(highs) - min(lows) or 1.0
    x_span = len(candles) or 1

    idx = len(candles) - 1
    rx = max(x_span * 0.045, 1.2)
    ry = price_span * 0.09
    ex, ey = _wobbly_ellipse_points(idx, entry_price, rx, ry, rng=rng)
    ax.plot(ex, ey, color=_ANNOTATION_COLOR, linewidth=2.0, alpha=0.9,
            solid_capstyle="round", solid_joinstyle="round", zorder=6, clip_on=False)

    is_short = str(direction).lower() == "short"
    arrow_len = price_span * 0.16
    ax_x = idx + x_span * 0.06
    # На графике выше цена = выше по оси Y, поэтому для long (рост)
    # наконечник должен быть ВЫШЕ entry_price, а для short (падение) -
    # НИЖЕ. (Раньше знаки были перепутаны, и стрелка "long" указывала
    # вниз - см. рендер-тест, поймавший это визуально.)
    y0 = entry_price + (-arrow_len * 0.4 if is_short else arrow_len * 0.4)
    y1 = entry_price + (-arrow_len if is_short else arrow_len)
    shaft_x, shaft_y = _wobbly_line_points(ax_x, y0, ax_x, y1, n=10, wobble=0.10, rng=rng)
    ax.plot(shaft_x, shaft_y, color=_ANNOTATION_COLOR, linewidth=2.2, alpha=0.9,
            solid_capstyle="round", zorder=6, clip_on=False)

    # Наконечник стрелки - два коротких штриха от кончика назад,
    # V-образно, тем же "дрожащим" стилем.
    head = price_span * 0.045
    tip_x, tip_y = ax_x, y1
    side = -head * 1.1 if is_short else head * 1.1
    for dx_sign in (-1, 1):
        hx, hy = _wobbly_line_points(
            tip_x, tip_y, tip_x + head * 0.9 * dx_sign, tip_y + side,
            n=6, wobble=0.15, rng=rng,
        )
        ax.plot(hx, hy, color=_ANNOTATION_COLOR, linewidth=2.2, alpha=0.9,
                solid_capstyle="round", zorder=6, clip_on=False)

    return True


def generate_chart_image(ticker: str, days: int = 2, expected_price: float | None = None,
                          watermark_text: str | None = "BINANCE", filename_suffix: str = "",
                          win_annotation: dict | None = None,
                          signal_annotation: dict | None = None) -> Path | None:
    """
    Возвращает путь к PNG со свечным графиком тикера (MA7/25/99 +
    объём снизу + водяной знак, в стиле самого Binance), либо None.

    watermark_text - см. _draw_watermark (None - без водяного знака,
    для графиков под чужие площадки).
    filename_suffix - добавляется к имени файла (например "_okx"),
    чтобы графики под разные площадки для одного тикера не
    перезаписывали друг друга при параллельной генерации.

    win_annotation - {"entry_price", "entry_time"} - если передан,
    поверх графика рисуются рукописные пометки "Забрали профит!" (см.
    draw_win_annotations): круг у входа + галочка у выхода. Только для
    закрытых сделок, вызывающий код (main._publish_win_celebrations)
    сам решает, включать ли (см. config.WIN_ANNOTATION_PROBABILITY).

    signal_annotation - {"entry_price", "direction"} - то же самое, но
    для свежего сигнала (см. draw_signal_annotations): круг у зоны
    входа + стрелка по направлению сделки, без галочки. Оба параметра
    не предполагается передавать одновременно.
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
    out_path = _CHARTS_DIR / f"{ticker}{filename_suffix}_chart.png"

    fig = plt.figure(figsize=(8, 5), dpi=150)
    fig.patch.set_facecolor(_BG_COLOR)
    gs = fig.add_gridspec(2, 1, height_ratios=(3.2, 1), hspace=0.05, left=0.04, right=0.90, top=0.83, bottom=0.08)
    ax_price = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_price)

    _draw_watermark(ax_price, watermark_text)
    _draw_candles(ax_price, candles)
    _draw_moving_averages(ax_price, candles)
    _style_axis(ax_price, show_xticks=False)
    ax_price.set_xlim(-1, len(candles))
    _draw_price_tag(ax_price, last_close, header_color)

    if win_annotation is not None:
        try:
            draw_win_annotations(
                ax_price, candles,
                entry_price=float(win_annotation["entry_price"]),
                entry_time=float(win_annotation["entry_time"]),
                exit_price=last_close,
            )
        except Exception:
            # Пометка декоративная - если что-то пошло не так (плохие
            # входные данные и т.п.), график всё равно должен уйти в
            # публикацию без пометок, а не сорваться целиком.
            logger.exception("Не удалось нарисовать пометки 'Забрали профит!' для %s", ticker)
    elif signal_annotation is not None:
        try:
            draw_signal_annotations(
                ax_price, candles,
                entry_price=float(signal_annotation["entry_price"]),
                direction=signal_annotation.get("direction", "long"),
            )
        except Exception:
            logger.exception("Не удалось нарисовать пометку входа для %s", ticker)

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