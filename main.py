"""
main.py - точка входа.

Три независимых формата поста, каждый со своим расписанием:

1. "currency" - пост про конкретную валюту из канала (дайджест или
   картинка), не чаще раза в MIN_POST_INTERVAL_HOURS (по умолчанию 4ч).
2. "opinion" - личное мнение о движении BTC за последние 2 дня,
   раз в OPINION_INTERVAL_HOURS (по умолчанию 48ч).
3. "article" - статья-сводка по дайджестам за неделю, раз в
   ARTICLE_INTERVAL_HOURS (по умолчанию 168ч).

Форматы независимы - могут опубликоваться в один день, если совпали
по времени. Проверка новых постов в канале (для currency) идёт на
каждом тике; для opinion/article новый контент генерируется "с нуля"
в момент публикации, без отдельной очереди.
"""
import base64
import logging
import mimetypes
import random
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

import article_generator
import accuracy_report_generator
import alerting
import audience_question_generator
import binance_promo_generator
import binance_publisher
import bluesky_publisher
import chart_generator
import config
import image_analyzer
import groq_client
import hot_take_generator
import index_signal_generator
import index_signal_scanner
import loss_review_generator
import mini_lesson_generator
import news_opinion_generator
import opinion_generator
import outcome_tracker
import post_format
import queue_manager
import rebalance_advisor
import scanner
import signal_parser
import strategy_tuner
import telegram_listener
import telegram_extended
import telegram_engagement
import telegram_glossary
import telegram_publisher
import text_generator
import treasury_generator
import validator
import voice_memory
import volatility_alert
import win_celebration_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


# ============================================================
# Формат 1: "currency" - пост про конкретную валюту из канала
# ============================================================

def check_for_new_signals() -> None:
    posts = telegram_listener.fetch_new_channel_posts()

    for post in posts:
        if post.text:
            signals = signal_parser.parse_signals(post.text)
            if signals:
                # Отсеиваем тикеры, которых физически нет на Binance (сторонний
                # бот-источник иногда шлёт пары не с Binance, например акции с
                # похожим тикером) - лучше сразу пропустить весь такой сигнал,
                # чем потратить 3-4 попытки публикации впустую и заблокировать
                # им место в очереди перед реально валидными сигналами.
                valid_signals = [s for s in signals if chart_generator.symbol_exists(s.ticker)]
                skipped = [s.ticker for s in signals if s not in valid_signals]
                if skipped:
                    logger.warning("Отсеяны тикеры не с Binance (нет такой пары): %s", skipped)

                if not valid_signals:
                    continue

                # выбираем сигнал из пачки, избегая повтора недавних тикеров,
                # вместо того чтобы всегда брать первый (для разнообразия постов)
                recent = queue_manager.get_recent_tickers()
                chosen = signal_parser.pick_entry(valid_signals, recent)
                logger.info(
                    "Новый сигнал: %s %s, вход %s-%s, стоп %s, тейк %s, score %s "
                    "(недавние тикеры: %s)",
                    chosen.ticker, chosen.direction, chosen.entry_low, chosen.entry_high,
                    chosen.invalidation, chosen.target, chosen.score, recent,
                )
                queue_manager.push_pending_signal(chosen)
                continue  # текст распознан как сигнал - картинку (если есть) не трогаем

        if post.image_url:
            insight = image_analyzer.analyze_chart_image(post.image_url, post.photo_file_id)
            if insight is not None:
                logger.info("Новая картинка распознана: %s, направление %s", insight.ticker, insight.direction)
                queue_manager.push_pending_image(insight)


def _publish_signal(signal) -> bool:
    logger.info("Публикуем сигнал %s", signal.ticker)

    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        post_text, hook = text_generator.generate_post_text(signal, hook_mode)
    except Exception as e:
        logger.error("Ошибка генерации текста: %s", e)
        return False

    ok, reason = validator.validate_post_text(post_text, signal)
    if not ok:
        logger.error("Пост не прошёл проверку чисел, публикация отменена: %s", reason)
        return False

    try:
        chart_path = chart_generator.generate_chart_image(
            signal.ticker, days=2, expected_price=float(signal.current_price)
        )
    except Exception as e:
        logger.warning("Не удалось сгенерировать график для %s: %s", signal.ticker, e)
        chart_path = None

    if chart_path is None:
        logger.warning(
            "Нет графика для %s - публикация пропущена, пост остаётся в очереди "
            "до появления следующего подходящего поста.", signal.ticker
        )
        return False

    published, bluesky_ref = _do_publish(post_text, [chart_path], signal.ticker, hook=hook, signal=signal)
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
        # Валютные сигналы не привязаны к "теме" в смысле voice_memory
        # (там это BTC/ETH/market для opinion/hot_take) - record_post
        # здесь только пополняет общий анти-повтор зачинов (без theme/pct,
        # continuity_block валютным сигналам не нужен - там уже есть
        # честная точная привязка к цифрам сетапа).
        voice_memory.record_post(post_text)
        # Ставим сигнал на трекинг результата ПОСЛЕ публикации - если
        # пост не вышел, аудитория его не видела, трекать нечего.
        try:
            outcome_tracker.record_signal_outcome(signal, bluesky_ref=bluesky_ref)
        except Exception:
            logger.exception("Не удалось поставить сигнал %s на трекинг результата", signal.ticker)
    return published


def _publish_image_insight(insight) -> bool:
    logger.info("Публикуем пост по картинке %s", insight.ticker)

    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        post_text = text_generator.generate_post_text_from_image(insight, hook_mode)
    except Exception as e:
        logger.error("Ошибка генерации текста по картинке: %s", e)
        return False

    ok, reason = validator.validate_image_post_text(post_text)
    if not ok:
        logger.error("Пост по картинке не прошёл проверку, публикация отменена: %s", reason)
        return False

    image_path = None
    download_url = insight.image_url
    if insight.photo_file_id:
        # Ссылка из момента анализа (insight.image_url) могла протухнуть -
        # запрашиваем свежую прямо перед скачиванием.
        fresh_url = telegram_listener.get_file_url(insight.photo_file_id)
        if fresh_url:
            download_url = fresh_url
        else:
            logger.warning(
                "Не удалось получить свежую ссылку на файл %s, пробую старую (может быть протухшей)",
                insight.photo_file_id,
            )

    try:
        image_path = image_analyzer.download_to_tempfile(download_url)
    except Exception as e:
        logger.warning("Не удалось скачать оригинальную картинку %s: %s", download_url, e)

    if image_path is None:
        logger.warning(
            "Нет картинки для %s - публикация пропущена, пост остаётся в очереди "
            "до появления следующего подходящего поста.", insight.ticker
        )
        return False

    published, _bluesky_ref = _do_publish(post_text, [image_path], insight.ticker)
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
        voice_memory.record_post(post_text)
    return published


def _do_publish(
    post_text: str, image_paths, ticker: str | None = None, hook: str | None = None, signal=None
) -> tuple:
    """Возвращает (published: bool, bluesky_ref: dict | None) -
    bluesky_ref ВСЕГДА None здесь: кросспост в Bluesky теперь отложен
    (см. _schedule_crossposts) и публикуется на одном из следующих
    тиков, а не синхронно внутри этого вызова. Когда отложенный кроспост
    реально состоится, ссылка на него дозаписывается в уже созданную
    запись трекинга результата через
    queue_manager.attach_bluesky_ref_to_outcome (см.
    _publish_scheduled_bluesky) - формат "До/После" по-прежнему работает,
    просто привязка происходит чуть позже, а не в момент этого вызова."""
    binance_text = post_text
    cta = post_format.maybe_binance_cta()
    if cta:
        binance_text = f"{post_text}\n\n{cta}"

    hashtags = post_format.square_hashtags_line(ticker)
    if hashtags:
        binance_text = f"{binance_text}\n\n{hashtags}"

    try:
        result = binance_publisher.publish_post(binance_text, image_paths=image_paths)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации: %s", e)
        return False, None

    logger.info("Опубликовано (валюта): %s", result)
    image_path = image_paths[0] if image_paths else None
    telegram_text = _build_extended_telegram_text(post_text, signal, hook) if (signal is not None and hook) else post_text
    _schedule_crossposts(post_text, telegram_text, image_path, ticker, hook, signal)
    return True, None


