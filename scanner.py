"""
Собственный сканер сигналов - замена чужому каналу @resultrsi.

Сканирует ликвидные пары USDT на Binance, считает RSI(14) и Bollinger
Bands(20, 2) на 15-минутных свечах, ищет:
- перекупленность/перепроданность по RSI (>70 / <30)
- касание верхней/нижней полосы Боллинджера
- простую дивергенцию RSI/цены за последние ~30 свечей

Результат - готовые RsiSignal, которые кладутся прямо в очередь бота
(queue_manager.push_pending_signal), без какого-либо Telegram-канала
посередине.

Источник данных: data-api.binance.vision - публичное зеркало рыночных
данных Binance без авторизации и без географических ограничений
обычного api.binance.com (specifically предназначено для случаев,
когда обычный домен заблокирован по региону - то же самое, из-за чего
ранее chart_generator.py переехал на CoinGecko).
"""
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

import config
import multi_timeframe
import queue_manager
import strategies
import strategy_tuner
import signal_parser
from signal_parser import RsiSignal

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-api.binance.vision/api/v3"

# --- Настройки сканирования (можно тюнить без переписывания логики) ---
TIMEFRAME = "15m"
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
TOP_N_BY_VOLUME = 150          # сколько самых ликвидных пар сканировать
MIN_QUOTE_VOLUME_24H = 500_000   # отсекаем совсем неликвидные пары ($)
ALERT_COOLDOWN_HOURS = 4        # не алертим один и тот же тикер+направление чаще

# Исключаем стейблы и плечевые токены - там RSI/Bollinger малоинформативны
_EXCLUDED_SUBSTRINGS = (
    "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT",
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
)


@dataclass
class _Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0  # объём в quote-валюте (USDT) этой свечи - используется
                          # strategies.py для подтверждения пробоя объёмом,
                          # исходный RSI/Bollinger расчёт его не использует


