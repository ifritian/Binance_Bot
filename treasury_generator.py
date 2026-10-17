"""
Генератор поста "Treasury Index" - публикуется раз в TREASURY_INTERVAL_HOURS
(конфиг), независимо от currency/opinion/article.

Сам индекс (состав корзины, веса, расчёт %, защита от выбросов) считается
ПОЛНОСТЬЮ кодом - см. treasury_index.py. LLM здесь получает уже готовый
числовой блок как контекст и пишет только короткий хук ПЕРЕД ним - не
переписывает и не придумывает цифры внутри блока, ровно та же идея, что
в opinion_generator/article_generator.
"""
import logging
import re
import time
from datetime import datetime
from typing import Optional

import config
import cliche_filter
from groq_client import call_groq
import index_health_monitor
import index_volatility
import post_format
import queue_manager
import treasury_chart
import treasury_composition_chart
import treasury_heatmap
import voice_guidelines
from treasury_index import (
    TreasuryIndexResult, compute_breadth, compute_index, fetch_market_benchmark_pct,
    fetch_reference_change_pct, format_breadth_line, format_index_block, leading_tier,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты пишешь короткий хук (вводную фразу) для поста
Treasury Index - собственного инфраструктурного крипто-индекса канала
(L1/L2/DeFi монеты без BTC/ETH/BNB, разбитые на три уровня риска:
Фундамент/Рост/Риск). Хук идёт ПЕРЕД готовым числовым блоком индекса -
сам блок тебе показан только для контекста, повторять его в ответе не
нужно, он будет добавлен отдельно после твоего текста.

1-3 предложения, живой разговорный стиль, без канцелярита и без
воды. Можно лёгкую рефлексию о том, что означает движение - например,
какой тир лидирует и что это может говорить о риск-аппетите рынка
(если Риск обгоняет Фундамент - аппетит к риску растёт, и наоборот),
а также о том, обогнал ли индекс BTC за период и как дела с момента
запуска (эти цифры тебе даны отдельно) - но без выдумывания новостей,
которых тебе не давали.

Тебе дан набор реальных чисел (см. задание) - если используешь цифры в
хуке, то ТОЧНО как даны, не округляя и не придумывая других. Можно
вообще не называть цифры в хуке (они и так есть в блоке ниже) - просто
качественная рефлексия тоже подходит.

ЖЁСТКО ЗАПРЕЩЕНО называть конкретную цену в долларах ($113, $1.47 и
т.п.) любой монеты индекса - тебе даны ТОЛЬКО проценты изменения, а не
абсолютные цены, поэтому любая названная тобой цена в долларах будет
выдумкой.

НЕ добавляй сам дисклеймер и НЕ дублируй числовой блок. Отвечай только
текстом хука, без пояснений и без кавычек.""" + voice_guidelines.STYLE_DIRECTIVE


def _extract_numbers(text: str) -> set[float]:
    return {round(float(n), 2) for n in re.findall(r"[+-]?\d+\.?\d*", text.replace(",", ""))}


def _sign(pct: float) -> str:
    return "+" if pct >= 0 else ""


def _format_comparison_block(
    result: TreasuryIndexResult, period_hours: float,
    btc_pct: Optional[float], eth_pct: Optional[float], market_pct: Optional[float],
    history: Optional[dict],
) -> str:
    """Собирает (кодом, не LLM) блок-"крючок" для шеринга:
    - расхождение с BTC за тот же период - самая "цитируемая" метрика
      поста (интереснее голого числа индекса самого по себе), подана
      развёрнуто ("обогнал/отстал на X п.п.");
    - компактная вторая строка с ETH и равновзвешенным топ-4 рынка
      (treasury_index.fetch_market_benchmark_pct) - более честный
      ориентир, чем один BTC, но без повторения той же развёрнутой
      формулировки трижды подряд;
    - кумулятивный трекер с момента запуска (индекс/BTC/ETH/рынок,
      база 100) - придаёт постам ощущение "истории", за которой можно
      следить неделями, а не разового снимка.
    Возвращает пустую строку, если вообще ничего из этого посчитать не
    удалось (все внешние тикеры недоступны) - остальной пост
    публикуется как обычно."""
    lines = []

    if btc_pct is not None:
        diff = round(result.total_pct - btc_pct, 2)
        if diff > 0:
            verb = f"обогнал BTC на {diff} п.п."
        elif diff < 0:
            verb = f"отстал от BTC на {abs(diff)} п.п."
        else:
            verb = "наравне с BTC"
        lines.append(
            f"🆚 За {period_hours:g}ч: Индекс {_sign(result.total_pct)}{result.total_pct}% "
            f"vs BTC {_sign(btc_pct)}{btc_pct}% - {verb}"
        )

    extra_bits = []
    if eth_pct is not None:
        extra_bits.append(f"ETH {_sign(eth_pct)}{eth_pct}%")
    if market_pct is not None:
        extra_bits.append(f"топ-4 рынка {_sign(market_pct)}{market_pct}%")
    if extra_bits:
        lines.append(f"📐 Для сравнения: {', '.join(extra_bits)}")

    if history is not None:
        launch_date = datetime.fromtimestamp(history["launch_at"]).strftime("%d.%m.%Y")
        idx_cum = round(history["index_value"] - 100, 2)
        btc_cum = round(history["btc_value"] - 100, 2)
        since_parts = [f"Индекс {_sign(idx_cum)}{idx_cum}%", f"BTC {_sign(btc_cum)}{btc_cum}%"]

        if "eth_value" in history:
            eth_cum = round(history["eth_value"] - 100, 2)
            since_parts.append(f"ETH {_sign(eth_cum)}{eth_cum}%")
        if "market_value" in history:
            market_cum = round(history["market_value"] - 100, 2)
            since_parts.append(f"Рынок {_sign(market_cum)}{market_cum}%")

        lines.append(f"📈 С запуска ({launch_date}): {' | '.join(since_parts)}")

        # Честная пометка о смене методики - показывается первые дни после
        # перехода (см. queue_manager.ensure_treasury_method_v2), чтобы
        # читатель понимал, почему "с запуска" начинается заново.
        if history.get("method_note_until", 0) > time.time():
            lines.append("ℹ️ Методика расчёта уточнена, отсчёт «с запуска» начат заново.")

    return "\n".join(lines)


_MIN_PERIOD_HOURS = 1
_MAX_PERIOD_HOURS = 168  # неделя - дольше 1ч-свечи запрашивать неразумно


def resolve_period_hours(default_hours: float, now: Optional[float] = None) -> float:
    """Окно расчёта текущего поста = число ЧАСОВЫХ СВЕЧЕЙ между прошлым
    замером и сейчас. Именно так цепочка периодов (каждый % применяется к
    накопленному значению - см. queue_manager.update_treasury_history)
    покрывает время без пропусков: окно начинается с открытия той часовой
    свечи, в которой был прошлый замер (перекрытие - меньше часа, а не
    пропуск и не двойной учёт нескольких часов, как было при фиксированных
    12ч раз в сутки).

    Если замеров ещё нет - default_hours. Результат ограничен
    [_MIN_PERIOD_HOURS, _MAX_PERIOD_HOURS]."""
    snapshots = queue_manager.get_treasury_snapshots()
    if not snapshots:
        return default_hours

    now = time.time() if now is None else now
    hours_between = int(now // 3600) - int(snapshots[-1]["timestamp"] // 3600)
    return float(min(max(hours_between, _MIN_PERIOD_HOURS), _MAX_PERIOD_HOURS))


def generate_treasury_post(period_hours: float = 12.0) -> Optional[tuple]:
    """Возвращает (текст для Binance Square, текст для кросспоста в
    Telegram, TreasuryIndexResult, chart_path, heatmap_path,
    composition_path), либо None, если индекс не удалось посчитать
    вообще (ни один тир не собрался - например, полностью недоступен
    data-api.binance.vision).

    - chart_path - equity curve "с запуска" (treasury_chart.py), либо
      None, если снимков ещё недостаточно или BTC недоступен для
      сравнения (без него не с чем сверять кумулятив).
    - heatmap_path - тепловая карта по всем 15 монетам (treasury_heatmap.py) -
      генерируется КАЖДЫЙ раз, когда есть хотя бы один тир.
    - composition_path - диаграмма состава корзины (treasury_composition_chart.py) -
      генерируется РЕДКО, раз в config.TREASURY_COMPOSITION_INTERVAL_POSTS
      постов (состав статичен между ребалансировками).

    Любая из трёх картинок может быть None (недостаточно данных или
    ошибка построения) - публикация текста при этом не блокируется,
    см. main.try_publish_treasury_post.

    Сейчас оба текста идентичны - ссылка на Telegram-канал в пост для
    Binance Square НЕ добавляется (площадка блокирует такие посты
    модерацией, см. комментарий внутри функции), возврат двух отдельных
    строк сохранён на будущее, если для одной из площадок понадобится
    другое форматирование.

    Поднимает groq_client.GroqRateLimited при 429 от Groq - вызывающий
    код (main.py) уже умеет это ловить и выставлять backoff, как для
    opinion/article.

    period_hours - только ЗАПАСНОЕ значение (первый пост без истории).
    Реальное окно расчёта - время с прошлого замера (resolve_period_hours),
    иначе кумулятивное "с запуска" не покрывало бы время между постами."""
    just_reset = queue_manager.ensure_treasury_method_v2()
    if just_reset:
        # Старт новой истории: период поста относится ко времени ДО
        # старта, поэтому в кумулятив он не входит (см. ниже).
        period_hours = config.TREASURY_INTERVAL_HOURS
    else:
        period_hours = resolve_period_hours(period_hours)

    result = compute_index(period_hours=period_hours)

    try:
        streaks = index_health_monitor.record_check_results(result.missing)
        index_health_monitor.check_and_alert(streaks)
    except Exception:
        logger.exception("Ошибка мониторинга здоровья монет индекса - не блокирую публикацию Treasury Index")

    try:
        queue_manager.append_coin_periods(result.tiers)
    except Exception:
        # История для rebalance_advisor - не критична для самого поста,
        # одна неудачная запись не должна ронять публикацию Treasury Index.
        logger.exception("Не удалось обновить per-coin историю для ребалансировки")

    if result.total_pct is None:
        logger.warning("Treasury Index: не удалось посчитать ни один тир - пропускаю публикацию")
        return None

    # Тепловая карта - КАЖДЫЙ пост (не зависит от BTC/сравнения - строится
    # из тех же result.tiers, что уже посчитаны выше).
    try:
        heatmap_path = treasury_heatmap.generate_treasury_heatmap(result)
    except Exception:
        logger.exception("Не удалось построить тепловую карту Treasury Index - публикую без неё")
        heatmap_path = None

    # Диаграмма состава - РЕДКО (раз в TREASURY_COMPOSITION_INTERVAL_POSTS
    # постов) - состав между ребалансировками не меняется, показывать
    # его каждый раз избыточно.
    composition_path = None
    post_count = queue_manager.increment_treasury_post_count()
    if post_count % config.TREASURY_COMPOSITION_INTERVAL_POSTS == 0:
        try:
            composition_path = treasury_composition_chart.generate_composition_chart()
        except Exception:
            logger.exception("Не удалось построить диаграмму состава Treasury Index - публикую без неё")

    # Сравнение с BTC/ETH/рынком за тот же период + кумулятивный трекер
    # с запуска. Каждый бенчмарк опционален - если какой-то недоступен
    # (сетевой сбой), публикуем пост без него, не блокируем публикацию
    # из-за одного внешнего тикера.
    btc_pct = fetch_reference_change_pct("BTCUSDT", period_hours)
    eth_pct = fetch_reference_change_pct("ETHUSDT", period_hours)
    market_pct = fetch_market_benchmark_pct(period_hours)

    history = None
    chart_path = None
    if btc_pct is not None:
        if just_reset:
            history = queue_manager.get_treasury_history()
            snapshots = queue_manager.get_treasury_snapshots()
        else:
            history = queue_manager.update_treasury_history(result.total_pct, btc_pct, eth_pct, market_pct)
            snapshots = queue_manager.append_treasury_snapshot(history)
        try:
            chart_path = treasury_chart.generate_treasury_chart(snapshots)
        except Exception:
            # Картинка - бонус, не условие публикации: если построение
            # почему-то упало (испорченный снимок, проблема matplotlib
            # в среде выполнения и т.п.), публикуем пост текстом, как раньше.
            logger.exception("Не удалось построить график Treasury Index - публикую без картинки")
    else:
        logger.warning("Не удалось получить BTC для сравнения - публикую Treasury Index без блока сравнения")

    if just_reset:
        returns_history = queue_manager.get_treasury_returns_history()
    else:
        returns_history = queue_manager.append_treasury_return(result.total_pct)
    breadth = compute_breadth(result)

    binance_text = build_basket_post(
        result, period_hours, btc_pct, eth_pct, history, breadth,
    )

    # Ссылку на Telegram-канал в пост для Binance Square НЕ добавляем -
    # площадка блокирует такие посты модерацией с причиной "promotes
    # third-party channels" (проверено на практике).
    telegram_text = binance_text

    logger.info("Сгенерирован пост 'Altcoin basket' (%s%%): %s", result.total_pct, binance_text[:150].replace("\n", " "))
    return binance_text, telegram_text, result, chart_path, heatmap_path, composition_path



def _plain_period(hours: float) -> str:
    if hours >= 36:
        return f"{hours / 24:.0f} days" if abs(hours / 24 - round(hours / 24)) < 0.2 else f"{hours / 24:.1f} days"
    return f"{hours:g}h"


_BASKET_QUESTIONS = [
    "Where is your money leaning right now: BTC or alts? 👇",
    "Alts or BTC for the next week? Pick one 👇",
    "Which side wins the next 3 days: alts or BTC?",
]


def build_basket_post(result, period_hours: float, btc_pct, eth_pct, history, breadth, rng=None) -> str:
    """Пост "Altcoin basket vs BTC": простой английский, без жаргона и без LLM
    (все числа и фразы собирает код). Первая строка - итог сравнения."""
    import random as _random
    rng = rng or _random
    total = result.total_pct
    period = _plain_period(period_hours)
    lines = []

    if btc_pct is not None:
        diff = round(total - btc_pct, 1)
        if diff >= 0.3:
            lines.append(f"Alts beat BTC by {diff} pts over {period}")
        elif diff <= -0.3:
            lines.append(f"BTC beat alts by {abs(diff)} pts over {period}")
        else:
            lines.append(f"Alts and BTC moved together over {period}")
    else:
        lines.append(f"Altcoin basket over {period}: {total:+.1f}%")

    lines += ["", f"Our 15-coin altcoin basket (L1, L2, DeFi; no BTC, ETH or BNB): {total:+.1f}%"]
    ref = []
    best_worst = []
    coins = [c for t in result.tiers for c in t.coins if c.pct is not None and not c.suspicious]
    coins.sort(key=lambda c: c.pct, reverse=True)
    if btc_pct is not None:
        ref.append(f"$BTC {btc_pct:+.1f}%")
    if eth_pct is not None:
        ref.append(f"ETH {eth_pct:+.1f}%")
    if ref:
        lines.append(" | ".join(ref))
    if coins:
        best_worst.append(f"Best: ${coins[0].ticker} {coins[0].pct:+.1f}%")
        if len(coins) > 1:
            best_worst.append(f"Worst: ${coins[-1].ticker} {coins[-1].pct:+.1f}%")
    if best_worst:
        lines += [""] + best_worst
    if breadth and breadth.get("total"):
        lines.append(f"{breadth['green']} of {breadth['total']} coins are up")

    if history is not None:
        since = datetime.fromtimestamp(history["launch_at"]).strftime("%d.%m")
        bits = [f"basket {history['index_value'] - 100:+.1f}%", f"BTC {history['btc_value'] - 100:+.1f}%"]
        if "eth_value" in history:
            bits.append(f"ETH {history['eth_value'] - 100:+.1f}%")
        lines += ["", f"Since we started tracking ({since}): " + ", ".join(bits)]

    lines += ["", rng.choice(_BASKET_QUESTIONS), "", "Not financial advice.", "#Altcoins #Crypto"]
    return "\n".join(lines)


_MIN_HOOK_CHARS = 10


def validate_treasury_hook(hook: str, allowed_numbers: set[float]) -> tuple[bool, str]:
    """Хук не должен содержать чисел, которых нет среди уже посчитанных
    (числового блока). Сам числовой блок в проверку не входит - он
    собран кодом и по определению корректен.

    Также хук не должен быть пустым/почти пустым (см. index_signal_generator
    - тот же баг: пустой хук формально не содержит "чужих" чисел и молча
    проходил бы проверку, оставляя пост без единой мысли автора)."""
    if len(hook.strip()) < _MIN_HOOK_CHARS:
        return False, f"Хук пустой или слишком короткий: {hook!r}"

    numbers = _extract_numbers(hook)
    unknown = [n for n in numbers if not any(abs(n - a) < 0.05 for a in allowed_numbers)]
    if unknown:
        return False, f"В хуке есть числа не из посчитанных данных: {unknown}"

    cliche_ok, found = cliche_filter.check_cliches(hook)
    if not cliche_ok:
        return False, f"В хуке есть шаблонные ИИ-фразы: {', '.join(found)}"

    return True, ""