def _build_extended_telegram_text(post_text: str, signal, hook: str) -> str:
    """Формат "Разбор без купюр" (Telegram) - расширяет обычный текст
    сигнала блоком "Контекст" (см. telegram_extended.py), ТОЛЬКО для
    кросспоста в канал. Square и Bluesky получают post_text как есть,
    без этого блока - это осознанное отличие контента по площадкам, а
    не сокращённая копия одного и того же текста.

    Если генерация/валидация блока не удалась - тихий fallback на
    обычный post_text, публикация в канал всё равно происходит, просто
    без дополнительного блока (бонус необязателен, а не условие)."""
    try:
        result = telegram_extended.generate_extended_context(signal, hook)
    except Exception as e:
        logger.warning("Не удалось сгенерировать блок 'Контекст' для Telegram (%s): %s", signal.ticker, e)
        return post_text

    if result is None:
        return post_text

    context, allowed_numbers = result
    ok, reason = telegram_extended.validate_extended_context(context, allowed_numbers)
    if not ok:
        logger.warning("Блок 'Контекст' для %s не прошёл проверку (%s) - публикую без него", signal.ticker, reason)
        return post_text

    if post_text.endswith(post_format.DISCLAIMER):
        body = post_text[: -len(post_format.DISCLAIMER)].rstrip()
        return f"{body}\n\n📚 Контекст:\n{context}\n\n{post_format.DISCLAIMER}"
    return f"{post_text}\n\n📚 Контекст:\n{context}"


def _crosspost_to_telegram(text: str, image_path=None) -> None:
    """Дублирует уже опубликованный (на Binance Square) пост в
    собственный Telegram-канал (config.TELEGRAM_PUBLISH_CHANNEL).

    Кросспостинг ОПЦИОНАЛЕН и НЕЗАВИСИМ от основной публикации - если
    канал не настроен (telegram_publisher.is_configured() == False)
    просто молча пропускаем, а если настроен, но запрос упал - логируем
    предупреждение и идём дальше: неудачный кросспост НЕ должен
    откатывать или блокировать уже состоявшуюся публикацию на Square."""
    if not telegram_publisher.is_configured():
        return
    try:
        telegram_publisher.publish_post(text, image_path)
    except telegram_publisher.TelegramPublishError as e:
        logger.warning("Кросспост в Telegram не удался: %s", e)


def _crosspost_to_bluesky(
    text: str,
    image_path=None,
    ticker: str | None = None,
    hook: str | None = None,
    signal=None,
) -> dict | None:
    """Дублирует уже опубликованный (на Binance Square) пост в Bluesky
    (config.BLUESKY_HANDLE / config.BLUESKY_APP_PASSWORD).

    Как и _crosspost_to_telegram выше - ОПЦИОНАЛЕН и НЕЗАВИСИМ от основной
    публикации: если Bluesky не настроен - молча пропускаем, если настроен,
    но запрос упал - логируем предупреждение и идём дальше.

    image_path - ЛОКАЛЬНЫЙ путь к уже сгенерированному/скачанному файлу
    (тот же, что был загружен на Binance Square) - AT Protocol грузит
    картинку как сырые байты через uploadBlob, а не по URL, поэтому здесь
    читаем файл с диска напрямую.

    Если signal передан, есть hook и сетап "сильный" (post_format.
    is_strong_setup - score >= BLUESKY_THREAD_MIN_SCORE) - публикуется
    формат "Тред-разбор" (3 поста цепочкой: интрига -> сетап -> вывод+
    ссылки, см. post_format.build_bluesky_thread_signal), а не обычный
    урезанный кросспост. Это редкий, "событийный" формат - для остальных
    сигналов работает обычная логика через build_bluesky_post.

    Возвращает {uri, cid} КОРНЕВОГО поста (обычного или первого поста
    треда), если публикация удалась, иначе None - используется для
    привязки будущего ответа "До/После" (см. outcome_tracker.
    record_signal_outcome / main._post_outcome_updates_to_bluesky)."""
    if not bluesky_publisher.is_configured():
        return None

    image_bytes = None
    content_type = "image/png"
    if image_path is not None:
        path = Path(image_path)
        content_type = mimetypes.guess_type(path.name)[0] or "image/png"
        image_bytes = path.read_bytes()

    if signal is not None and hook is not None and post_format.is_strong_setup(signal):
        try:
            posts = post_format.build_bluesky_thread_signal(hook, signal)
            results = bluesky_publisher.publish_thread(posts, image_bytes=image_bytes, image_content_type=content_type)
            logger.info(
                "Опубликован Bluesky-тред \"сильный сетап\" для %s (score=%s)",
                signal.ticker, signal.score,
            )
            return bluesky_publisher.thread_ref(results[0]) if results else None
        except bluesky_publisher.BlueskyPublishError as e:
            logger.warning("Тред в Bluesky для %s не удался: %s", signal.ticker, e)
            return None
        # Намеренно НЕ падаем дальше в обычный одиночный кросспост при
        # неудаче треда - если часть треда всё же опубликовалась (напр.
        # 1-2 поста, а на третьем упало), добавление ещё и отдельного
        # полного поста той же темой выглядело бы задвоенным контентом
        # в ленте. Лучше пропустить кросспост в этот раз, чем задвоить.

    try:
        # "Тизер-график" - только когда есть картинка (без неё тизер не
        # имеет смысла - показывать нечего) и не всегда, а с заданной
        # вероятностью (см. config.BLUESKY_TEASER_PROBABILITY).
        if image_bytes and random.random() < config.BLUESKY_TEASER_PROBABILITY:
            bluesky_text, link_facets = post_format.build_bluesky_teaser(ticker=ticker)
        else:
            bluesky_text, link_facets = post_format.build_bluesky_post(text, ticker=ticker)
        result = bluesky_publisher.publish_post(
            bluesky_text,
            image_bytes=image_bytes,
            image_content_type=content_type,
            link_facets=link_facets,
        )
        return bluesky_publisher.thread_ref(result)
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Кросспост в Bluesky не удался: %s", e)
        return None


# ============================================================
# Отложенный кроспостинг сигналов (разведение площадок по времени)
# ============================================================
#
# Раньше _do_publish кросспостил (через _crosspost_to_telegram и
# _crosspost_to_bluesky выше) СРАЗУ ЖЕ после публикации на Binance
# Square - все три площадки получали пост в одну и ту же минуту.
# Функции ниже вместо этого кладут кроспост в очередь bot_state.db с
# случайной задержкой (config.CROSSPOST_DELAY_MIN/MAX_MINUTES) и
# публикуют его на одном из следующих тиков, когда время наступит - см.
# _process_pending_crossposts, вызывается из tick().
#
# Другие форматы (мнение, treasury, статья, отчёты) по-прежнему
# кросспостятся синхронно через _crosspost_to_telegram/_crosspost_to_bluesky
# выше - они публикуются намного реже сигналов, разводить их по времени
# отдельная задача (можно сделать следующим шагом при необходимости).


def _b64_to_tempfile(image_b64: str, content_type: str | None) -> Path:
    """Обратная операция base64.b64encode в _schedule_crossposts -
    файл на диске от исходной публикации (chart_generator/image_analyzer)
    не переживает следующий запуск GitHub Actions (свежий checkout), а
    отложенный кроспост может публиковаться через несколько запусков -
    поэтому картинка хранится в очереди как base64 и восстанавливается
    во временный файл только непосредственно перед отправкой в Telegram
    (Bot API sendPhoto ожидает путь к файлу, в отличие от Bluesky, куда
    можно грузить сырые байты напрямую - см. _publish_scheduled_bluesky)."""
    ext = mimetypes.guess_extension(content_type or "image/png") or ".png"
    charts_dir = config.BASE_DIR / "charts"
    charts_dir.mkdir(exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext, dir=charts_dir)
    tmp.write(base64.b64decode(image_b64))
    tmp.close()
    return Path(tmp.name)