def _fetch_universe() -> list[tuple[str, float]]:
    """Топ-N USDT-пар по 24h объёму в долларах (один запрос на всех).
    Возвращает (symbol, quote_volume_usd)."""
    try:
        resp = requests.get(f"{_BASE_URL}/ticker/24hr", timeout=20)
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.warning("Не удалось получить список пар Binance: %s", e)
        return []

    candidates = []
    for row in rows:
        symbol = row.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        if any(bad in symbol for bad in _EXCLUDED_SUBSTRINGS):
            continue
        ticker = symbol.removesuffix("USDT")
        if ticker in config.EXCLUDED_TICKERS:
            continue
        try:
            quote_volume = float(row["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if quote_volume < MIN_QUOTE_VOLUME_24H:
            continue
        candidates.append((symbol, quote_volume))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:TOP_N_BY_VOLUME]


def _fetch_klines(symbol: str, limit: int = 100) -> list[_Candle]:
    try:
        resp = requests.get(
            f"{_BASE_URL}/klines",
            params={"symbol": symbol, "interval": TIMEFRAME, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except requests.RequestException as e:
        logger.debug("Не удалось получить свечи %s: %s", symbol, e)
        return []

    return [
        _Candle(
            open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
            volume=float(r[7]) if len(r) > 7 else 0.0,  # объём в quote-валюте (USDT)
        )
        for r in rows
    ]


def _calc_rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    """RSI по Уайлдеру - возвращает значение для каждой свечи начиная
    с (period+1)-й, выровненное по индексу с closes (более ранние
    индексы будут отсутствовать)."""
    if len(closes) < period + 1:
        return []

    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values = []

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))

    rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi_values  # rsi_values[k] соответствует closes[k + period]


def _calc_bollinger(closes: list[float], period: int = BB_PERIOD, num_std: float = BB_STD):
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    std = statistics.pstdev(window)
    return sma - num_std * std, sma, sma + num_std * std  # lower, mid, upper


def _detect_divergence(closes: list[float], rsi_series: list[float]) -> str | None:
    """Очень упрощённая дивергенция: сравниваем минимум/максимум цены и
    RSI в первой и второй половине последних 30 точек. Не претендует
    на точность профессиональных индикаторов - это базовый эвристический
    фильтр, который можно уточнять позже."""
    n = min(30, len(rsi_series))
    if n < 10:
        return None

    price_window = closes[-n:]
    rsi_window = rsi_series[-n:]
    mid = n // 2
    p1, p2 = price_window[:mid], price_window[mid:]
    r1, r2 = rsi_window[:mid], rsi_window[mid:]

    if min(p2) < min(p1) and min(r2) > min(r1):
        return "bullish"
    if max(p2) > max(p1) and max(r2) < max(r1):
        return "bearish"
    return None


def _score_and_quality(rsi: float, direction_overbought: bool, bb_touch: bool,
                        divergence_match: bool, quote_volume: float) -> tuple[int, str]:
    """Собственная (не претендующая на чужую формулу) прозрачная оценка
    0-100. Откалибровано так, чтобы 90+ получали только сетапы с
    настоящим совпадением нескольких факторов сразу (а не просто
    RSI чуть за 70/30, как было раньше - с той формулой 90+ было
    практически недостижимо математически)."""
    extremity = (rsi - RSI_OVERBOUGHT) if direction_overbought else (RSI_OVERSOLD - rsi)
    score = 30 + min(max(extremity, 0) * 3, 50)  # 30 (на грани 70/30) .. 80 (RSI экстремальнее ~87/13)
    if bb_touch:
        score += 15
    if divergence_match:
        score += 10
    if quote_volume >= 5_000_000:
        score += 5
    score = round(min(score, 100))

    if score >= 90:
        quality = "Conservative"
    elif score >= 70:
        quality = "Moderate"
    else:
        quality = "Aggressive"
    return score, quality


def _build_signal(symbol: str, candles: list[_Candle], quote_volume: float) -> RsiSignal | None:
    closes = [c.close for c in candles]
    if len(closes) < BB_PERIOD + RSI_PERIOD:
        return None

    rsi_series = _calc_rsi_series(closes)
    if not rsi_series:
        return None
    rsi_now = rsi_series[-1]

    bb = _calc_bollinger(closes)
    if bb is None:
        return None
    lower, mid, upper = bb
    current_price = closes[-1]

    overbought = rsi_now >= RSI_OVERBOUGHT
    oversold = rsi_now <= RSI_OVERSOLD
    if not (overbought or oversold):
        return None  # ничего интересного по этой паре прямо сейчас

    bb_touch = current_price >= upper if overbought else current_price <= lower
    divergence = _detect_divergence(closes, rsi_series)
    divergence_match = (divergence == "bearish" and overbought) or (divergence == "bullish" and oversold)

    score, quality = _score_and_quality(rsi_now, overbought, bb_touch, divergence_match, quote_volume)

    strategy_parts = ["RSI"]
    if bb_touch:
        strategy_parts.append("Bollinger Touch")
    if divergence_match:
        strategy_parts.append("Divergence")
    strategy = " + ".join(strategy_parts)

    direction = "Шорт (перекупленность)" if overbought else "Лонг (перепроданность)"

    recent_high = max(c.high for c in candles[-20:])
    recent_low = min(c.low for c in candles[-20:])
    atr_buffer = None
    if config.USE_ATR_STOPS:
        atr = strategies.calc_atr(candles, config.ATR_PERIOD)
        atr_buffer = atr * config.ATR_STOP_MULTIPLIER if atr else None

    if overbought:
        entry_low, entry_high = current_price * 0.999, current_price * 1.002
        invalidation = recent_high + atr_buffer if atr_buffer else recent_high * 1.003
        target = mid  # возврат к средней полосе Боллинджера
    else:
        entry_low, entry_high = current_price * 0.998, current_price * 1.001
        invalidation = recent_low - atr_buffer if atr_buffer else recent_low * 0.997
        target = mid

    ticker = symbol.replace("USDT", "")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 24ч = 96 свечей по 15 минут назад (если данных достаточно)
    change_24h_str = ""
    if len(closes) >= 97 and closes[-97] != 0:
        change_24h = (current_price - closes[-97]) / closes[-97] * 100
        change_24h_str = f"{'+' if change_24h >= 0 else ''}{change_24h:.2f}%"

    description = (
        f"RSI {'выше 70' if overbought else 'ниже 30'} на {TIMEFRAME}"
        + (", цена коснулась полосы Боллинджера" if bb_touch else "")
        + (", обнаружена дивергенция" if divergence_match else "")
        + ". Собственный сканер бота, без участия Telegram-канала."
    )

    return RsiSignal(
        ticker=ticker,
        timeframe=TIMEFRAME,
        strategy=strategy,
        direction=direction,
        current_price=f"{current_price:.6g}",
        rsi_now=f"{rsi_now:.2f}",
        score=str(score),
        quality=quality,
        entry_low=f"{entry_low:.6g}",
        entry_high=f"{entry_high:.6g}",
        invalidation=f"{invalidation:.6g}",
        target=f"{target:.6g}",
        change_24h=change_24h_str,
        volume=f"{quote_volume / 1_000_000:.2f}M",
        rsi_live=f"{rsi_now:.2f}",
        created_at=now_str,
        description=description,
        raw_text="(сгенерировано собственным сканером, без исходного текста)",
    )


def _is_actively_trading(symbol: str) -> bool:
    """Доп. проверка статуса листинга через /exchangeInfo - строже, чем
    /ticker/24hr, который использует _fetch_universe().

    Баг, который эта функция чинит: data-api.binance.vision - публичное
    зеркало, и его /ticker/24hr иногда продолжает отдавать объём/данные
    по паре ЕЩЁ ДОЛГО ПОСЛЕ реального делистинга с Binance (например,
    PHBUSDT делистнут 27 мая 2026, но зеркало отдавало по нему объём и
    свечи ещё в июле) - из-за этого сканер предлагает сигналы по парам,
    которых уже не существует для реальной торговли. /exchangeInfo -
    авторитетный "снимок" статуса (TRADING/BREAK/HALT и т.п.) и обычно
    актуализируется вместе с самим делистингом, в отличие от рыночной
    статистики.

    Используем ТОТ ЖЕ домен (data-api.binance.vision), не api.binance.com -
    последний геоблокирован на раннерах, ради чего сканер и переехал на
    зеркало (см. docstring модуля). При сетевой ошибке/неожиданном
    ответе - True (не блокируем сигнал из-за сбоя самой проверки, та же
    философия, что и в chart_generator.symbol_exists)."""
    try:
        resp = requests.get(f"{_BASE_URL}/exchangeInfo", params={"symbol": symbol}, timeout=10)
    except requests.RequestException as e:
        logger.warning("Не удалось проверить статус листинга %s: %s", symbol, e)
        return True

    if resp.status_code in (400, 404):
        logger.info("Сканер: %s не торгуется на Binance (exchangeInfo status %d) - отсеян", symbol, resp.status_code)
        return False
    if not resp.ok:
        return True

    try:
        symbols = resp.json().get("symbols", [])
        status = symbols[0]["status"] if symbols else None
    except (ValueError, KeyError, IndexError):
        return True

    if status is not None and status != "TRADING":
        logger.info("Сканер: %s имеет статус '%s' (не TRADING) - отсеян", symbol, status)
        return False
    return True


def _process_signal_candidate(signal: RsiSignal, symbol: str, ticker: str, min_score_cfg: int,
                               on_signal_accepted=None) -> bool:
    """Общий конвейер обработки ОДНОГО кандидата - неважно, от базовой
    RSI/Bollinger (см. _build_signal выше) или от любой стратегии из
    strategies.ADDITIONAL_STRATEGIES: cooldown -> подтверждение старшими
    ТФ (multi_timeframe.refine_signal) -> порог публикации -> очередь.
    Возвращает True, если сигнал был добавлен в очередь.

    on_signal_accepted(signal, symbol) - опциональный колбэк, вызывается
    ПОСЛЕ того, как сигнал прошёл ВСЕ те же фильтры, что и для публикации
    поста (cooldown, подтверждение старшими ТФ, порог score) - см.
    futures_signal_bridge.py/futures_auto_trade.py, который передаёт сюда
    колбэк, открывающий защищённую позицию по прошедшему фильтры сигналу.
    scanner.py сознательно НИЧЕГО не знает о futures/риск-предохранителях -
    это ответственность колбэка, не этого модуля (тот же сканер
    переиспользуется index_signal_scanner.py, которому futures вообще не
    касается). Ошибка внутри колбэка НЕ должна ронять весь тик сканирования
    (одна плохая позиция не должна останавливать сканирование остальных
    150 пар) - см. try/except вокруг вызова.

    Ключ cooldown - (ticker, direction), БЕЗ учёта стратегии: если в
    один и тот же тик сразу две разные стратегии сигналят "SOL, лонг" -
    после первой же успешно добавленной публикация второй такой же по
    направлению станет избыточной (читателю не нужны два почти
    одинаковых поста подряд про один и тот же тикер/направление) - её
    отфильтрует тот же cooldown, что защищает и от повторов одной и той
    же стратегии."""
    direction_key = "long" if signal_parser.is_long_direction(signal.direction) else "short"
    if queue_manager.was_recently_alerted(ticker, direction_key, ALERT_COOLDOWN_HOURS):
        return False

    # Подтверждение старшими таймфреймами (1ч/4ч/1д, см.
    # multi_timeframe.py) - может изменить score/quality сигнала
    # (согласие/конфликт со старшими ТФ) или отклонить сигнал целиком
    # (veto при сильном конфликте).
    refined = multi_timeframe.refine_signal(signal, symbol)
    if refined is None:
        return False
    signal = refined

    if int(signal.score) < strategy_tuner.get_effective_min_score(signal.strategy, min_score_cfg):
        # Сигнал есть, но он не пройдёт порог публикации (см.
        # config.MIN_SIGNAL_SCORE_TO_PUBLISH, с поправкой strategy_tuner -
        # если у ЭТОЙ стратегии статистически слабый win-rate, порог для
        # неё временно строже) - не кладём его в очередь и НЕ ставим
        # cooldown, чтобы на следующем тике, если сетап станет более
        # выраженным, сигнал по этому же тикеру мог пройти порог и быть
        # учтён. Раньше такие сигналы всё равно копились в очереди и
        # просто вытесняли друг друга при переполнении (>30), никогда не
        # доходя до публикации.
        return False

    queue_manager.push_pending_signal(signal)
    queue_manager.mark_alerted(ticker, direction_key)
    logger.info(
        "Сканер: новый сигнал %s %s (%s, score %s)",
        ticker, signal.direction, signal.strategy, signal.score,
    )

    if on_signal_accepted is not None:
        try:
            on_signal_accepted(signal, symbol)
        except Exception:
            logger.exception(
                "Сканер: колбэк on_signal_accepted упал на сигнале %s %s - "
                "сканирование продолжается, публикация в очередь выше уже прошла успешно",
                ticker, signal.direction,
            )

    return True


def run_scan(on_signal_accepted=None) -> int:
    """Сканирует рынок и кладёт найденные сигналы в очередь бота.
    Возвращает количество добавленных сигналов.

    on_signal_accepted - см. docstring _process_signal_candidate. По
    умолчанию None - поведение полностью совпадает со старым (только
    публикация в очередь, без каких-либо побочных действий).

    Пробует НЕСКОЛЬКО независимых стратегий на одних и тех же уже
    полученных свечах - базовую RSI/Bollinger (_build_signal) и все из
    strategies.ADDITIONAL_STRATEGIES (MACD Crossover, Donchian Breakout
    и т.п.). Стратегии не обязаны совпадать друг с другом - сигнал от
    ЛЮБОЙ из них независимо проходит дальше (см. _process_signal_candidate).
    Это НЕ увеличивает число сетевых запросов - все стратегии работают
    на одном и том же наборе свечей одного fetch'а на символ."""
    universe = _fetch_universe()
    if not universe:
        logger.warning("Сканер: не удалось получить список пар - пропускаю тик")
        return 0

    added = 0
    for symbol, quote_volume in universe:
        candles = _fetch_klines(symbol)
        if not candles:
            continue

        candidates = [_build_signal(symbol, candles, quote_volume)]
        for build_extra_signal in strategies.ADDITIONAL_STRATEGIES:
            candidates.append(build_extra_signal(symbol, candles, quote_volume))
        candidates = [c for c in candidates if c is not None]
        if not candidates:
            continue  # ни одна стратегия ничего не нашла по этой паре прямо сейчас

        if not _is_actively_trading(symbol):
            # Пара всё ещё встречается в /ticker/24hr зеркала (иногда
            # неделями/месяцами после реального делистинга), но
            # /exchangeInfo подтверждает, что торговать ей уже нельзя -
            # не даём ни одному из кандидатов дойти до очереди/публикации.
            continue

        ticker = symbol.replace("USDT", "")
        for signal in candidates:
            if _process_signal_candidate(signal, symbol, ticker, config.MIN_SIGNAL_SCORE_TO_PUBLISH,
                                          on_signal_accepted=on_signal_accepted):
                added += 1

    if added:
        logger.info("Сканер: добавлено %d новых сигналов в очередь", added)
    return added