def _schedule_crossposts(
    post_text: str, telegram_text: str, image_path, ticker: str | None, hook: str | None, signal
) -> None:
    """Кладёт кроспост в Telegram и в Bluesky в очередь отложенной
    публикации - каждая площадка получает свою НЕЗАВИСИМУЮ случайную
    задержку в диапазоне config.CROSSPOST_DELAY_MIN_MINUTES..
    CROSSPOST_DELAY_MAX_MINUTES, так что порядок площадок между собой
    каждый раз разный (иногда раньше "созреет" Telegram, иногда Bluesky).

    Полностью опционально по каждой площадке, как и раньше - если
    площадка не настроена, запись просто не добавляется в очередь."""
    image_b64 = None
    content_type = "image/png"
    if image_path is not None:
        path = Path(image_path)
        content_type = mimetypes.guess_type(path.name)[0] or "image/png"
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")

    now = time.time()

    if telegram_publisher.is_configured():
        delay_seconds = random.uniform(config.CROSSPOST_DELAY_MIN_MINUTES, config.CROSSPOST_DELAY_MAX_MINUTES) * 60
        due_ts = now + delay_seconds
        queue_manager.push_pending_crosspost("telegram", due_ts, {
            "text": telegram_text,
            "image_b64": image_b64,
            "content_type": content_type,
            "ticker": ticker,
        })
        logger.info("Кросспост в Telegram (%s) отложен на ~%.0f мин", ticker, delay_seconds / 60)

    if bluesky_publisher.is_configured():
        delay_seconds = random.uniform(config.CROSSPOST_DELAY_MIN_MINUTES, config.CROSSPOST_DELAY_MAX_MINUTES) * 60
        due_ts = now + delay_seconds
        queue_manager.push_pending_crosspost("bluesky", due_ts, {
            "text": post_text,
            "image_b64": image_b64,
            "content_type": content_type,
            "ticker": ticker,
            "hook": hook,
            "signal": asdict(signal) if signal is not None else None,
        })
        logger.info("Кросспост в Bluesky (%s) отложен на ~%.0f мин", ticker, delay_seconds / 60)


def _publish_scheduled_telegram(data: dict) -> None:
    if not telegram_publisher.is_configured():
        return

    image_path = None
    if data.get("image_b64"):
        image_path = _b64_to_tempfile(data["image_b64"], data.get("content_type"))

    telegram_publisher.publish_post(data["text"], image_path)
    logger.info("Отложенный кросспост в Telegram опубликован (%s)", data.get("ticker"))


def _publish_scheduled_bluesky(data: dict) -> None:
    if not bluesky_publisher.is_configured():
        return

    text = data["text"]
    ticker = data.get("ticker")
    hook = data.get("hook")
    signal = signal_parser.RsiSignal(**data["signal"]) if data.get("signal") else None

    image_bytes = base64.b64decode(data["image_b64"]) if data.get("image_b64") else None
    content_type = data.get("content_type") or "image/png"

    bluesky_ref = None
    if signal is not None and hook is not None and post_format.is_strong_setup(signal):
        posts = post_format.build_bluesky_thread_signal(hook, signal)
        results = bluesky_publisher.publish_thread(posts, image_bytes=image_bytes, image_content_type=content_type)
        logger.info(
            "Опубликован отложенный Bluesky-тред \"сильный сетап\" для %s (score=%s)",
            signal.ticker, signal.score,
        )
        bluesky_ref = bluesky_publisher.thread_ref(results[0]) if results else None
    else:
        if image_bytes and random.random() < config.BLUESKY_TEASER_PROBABILITY:
            bluesky_text, link_facets = post_format.build_bluesky_teaser(ticker=ticker)
        else:
            bluesky_text, link_facets = post_format.build_bluesky_post(text, ticker=ticker)
        result = bluesky_publisher.publish_post(
            bluesky_text, image_bytes=image_bytes, image_content_type=content_type, link_facets=link_facets,
        )
        bluesky_ref = bluesky_publisher.thread_ref(result)

    logger.info("Отложенный кросспост в Bluesky опубликован (%s)", ticker)

    # Привязываем ссылку к уже созданной записи трекинга результата
    # (см. _do_publish/outcome_tracker.record_signal_outcome) - только
    # для сигналов (image insight не трекается по результату).
    if bluesky_ref and ticker and signal is not None:
        queue_manager.attach_bluesky_ref_to_outcome(ticker, bluesky_ref)


def _process_pending_crossposts() -> None:
    """Вызывается каждый тик - публикует все отложенные кроспосты, чьё
    время уже наступило (см. queue_manager.get_due_crossposts). Ошибка
    на одной записи не должна ронять остальные - каждая обрабатывается
    в своём try/except, с ограниченным числом повторных попыток
    (register_failed_crosspost), как у основной очереди сигналов."""
    due = queue_manager.get_due_crossposts()
    for item in due:
        platform = item["platform"]
        data = item["data"]
        try:
            if platform == "telegram":
                _publish_scheduled_telegram(data)
            elif platform == "bluesky":
                _publish_scheduled_bluesky(data)
            else:
                logger.warning("Неизвестная площадка в очереди отложенных кроспостов: %s", platform)
            queue_manager.remove_crosspost(item["id"])
        except Exception as e:
            logger.warning("Отложенный кросспост (%s, %s) не удался: %s", platform, data.get("ticker"), e)
            dropped = queue_manager.register_failed_crosspost(item["id"])
            if dropped:
                logger.warning(
                    "Отложенный кросспост (%s, %s) выброшен из очереди после превышения лимита попыток",
                    platform, data.get("ticker"),
                )

    queue_manager.prune_stale_crossposts(config.CROSSPOST_STALE_HOURS)


def _post_outcome_updates_to_bluesky(closed_records: list) -> None:
    """Форматы "До/После" и "Win-reveal" - вызывается из tick() сразу
    после outcome_tracker.check_open_outcomes() для каждой ТОЛЬКО ЧТО
    закрытой сделки.

    Для каждой записи:
    1. Если у сигнала был bluesky_ref (кросспост при входе удался) -
       отвечаем В ТОТ ЖЕ ТРЕД сухим итогом (build_bluesky_outcome_reply) -
       это формат "До/После", закрывающий открытую петлю для тех, кто
       видел входной пост. Публикуется на ЛЮБОЙ исход (win/loss/timeout).
    2. Если результат - "win", ДОПОЛНИТЕЛЬНО публикуется отдельный
       самостоятельный "победный" пост (build_bluesky_win_reveal) - формат
       "Win-reveal", рассчитанный на ленту в целом, а не только на тех,
       кто видел исходный вход (у него есть свои ссылки на Binance/TG).

    Как и остальные кросспосты - полностью опционально (тихо пропускаем,
    если Bluesky не настроен) и не должно ронять tick() при сетевой
    ошибке на отдельной записи - одна неудачная запись не должна
    блокировать остальные."""
    if not bluesky_publisher.is_configured() or not closed_records:
        return

    for record in closed_records:
        bluesky_ref = record.get("bluesky_ref")
        if bluesky_ref:
            try:
                reply_text, reply_facets = post_format.build_bluesky_outcome_reply(record)
                root_result = {"uri": bluesky_ref["uri"], "cid": bluesky_ref["cid"]}
                bluesky_publisher.publish_post(
                    reply_text,
                    link_facets=reply_facets,
                    reply_to=bluesky_publisher.reply_refs(root_result),
                )
            except bluesky_publisher.BlueskyPublishError as e:
                logger.warning("Ответ 'До/После' в Bluesky для %s не удался: %s", record["ticker"], e)
            except (KeyError, TypeError) as e:
                logger.warning("Некорректный bluesky_ref у %s, пропускаю 'До/После': %s", record["ticker"], e)

        if record.get("result") == "win":
            try:
                win_text, win_facets = post_format.build_bluesky_win_reveal(record)
                bluesky_publisher.publish_post(win_text, link_facets=win_facets)
            except bluesky_publisher.BlueskyPublishError as e:
                logger.warning("Win-reveal в Bluesky для %s не удался: %s", record["ticker"], e)


def _publish_win_celebrations(closed_records: list) -> None:
    """Формат "Забрали профит!" (см. win_celebration_generator.py) -
    ТОЛЬКО Binance Square, на КАЖДУЮ сделку, закрывшуюся в плюс в этом
    тике. Вызывается из tick() сразу после _post_outcome_updates_to_bluesky
    (тот же список closed_records, что и там - один и тот же источник
    "что только что закрылось", просто два независимых формата на двух
    площадках).

    Как и остальные "бонусные", не критичные для основной публикации
    форматы (Bluesky win-reveal/До-После, промо-посты) - ошибка на
    одной записи логируется и не должна ронять остальные/весь tick()."""
    for record in closed_records:
        if record.get("result") != "win":
            continue
        try:
            angle = win_celebration_generator.pick_angle(queue_manager.get_last_win_celebration_angle())
            hook = win_celebration_generator.generate_win_celebration_hook(angle)
            if hook is None:
                continue

            ok, reason = win_celebration_generator.validate_win_celebration_hook(hook)
            if not ok:
                logger.warning("Хук 'Забрали профит!' для %s не прошёл проверку (%s) - пропускаю", record["ticker"], reason)
                continue

            post_text = post_format.assemble_win_celebration_post(hook, record)

            # Картинка (график с отметкой цены выхода) - тот же паттерн,
            # что и в _publish_signal: неудача генерации графика НЕ
            # должна блокировать сам пост, просто уходит без картинки.
            # Именно этот формат ("вот закрытая сделка, вот цифры") -
            # ровно то, для чего Binance советует добавлять картинку:
            # визуальное подтверждение делает пост убедительнее, чем
            # голые цифры в тексте.
            try:
                chart_path = chart_generator.generate_chart_image(
                    record["ticker"], days=2, expected_price=float(record["exit_price"])
                )
            except Exception as e:
                logger.warning("Не удалось сгенерировать график для 'Забрали профит!' %s: %s", record["ticker"], e)
                chart_path = None
            image_paths = [chart_path] if chart_path else None

            hashtags = post_format.square_hashtags_line(record["ticker"])
            binance_text = f"{post_text}\n\n{hashtags}" if hashtags else post_text

            binance_publisher.publish_post(binance_text, image_paths=image_paths)
            queue_manager.set_last_win_celebration_angle(angle)
            logger.info("Опубликован пост 'Забрали профит!' для %s (%+.2f%%)", record["ticker"], record["pnl_pct"])
        except binance_publisher.PublishError as e:
            logger.warning("Пост 'Забрали профит!' для %s не удался: %s", record["ticker"], e)
        except Exception:
            logger.exception("Неожиданная ошибка при публикации 'Забрали профит!' для %s", record.get("ticker"))


def try_publish_currency_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("currency")
    min_seconds = config.MIN_POST_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("currency")

    if seconds_elapsed < min_seconds:
        return  # окно публикации ещё не открылось

    pending = queue_manager.get_pending_post(min_score=config.MIN_SIGNAL_SCORE_TO_PUBLISH)
    if pending is None:
        logger.info(
            "Окно публикации (валюта) открыто, но в очереди нет сигнала со score > %d - жду следующего тика.",
            config.MIN_SIGNAL_SCORE_TO_PUBLISH,
        )
        return  # нет сигнала, проходящего порог качества - публиковать нечего

    queue_index, kind, payload = pending
    logger.info("Окно публикации (валюта) открыто, тип отложенного поста: %s", kind)

    if kind == "signal":
        published = _publish_signal(payload)
    elif kind == "image":
        published = _publish_image_insight(payload)
    else:
        logger.error("Неизвестный тип отложенного поста: %s", kind)
        return

    if published:
        ticker = payload.ticker
        if kind == "signal":
            queue_manager.log_signal_history(payload)  # для еженедельной статьи - только реально опубликованное
        queue_manager.log_posted_ticker(ticker)
        queue_manager.set_last_post_time("currency")
        queue_manager.roll_new_jitter("currency", config.CURRENCY_JITTER_MINUTES * 60)
        queue_manager.clear_pending_post(queue_index)
    else:
        dropped = queue_manager.register_failed_attempt(queue_index)
        if dropped:
            logger.warning(
                "Пост (%s, %s) не опубликовался %d раза подряд - выброшен из очереди, "
                "чтобы не блокировать остальное.",
                kind, payload.ticker, queue_manager.MAX_PUBLISH_ATTEMPTS,
            )
        # иначе пост остаётся в очереди, попробуем снова на следующем тике


# ============================================================
# Формат "Экстренный" - ТОЛЬКО Bluesky (см. volatility_alert.py)
# ============================================================

def try_publish_emergency_post() -> None:
    """В отличие от остальных Bluesky-форматов - проверяется на КАЖДОМ
    тике, а не по фиксированному расписанию: у скачка волатильности нет
    предсказуемого времени, реагировать нужно как можно быстрее.
    Повторные срабатывания ограничены кулдауном
    (config.EMERGENCY_COOLDOWN_HOURS), а не джиттером."""
    if not bluesky_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("emergency")
    if seconds_elapsed < config.EMERGENCY_COOLDOWN_HOURS * 3600:
        return
    if not queue_manager.should_retry_now("emergency"):
        return

    try:
        spike = volatility_alert.detect_market_volatility_spike()
    except Exception as e:
        logger.error("Ошибка проверки рыночной волатильности: %s", e)
        return

    if spike is None:
        return

    logger.info(
        "Обнаружен скачок волатильности рынка (%.2f%% за %dч) - генерирую экстренный пост",
        spike["pct"], spike["window_hours"],
    )

    try:
        post_text = volatility_alert.generate_emergency_post(spike)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на экстренном посте - жду %.1fч", backoff_hours)
        queue_manager.set_retry_backoff("emergency", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации экстренного поста: %s", e)
        queue_manager.set_retry_backoff("emergency", 1)
        return

    if post_text is None:
        queue_manager.set_retry_backoff("emergency", 1)
        return

    ok, reason = volatility_alert.validate_emergency_post(post_text, spike)
    if not ok:
        logger.error("Экстренный пост не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("emergency", 1)
        return

    try:
        bluesky_publisher.publish_post(post_text)
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Публикация экстренного поста в Bluesky не удалась: %s", e)
        queue_manager.set_retry_backoff("emergency", 1)
        return

    logger.info("Опубликован экстренный пост о волатильности рынка в Bluesky")
    queue_manager.set_last_post_time("emergency")


# ============================================================
# Формат 2: "opinion" - личное мнение по движению BTC
# ============================================================

def try_publish_opinion_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("opinion")
    min_seconds = config.OPINION_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("opinion")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("opinion"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (мнение) открыто - генерирую пост")

    theme = opinion_generator.pick_theme(queue_manager.get_last_opinion_theme())
    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        result = opinion_generator.generate_opinion_post(theme, hook_mode=hook_mode)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на посте-мнении - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("opinion", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации поста-мнения: %s", e)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    if result is None:
        logger.warning("Не удалось получить данные для темы %s - пропускаю до следующего окна", theme)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    post_text, allowed_numbers, headline_pct = result
    ok, reason = opinion_generator.validate_opinion_post_text(post_text, allowed_numbers)
    if not ok:
        logger.error("Пост-мнение не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    # theme - ключ opinion_generator.THEMES: "BTC"/"ETH" - конкретный
    # тикер, есть $CASHTAG; "market" - корзина из нескольких активов,
    # $CASHTAG строить не от чего (см. square_hashtags_line), берём
    # общий тег без тикера вместо того, чтобы просто ничего не добавлять.
    hashtags = post_format.square_hashtags_line(theme) if theme in ("BTC", "ETH") else post_format.square_general_hashtag_line()
    binance_text = f"{post_text}\n\n{hashtags}"

    try:
        published_result = binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации поста-мнения: %s", e)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    voice_memory.record_post(post_text, theme=theme, pct=headline_pct)
    queue_manager.set_last_opinion_theme(theme)
    queue_manager.set_last_hook_mode(hook_mode)

    logger.info("Опубликовано (мнение): %s", published_result)
    _crosspost_to_telegram(post_text)
    _crosspost_to_bluesky(post_text)
    queue_manager.set_last_post_time("opinion")
    queue_manager.roll_new_jitter("opinion", config.OPINION_JITTER_HOURS * 3600)


def try_publish_news_take() -> None:
    """Формат "мнение по новости" (см. news_opinion_generator.py) -
    читает публичный новостной канал (config.NEWS_SOURCE_CHANNEL) и
    публикует авторскую реакцию, АВТОМАТИЧЕСКИ, как и остальные
    Binance Square форматы (в отличие от OKX Orbit/Bybit ByX - там нет
    API, только черновик). Выключено по умолчанию
    (config.NEWS_TAKE_ENABLED)."""
    if not config.NEWS_TAKE_ENABLED:
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("news_take")
    min_seconds = news_opinion_generator.MIN_DAYS_BETWEEN_NEWS_POSTS * 86400

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("news_take"):
        return  # недавно был сбой - ждём отступ, не долбим сеть на каждом тике
    if not news_opinion_generator.is_news_window_open():
        return

    logger.info("Окно новостного поста открыто - проверяю канал %s", config.NEWS_SOURCE_CHANNEL)

    try:
        result = news_opinion_generator.generate_news_take()
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на новостном посте - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("news_take", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации новостного поста: %s", e)
        queue_manager.set_retry_backoff("news_take", 1)
        return

    if result is None:
        # Нет свежей непрочитанной новости, или пост не прошёл проверку.
        # Короткий отступ (не час, как у остальных форматов) - проверка
        # канала дешёвая (просто чтение статичной страницы), а быстрее
        # заметить свежую новость - прямая рекомендация из официальных
        # материалов Binance ("rapid coverage of...macro events attracts
        # wider engagement"). Это не увеличивает ЧАСТОТУ публикаций
        # (интервал между постами не изменился), только задержку до
        # момента, когда бот заметит новую новость и сможет отреагировать.
        queue_manager.set_retry_backoff("news_take", 0.25)
        return

    post_text, source_post_id, image_path = result
    hashtags = post_format.square_general_hashtag_line()
    binance_text = f"{post_text}\n\n{hashtags}"

    try:
        published_result = binance_publisher.publish_post(
            binance_text, image_paths=[image_path] if image_path else None,
        )
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации новостного поста: %s", e)
        queue_manager.set_retry_backoff("news_take", 1)
        return

    voice_memory.record_post(post_text)
    news_opinion_generator.mark_news_post_used(source_post_id)

    logger.info("Опубликовано (новость): %s", published_result)
    _crosspost_to_telegram(post_text)
    _crosspost_to_bluesky(post_text)
    queue_manager.set_last_post_time("news_take")


# ============================================================
# Формат "Хот-тейк" - ТОЛЬКО Bluesky (см. hot_take_generator.py)
# ============================================================

# ============================================================
# Формат "Промо" - ТОЛЬКО Binance Square (см. binance_promo_generator.py)
# ============================================================

def try_publish_binance_promo() -> None:
    """Как try_publish_hot_take, но зеркально: публикует ТОЛЬКО на
    Binance Square, минуя Telegram/Bluesky целиком - это
    площадочно-специфичный формат про выгоду/удобство самой площадки
    (комиссии, Square как соцсеть), а не про рыночный сигнал."""
    seconds_elapsed = queue_manager.seconds_since_last_post("binance_promo")
    min_seconds = config.BINANCE_PROMO_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("binance_promo")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("binance_promo"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (промо, Binance Square) открыто - генерирую пост")

    theme = binance_promo_generator.pick_theme(queue_manager.get_last_binance_promo_theme())
    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        text = binance_promo_generator.generate_binance_promo(theme, hook_mode=hook_mode)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на промо-посте - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("binance_promo", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации промо-поста: %s", e)
        queue_manager.set_retry_backoff("binance_promo", 1)
        return

    if text is None:
        logger.warning("Не удалось сгенерировать промо-пост (тема %s) - пропускаю до следующего окна", theme)
        queue_manager.set_retry_backoff("binance_promo", 1)
        return

    ok, reason = binance_promo_generator.validate_binance_promo(text)
    if not ok:
        logger.error("Промо-пост не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("binance_promo", 1)
        return

    post_text = binance_promo_generator.assemble_binance_promo(text)

    hashtags = post_format.square_general_hashtag_line()
    binance_text = f"{post_text}\n\n{hashtags}"

    try:
        binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.warning("Публикация промо-поста на Binance Square не удалась: %s", e)
        queue_manager.set_retry_backoff("binance_promo", 1)
        return

    logger.info("Опубликован промо-пост (фокус %s) на Binance Square", theme)
    voice_memory.record_post(post_text)
    queue_manager.set_last_binance_promo_theme(theme)
    queue_manager.set_last_hook_mode(hook_mode)
    queue_manager.set_last_post_time("binance_promo")
    queue_manager.roll_new_jitter("binance_promo", config.BINANCE_PROMO_JITTER_HOURS * 3600)


def try_publish_hot_take() -> None:
    """В отличие от остальных try_publish_* - публикует ТОЛЬКО в
    Bluesky, минуя Binance Square/Telegram целиком (это формат,
    заточенный под механику конкретно этой площадки - см. docstring
    hot_take_generator.py). Если Bluesky не настроен - даже не пытаемся
    генерировать текст, незачем тратить вызов LLM впустую."""
    if not bluesky_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("hot_take")
    min_seconds = config.HOT_TAKE_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("hot_take")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("hot_take"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (хот-тейк, Bluesky) открыто - генерирую пост")

    theme = hot_take_generator.pick_theme(queue_manager.get_last_hot_take_theme())
    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        result = hot_take_generator.generate_hot_take(theme, hook_mode=hook_mode)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на хот-тейке - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("hot_take", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации хот-тейка: %s", e)
        queue_manager.set_retry_backoff("hot_take", 1)
        return

    if result is None:
        logger.warning("Не удалось получить данные для темы %s - пропускаю до следующего окна хот-тейка", theme)
        queue_manager.set_retry_backoff("hot_take", 1)
        return

    post_text, allowed_numbers, headline_pct = result
    ok, reason = hot_take_generator.validate_hot_take(post_text, allowed_numbers)
    if not ok:
        logger.error("Хот-тейк не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("hot_take", 1)
        return

    try:
        bluesky_publisher.publish_post(post_text)
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Публикация хот-тейка в Bluesky не удалась: %s", e)
        queue_manager.set_retry_backoff("hot_take", 1)
        return

    logger.info("Опубликован хот-тейк (тема %s) в Bluesky", theme)
    voice_memory.record_post(post_text, theme=theme, pct=headline_pct)
    queue_manager.set_last_hot_take_theme(theme)
    queue_manager.set_last_hook_mode(hook_mode)
    queue_manager.set_last_post_time("hot_take")
    queue_manager.roll_new_jitter("hot_take", config.HOT_TAKE_JITTER_HOURS * 3600)


# ============================================================
# Формат "Мини-урок" - ТОЛЬКО Bluesky (см. mini_lesson_generator.py)
# ============================================================

def try_publish_mini_lesson() -> None:
    """Как и try_publish_hot_take - публикует ТОЛЬКО в Bluesky, минуя
    Square/Telegram. Без реальных рыночных чисел (это концептуальный
    образовательный пост, не про конкретный актив сейчас), поэтому нет
    проверки allowed_numbers - только validate_mini_lesson (дисклеймер/
    длина/отсутствие заявлений о конкретной цене)."""
    if not bluesky_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("mini_lesson")
    min_seconds = config.MINI_LESSON_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("mini_lesson")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("mini_lesson"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (мини-урок, Bluesky) открыто - генерирую пост")

    topic = mini_lesson_generator.pick_topic(queue_manager.get_last_mini_lesson_topic())

    try:
        post_text = mini_lesson_generator.generate_mini_lesson(topic)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на мини-уроке - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("mini_lesson", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации мини-урока: %s", e)
        queue_manager.set_retry_backoff("mini_lesson", 1)
        return

    if post_text is None:
        logger.warning("Не удалось сгенерировать мини-урок по теме %s - пропускаю до следующего окна", topic)
        queue_manager.set_retry_backoff("mini_lesson", 1)
        return

    ok, reason = mini_lesson_generator.validate_mini_lesson(post_text)
    if not ok:
        logger.error("Мини-урок не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("mini_lesson", 1)
        return

    try:
        bluesky_publisher.publish_post(post_text)
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Публикация мини-урока в Bluesky не удалась: %s", e)
        queue_manager.set_retry_backoff("mini_lesson", 1)
        return

    logger.info("Опубликован мини-урок (тема %s) в Bluesky", topic)
    queue_manager.set_last_mini_lesson_topic(topic)
    queue_manager.set_last_post_time("mini_lesson")
    queue_manager.roll_new_jitter("mini_lesson", config.MINI_LESSON_JITTER_HOURS * 3600)


# ============================================================
# Формат "Вопрос аудитории" - ТОЛЬКО Bluesky
# (см. audience_question_generator.py)
# ============================================================

def try_publish_audience_question() -> None:
    """Как и остальные Bluesky-эксклюзивные форматы - публикует ТОЛЬКО
    в Bluesky. Без обращения к LLM (см. docstring
    audience_question_generator.py) - вопрос это статический,
    заранее написанный текст, генерировать и валидировать нечего."""
    if not bluesky_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("audience_question")
    min_seconds = (
        config.AUDIENCE_QUESTION_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("audience_question")
    )

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("audience_question"):
        return

    question = audience_question_generator.pick_question(queue_manager.get_last_audience_question())

    try:
        bluesky_publisher.publish_post(question)
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Публикация вопроса аудитории в Bluesky не удалась: %s", e)
        queue_manager.set_retry_backoff("audience_question", 1)
        return

    logger.info("Опубликован вопрос аудитории в Bluesky: %s", question)
    queue_manager.set_last_audience_question(question)
    queue_manager.set_last_post_time("audience_question")
    queue_manager.roll_new_jitter("audience_question", config.AUDIENCE_QUESTION_JITTER_HOURS * 3600)


# ============================================================
# Формат "Глоссарий" - ТОЛЬКО Telegram (см. telegram_glossary.py)
# ============================================================

def try_publish_telegram_glossary() -> None:
    """Публикует ТОЛЬКО в Telegram (в отличие от Bluesky-эксклюзивных
    форматов выше) - минуя Binance Square/Bluesky целиком. Темы идут
    строго последовательно (queue_manager.get_glossary_index), а не
    случайно - см. docstring telegram_glossary.py."""
    if not telegram_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("telegram_glossary")
    min_seconds = (
        config.TELEGRAM_GLOSSARY_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("telegram_glossary")
    )

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("telegram_glossary"):
        return

    index = queue_manager.get_glossary_index()
    topic = telegram_glossary.get_topic(index)

    try:
        post_text = telegram_glossary.generate_glossary_post(topic)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на посте глоссария - жду %.1fч", backoff_hours)
        queue_manager.set_retry_backoff("telegram_glossary", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации поста глоссария (%s): %s", topic["key"], e)
        queue_manager.set_retry_backoff("telegram_glossary", 1)
        return

    if post_text is None:
        queue_manager.set_retry_backoff("telegram_glossary", 1)
        return

    ok, reason = telegram_glossary.validate_glossary_post(post_text, topic)
    if not ok:
        logger.error("Пост глоссария (%s) не прошёл проверку, публикация отменена: %s", topic["key"], reason)
        queue_manager.set_retry_backoff("telegram_glossary", 1)
        return

    try:
        telegram_publisher.publish_post(post_text)
    except telegram_publisher.TelegramPublishError as e:
        logger.warning("Публикация поста глоссария в Telegram не удалась: %s", e)
        queue_manager.set_retry_backoff("telegram_glossary", 1)
        return

    logger.info("Опубликован пост глоссария (%s) в Telegram", topic["key"])
    queue_manager.set_glossary_index(index + 1)
    queue_manager.set_last_post_time("telegram_glossary")
    queue_manager.roll_new_jitter("telegram_glossary", config.TELEGRAM_GLOSSARY_JITTER_HOURS * 3600)


# ============================================================
# Форматы "Опрос" и "AMA" - ТОЛЬКО Telegram (см. telegram_engagement.py)
# ============================================================

def try_publish_telegram_poll() -> None:
    """Нативный Telegram-опрос (sendPoll) - без LLM, статический пул
    вопросов (см. docstring telegram_engagement.py). Публикует ТОЛЬКО
    в Telegram."""
    if not telegram_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("telegram_poll")
    min_seconds = config.TELEGRAM_POLL_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("telegram_poll")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("telegram_poll"):
        return

    poll = telegram_engagement.pick_poll(queue_manager.get_last_telegram_poll())

    try:
        telegram_publisher.publish_poll(poll["question"], poll["options"])
    except telegram_publisher.TelegramPublishError as e:
        logger.warning("Публикация опроса в Telegram не удалась: %s", e)
        queue_manager.set_retry_backoff("telegram_poll", 1)
        return

    logger.info("Опубликован опрос в Telegram: %s", poll["question"])
    queue_manager.set_last_telegram_poll(poll["question"])
    queue_manager.set_last_post_time("telegram_poll")
    queue_manager.roll_new_jitter("telegram_poll", config.TELEGRAM_POLL_JITTER_HOURS * 3600)


def try_publish_telegram_ama() -> None:
    """Приглашение на AMA - без LLM, статический пул текстов (см.
    docstring telegram_engagement.py). Сам разбор ответов на вопросы из
    комментариев - за пределами автоматизации, это делает человек
    вручную отдельным постом позже. Публикует ТОЛЬКО в Telegram."""
    if not telegram_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("telegram_ama")
    min_seconds = config.TELEGRAM_AMA_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("telegram_ama")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("telegram_ama"):
        return

    prompt = telegram_engagement.pick_ama_prompt(queue_manager.get_last_telegram_ama_prompt())

    try:
        telegram_publisher.publish_post(prompt)
    except telegram_publisher.TelegramPublishError as e:
        logger.warning("Публикация приглашения на AMA в Telegram не удалась: %s", e)
        queue_manager.set_retry_backoff("telegram_ama", 1)
        return

    logger.info("Опубликовано приглашение на AMA в Telegram")
    queue_manager.set_last_telegram_ama_prompt(prompt)
    queue_manager.set_last_post_time("telegram_ama")
    queue_manager.roll_new_jitter("telegram_ama", config.TELEGRAM_AMA_JITTER_HOURS * 3600)


# ============================================================
# Формат "Предложения по ребалансировке" - ТОЛЬКО Telegram
# (см. rebalance_advisor.py)
# ============================================================

def try_publish_rebalance_report() -> None:
    """ПОЛУавтоматический - бот только ПРЕДЛАГАЕТ кандидатов на пересмотр
    состава Treasury Index, ничего не меняет в BASKET сам (см. docstring
    rebalance_advisor.py). Если кандидатов нет - отчёт не публикуется
    вообще (это нормальный, самый частый исход, не ошибка). Публикует
    ТОЛЬКО в Telegram."""
    if not telegram_publisher.is_configured():
        return

    seconds_elapsed = queue_manager.seconds_since_last_post("rebalance_report")
    min_seconds = (
        config.REBALANCE_REVIEW_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("rebalance_report")
    )

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("rebalance_report"):
        return

    candidates = rebalance_advisor.find_rebalance_candidates()
    if not candidates:
        logger.info("Ребалансировка: кандидатов на пересмотр состава индекса нет - отчёт не нужен")
        # Не откладываем окно джиттером - следующая проверка пройдёт по
        # тому же базовому интервалу, а не будет искусственно сдвинута
        # тем, что в этот раз предлагать было нечего.
        queue_manager.set_last_post_time("rebalance_report")
        return

    try:
        report_text = rebalance_advisor.build_rebalance_report(candidates)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на отчёте по ребалансировке - жду %.1fч", backoff_hours)
        queue_manager.set_retry_backoff("rebalance_report", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации отчёта по ребалансировке: %s", e)
        queue_manager.set_retry_backoff("rebalance_report", 1)
        return

    if report_text is None:
        queue_manager.set_retry_backoff("rebalance_report", 1)
        return

    try:
        telegram_publisher.publish_post(report_text)
    except telegram_publisher.TelegramPublishError as e:
        logger.warning("Публикация отчёта по ребалансировке в Telegram не удалась: %s", e)
        queue_manager.set_retry_backoff("rebalance_report", 1)
        return

    logger.info("Опубликован отчёт по ребалансировке (%d кандидатов) в Telegram", len(candidates))
    queue_manager.set_last_post_time("rebalance_report")
    queue_manager.roll_new_jitter("rebalance_report", config.REBALANCE_REVIEW_JITTER_HOURS * 3600)


# ============================================================
# Формат 2.5: "treasury" - Treasury Index (собственный инфраструктурный индекс)
# ============================================================

def try_publish_treasury_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("treasury")
    min_seconds = config.TREASURY_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("treasury")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("treasury"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (Treasury Index) открыто - считаю индекс")

    try:
        result = treasury_generator.generate_treasury_post(config.TREASURY_PERIOD_HOURS)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на Treasury Index - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("treasury", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации Treasury Index: %s", e)
        queue_manager.set_retry_backoff("treasury", 1)
        return

    if result is None:
        logger.warning("Treasury Index не удалось посчитать (нет данных с Binance) - пропускаю до следующего окна")
        queue_manager.set_retry_backoff("treasury", 1)
        return

    binance_text, telegram_text, _index_result, chart_path, heatmap_path, composition_path = result

    # Square поддерживает карусель из нескольких картинок - отдаём все,
    # что удалось построить. Heatmap первой (самый информативный "снимок
    # периода" каждый раз), дальше equity curve (история), и, если в
    # этот раз сгенерирована, диаграмма состава (редкая, но ценная).
    square_images = [p for p in (heatmap_path, chart_path, composition_path) if p is not None]
    image_paths = square_images or None

    # Telegram/Bluesky (см. _crosspost_to_telegram/_crosspost_to_bluesky)
    # принимают только ОДНУ картинку - приоритет: диаграмма состава,
    # если сгенерирована в этот раз (редкое "особое" событие, стоит
    # выделить), иначе тепловая карта (ценность каждый раз), equity
    # curve сюда намеренно не идёт - она менее нужна как единственная
    # картинка при живом текстовом "с запуска" блоке в самом посте.
    single_image_path = composition_path or heatmap_path

    try:
        published_result = binance_publisher.publish_post(binance_text, image_paths=image_paths)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации Treasury Index: %s", e)
        queue_manager.set_retry_backoff("treasury", 2)
        return

    logger.info("Опубликовано (Treasury Index): %s", published_result)
    _crosspost_to_telegram(telegram_text, single_image_path)
    _crosspost_to_bluesky(telegram_text, single_image_path)
    queue_manager.set_last_post_time("treasury")
    queue_manager.roll_new_jitter("treasury", config.TREASURY_JITTER_HOURS * 3600)


# ============================================================
# Формат "index_signal" - удобный момент купить/продать монету
# из Treasury Index (умный менеджмент по индексу)
# ============================================================

def try_publish_index_signal_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("index_signal")
    min_seconds = config.INDEX_SIGNAL_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("index_signal")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("index_signal"):
        return

    picked = queue_manager.get_pending_index_signal(config.MIN_INDEX_SIGNAL_SCORE_TO_PUBLISH)
    if picked is None:
        # Нет подходящего сигнала прямо сейчас - это нормально (вселенная
        # всего 15 монет), не двигаем таймер, просто ждём следующего окна.
        return
    idx, signal = picked

    logger.info("Окно публикации (index_signal) открыто - генерирую текст для %s", signal.ticker)

    try:
        binance_text = index_signal_generator.generate_index_signal_post(signal)
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на index_signal - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("index_signal", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации index_signal для %s: %s", signal.ticker, e)
        if queue_manager.register_failed_index_attempt(idx):
            logger.warning("Сигнал %s выброшен из очереди индекса по лимиту попыток", signal.ticker)
        queue_manager.set_retry_backoff("index_signal", 1)
        return

    try:
        published_result = binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации index_signal для %s: %s", signal.ticker, e)
        if queue_manager.register_failed_index_attempt(idx):
            logger.warning("Сигнал %s выброшен из очереди индекса по лимиту попыток", signal.ticker)
        queue_manager.set_retry_backoff("index_signal", 2)
        return

    logger.info("Опубликовано (index_signal, %s): %s", signal.ticker, published_result)
    _crosspost_to_telegram(binance_text)
    _crosspost_to_bluesky(binance_text, ticker=signal.ticker)
    queue_manager.clear_pending_index_signal(idx)
    queue_manager.set_last_post_time("index_signal")
    queue_manager.roll_new_jitter("index_signal", config.INDEX_SIGNAL_JITTER_HOURS * 3600)


# ============================================================
# Формат 3: "article" - еженедельная статья-сводка
# ============================================================

def try_publish_article_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("article")
    min_seconds = config.ARTICLE_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("article")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("article"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (статья) открыто - собираю историю за неделю")

    history = queue_manager.get_digest_history(min_seconds)
    try:
        result = article_generator.generate_weekly_article(history)
    except groq_client.GroqRateLimited as e:
        # Groq сам сказал, сколько ждать (Retry-After) - используем это,
        # а не фиксированные 2 часа на глазок. Минимум 5 минут, чтобы не
        # долбить API почти сразу же, если Retry-After окажется крошечным.
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning(
            "Groq rate limit на статье - жду %.1fч перед следующей попыткой", backoff_hours,
        )
        queue_manager.set_retry_backoff("article", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации статьи: %s", e)
        queue_manager.set_retry_backoff("article", 2)
        return

    if result is None:
        logger.warning("Недостаточно данных для статьи - пропускаю до следующего окна")
        # сдвигаем таймер, чтобы не пытаться каждую минуту - попробуем
        # снова через обычный интервал, а не спамить логи
        queue_manager.set_last_post_time("article")
        queue_manager.roll_new_jitter("article", config.ARTICLE_JITTER_HOURS * 3600)
        return

    title, body, _ = result
    ok, reason = article_generator.validate_article_text(title, body, history)
    if not ok:
        logger.error("Статья не прошла проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("article", 2)
        return

    try:
        cover_path = article_generator.generate_cover_image()
    except Exception as e:
        logger.warning("Не удалось сгенерировать обложку для статьи: %s", e)
        cover_path = None

    try:
        published_result = binance_publisher.publish_article(
            title, f"{body}\n\n{post_format.square_article_hashtags_line()}", cover_path
        )
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации статьи: %s", e)
        queue_manager.set_retry_backoff("article", 2)
        return

    logger.info("Опубликовано (статья): %s", published_result)
    _crosspost_to_telegram(f"{title}\n\n{body}", cover_path)
    # Обложка для Bluesky - тот же локальный файл, что уже был загружен
    # на Binance Square (uploadBlob в AT Protocol требует сырые байты,
    # не URL - см. bluesky_publisher).
    _crosspost_to_bluesky(f"{title}\n\n{body}", cover_path)
    queue_manager.set_last_post_time("article")
    queue_manager.roll_new_jitter("article", config.ARTICLE_JITTER_HOURS * 3600)


def try_publish_accuracy_report() -> None:
    """Еженедельный отчёт точности сигналов (win-rate, средний % результата)
    на основе outcome_tracker (Фаза 1). Та же схема backoff/jitter, что
    у treasury/article - см. try_publish_treasury_post выше."""
    seconds_elapsed = queue_manager.seconds_since_last_post("accuracy_report")
    min_seconds = config.ACCURACY_REPORT_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("accuracy_report")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("accuracy_report"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (отчёт точности) открыто - считаю статистику")

    try:
        result = accuracy_report_generator.generate_accuracy_report_post()
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на отчёте точности - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("accuracy_report", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации отчёта точности: %s", e)
        queue_manager.set_retry_backoff("accuracy_report", 1)
        return

    if result is None:
        # Недостаточно закрытых сигналов за период - не сбой, а штатная
        # ситуация (например, только что включили трекинг). Сдвигаем
        # таймер на обычный интервал, а не долбим каждый тик.
        logger.info("Недостаточно данных для отчёта точности - пропускаю до следующего окна")
        queue_manager.set_last_post_time("accuracy_report")
        queue_manager.roll_new_jitter("accuracy_report", config.ACCURACY_REPORT_JITTER_HOURS * 3600)
        return

    binance_text, telegram_text, chart_path = result

    try:
        published_result = binance_publisher.publish_post(
            binance_text, image_paths=[chart_path] if chart_path else None,
        )
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации отчёта точности: %s", e)
        queue_manager.set_retry_backoff("accuracy_report", 2)
        return

    logger.info("Опубликован отчёт точности: %s", published_result)
    _crosspost_to_telegram(telegram_text, image_path=chart_path)
    _crosspost_to_bluesky(telegram_text, image_path=chart_path)
    queue_manager.set_last_post_time("accuracy_report")
    queue_manager.roll_new_jitter("accuracy_report", config.ACCURACY_REPORT_JITTER_HOURS * 3600)


def try_publish_loss_review() -> None:
    """Разбор неудачных сигналов за период (Фаза 4, вторая часть) - та же
    схема backoff/jitter, что у accuracy_report/treasury/article выше."""
    seconds_elapsed = queue_manager.seconds_since_last_post("loss_review")
    min_seconds = config.LOSS_REVIEW_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("loss_review")

    if seconds_elapsed < min_seconds:
        return
    if not queue_manager.should_retry_now("loss_review"):
        return  # недавно был сбой - ждём отступ, не долбим API на каждом тике

    logger.info("Окно публикации (разбор промахов) открыто - собираю закрытые убыточные сигналы")

    try:
        result = loss_review_generator.generate_loss_review_post()
    except groq_client.GroqRateLimited as e:
        backoff_hours = max(e.retry_after_seconds / 3600, 5 / 60)
        logger.warning("Groq rate limit на разборе промахов - жду %.1fч перед следующей попыткой", backoff_hours)
        queue_manager.set_retry_backoff("loss_review", backoff_hours)
        return
    except Exception as e:
        logger.error("Ошибка генерации разбора промахов: %s", e)
        queue_manager.set_retry_backoff("loss_review", 1)
        return

    if result is None:
        # Недостаточно убыточных сигналов за период - не сбой, а штатная
        # ситуация (в том числе хорошая - значит, стратегия давно не мажет).
        logger.info("Недостаточно убыточных сигналов для разбора промахов - пропускаю до следующего окна")
        queue_manager.set_last_post_time("loss_review")
        queue_manager.roll_new_jitter("loss_review", config.LOSS_REVIEW_JITTER_HOURS * 3600)
        return

    binance_text, telegram_text = result

    try:
        published_result = binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации разбора промахов: %s", e)
        queue_manager.set_retry_backoff("loss_review", 2)
        return

    logger.info("Опубликован разбор промахов: %s", published_result)
    _crosspost_to_telegram(telegram_text)
    _crosspost_to_bluesky(telegram_text)
    queue_manager.set_last_post_time("loss_review")
    queue_manager.roll_new_jitter("loss_review", config.LOSS_REVIEW_JITTER_HOURS * 3600)


# ============================================================
# Общий цикл
# ============================================================

def _check_dead_mans_switch() -> None:
    """Если currency-формат не публиковался дольше config.DEAD_MANS_SWITCH_HOURS
    часов - это, скорее всего, не штатное "нет хороших сигналов" (такое
    бывает часами), а признак реальной поломки. Шлём алерт владельцу
    (троттлится внутри alerting.send_owner_alert - не чаще раза в тот
    же период, чтобы не долбить в личку каждые 10 минут)."""
    elapsed_hours = queue_manager.seconds_since_last_post("currency") / 3600
    if elapsed_hours == float("inf"):
        return  # бот ни разу ещё не публиковал - это старт, а не сбой
    if elapsed_hours >= config.DEAD_MANS_SWITCH_HOURS:
        alerting.send_owner_alert(
            "dead_mans_switch_currency",
            f"Бот не публиковал сигналы уже {elapsed_hours:.1f}ч "
            f"(порог: {config.DEAD_MANS_SWITCH_HOURS}ч). Возможно, сломался "
            f"источник сигналов, протух API-ключ, или перестал запускаться workflow "
            f"- стоит проверить Actions и check_state.py.",
            min_repeat_hours=config.DEAD_MANS_SWITCH_HOURS,
        )


def tick() -> None:
    try:
        queue_manager.prune_expired_entries(config.SIGNAL_MAX_AGE_HOURS)

        try:
            outcome_summary = outcome_tracker.check_open_outcomes()
            if outcome_summary["closed"]:
                logger.info(
                    "Трекинг результатов: закрыто %d, ещё открыто %d",
                    outcome_summary["closed"], outcome_summary["still_open"],
                )
                _post_outcome_updates_to_bluesky(outcome_summary["closed_records"])
                _publish_win_celebrations(outcome_summary["closed_records"])
        except Exception:
            logger.exception("Ошибка проверки открытых результатов - пропускаю до следующего тика")

        try:
            # Дёшево (без сети, работает по уже посчитанным closed_outcomes) -
            # можно пересчитывать каждый тик, не только по расписанию.
            strategy_tuner.recompute_adjustments()
        except Exception:
            logger.exception("Ошибка автокоррекции тактики - пропускаю до следующего тика")

        try:
            _check_dead_mans_switch()
        except Exception:
            logger.exception("Ошибка проверки dead man's switch - пропускаю до следующего тика")

        try:
            _process_pending_crossposts()
        except Exception:
            logger.exception("Ошибка обработки отложенных кроспостов - пропускаю до следующего тика")

        queue_manager.prune_expired_index_signals(config.INDEX_SIGNAL_MAX_AGE_HOURS)
        try:
            # В отличие от общего сканера, здесь вселенная всего 15 монет -
            # сканируем каждый тик независимо от окна публикации (дёшево),
            # публикация всё равно ограничена своим отдельным интервалом
            # в try_publish_index_signal_post().
            index_signal_scanner.run_index_scan()
        except Exception:
            logger.exception("Ошибка в сканере индекс-сигналов - пропускаю до следующего тика")

        seconds_elapsed = queue_manager.seconds_since_last_post("currency")
        min_seconds = config.MIN_POST_INTERVAL_HOURS * 3600 + queue_manager.get_jitter_seconds("currency")
        seconds_until_window = min_seconds - seconds_elapsed

        # "Активный" режим: окно публикации уже открыто (seconds_until_window <= 0)
        # или откроется в ближайшие ACTIVE_WINDOW_LOOKAHEAD_MINUTES. Только в этом
        # режиме дёргаем сканер и канал - до этого момента нет смысла собирать
        # сигналы, которые всё равно устареют за несколько часов ожидания.
        window_active = seconds_until_window <= config.ACTIVE_WINDOW_LOOKAHEAD_MINUTES * 60

        if window_active:
            check_for_new_signals()
            try:
                scanner.run_scan()
            except Exception:
                logger.exception("Ошибка в собственном сканере сигналов - пропускаю до следующего тика")
        else:
            logger.info(
                "Окно публикации (валюта) откроется через %.0f мин - сканирование "
                "пропущено на этом тике (активный режим начнётся за %.0f мин до окна)",
                seconds_until_window / 60, config.ACTIVE_WINDOW_LOOKAHEAD_MINUTES,
            )

        try_publish_emergency_post()
        try_publish_currency_post()
        try_publish_binance_promo()
        try_publish_opinion_post()
        try_publish_news_take()
        try_publish_hot_take()
        try_publish_mini_lesson()
        try_publish_audience_question()
        try_publish_telegram_glossary()
        try_publish_telegram_poll()
        try_publish_telegram_ama()
        try_publish_rebalance_report()
        try_publish_treasury_post()
        try_publish_article_post()
        try_publish_accuracy_report()
        try_publish_loss_review()
        try_publish_index_signal_post()
    except Exception as e:
        logger.exception("Неожиданная ошибка в основном цикле")
        alerting.send_owner_alert(
            "tick_unhandled_exception",
            f"Необработанная ошибка в основном цикле бота: {type(e).__name__}: {e}\n"
            f"Подробности - в логе запуска (Actions -> последний run -> Run one bot check).",
        )


def main() -> None:
    missing = config.validate_config()
    if missing:
        logger.error(
            "Не заполнены обязательные переменные в .env: %s. "
            "Заполни их и перезапусти бота.",
            ", ".join(missing),
        )
        return

    once = "--once" in sys.argv

    logger.info(
        "Бот запущен. Интервал проверки: %sс. Окна публикации - валюта: %sч, мнение: %sч, "
        "treasury: %sч, статья: %sч, отчёт точности: %sч, новость: enabled=%s (мин. %sд между постами, канал %s)",
        config.POLL_INTERVAL_SECONDS, config.MIN_POST_INTERVAL_HOURS,
        config.OPINION_INTERVAL_HOURS, config.TREASURY_INTERVAL_HOURS, config.ARTICLE_INTERVAL_HOURS,
        config.ACCURACY_REPORT_INTERVAL_HOURS,
        config.NEWS_TAKE_ENABLED, news_opinion_generator.MIN_DAYS_BETWEEN_NEWS_POSTS, config.NEWS_SOURCE_CHANNEL,
    )

    if once:
        # Режим разового запуска (GitHub Actions: python main.py --once) -
        # делаем ровно один проход и выходим, НЕ запускаем планировщик,
        # иначе процесс зависнет навсегда (BlockingScheduler.start()
        # никогда не возвращает управление).
        logger.info("Режим --once: запуск одного тика")
        tick()
        logger.info("Тик завершён, выход")
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(tick, "interval", seconds=config.POLL_INTERVAL_SECONDS, next_run_time=None)
    tick()  # сразу один проход при старте, не дожидаясь первого интервала
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()