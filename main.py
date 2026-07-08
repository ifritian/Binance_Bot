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
import logging
import mimetypes
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

import article_generator
import accuracy_report_generator
import alerting
import binance_publisher
import bluesky_publisher
import chart_generator
import config
import image_analyzer
import groq_client
import index_signal_generator
import index_signal_scanner
import loss_review_generator
import opinion_generator
import outcome_tracker
import post_format
import queue_manager
import scanner
import signal_parser
import strategy_tuner
import telegram_listener
import telegram_publisher
import text_generator
import treasury_generator
import validator

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
        post_text = text_generator.generate_post_text(signal, hook_mode)
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

    published = _do_publish(post_text, [chart_path], signal.ticker)
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
        # Ставим сигнал на трекинг результата ПОСЛЕ публикации - если
        # пост не вышел, аудитория его не видела, трекать нечего.
        try:
            outcome_tracker.record_signal_outcome(signal)
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

    published = _do_publish(post_text, [image_path], insight.ticker)
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
    return published


def _do_publish(post_text: str, image_paths, ticker: str | None = None) -> bool:
    try:
        result = binance_publisher.publish_post(post_text, image_paths=image_paths)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации: %s", e)
        return False

    logger.info("Опубликовано (валюта): %s", result)
    image_path = image_paths[0] if image_paths else None
    _crosspost_to_telegram(post_text, image_path)
    _crosspost_to_bluesky(post_text, image_path, ticker=ticker)
    return True


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


def _crosspost_to_bluesky(text: str, image_path=None, ticker: str | None = None) -> None:
    """Дублирует уже опубликованный (на Binance Square) пост в Bluesky
    (config.BLUESKY_HANDLE / config.BLUESKY_APP_PASSWORD).

    Как и _crosspost_to_telegram выше - ОПЦИОНАЛЕН и НЕЗАВИСИМ от основной
    публикации: если Bluesky не настроен - молча пропускаем, если настроен,
    но запрос упал - логируем предупреждение и идём дальше.

    image_path - ЛОКАЛЬНЫЙ путь к уже сгенерированному/скачанному файлу
    (тот же, что был загружен на Binance Square) - AT Protocol грузит
    картинку как сырые байты через uploadBlob, а не по URL (в отличие от
    Telegram/бывшего Threads), поэтому здесь читаем файл с диска напрямую.

    text адаптируется под формат Bluesky через post_format.build_bluesky_post
    (хэштег тикера + ссылки на Binance/Telegram + обрезка под лимит 300
    символов, плюс facets для кликабельных ссылок) - в исходном виде текст
    никуда, кроме Bluesky, не уходит."""
    if not bluesky_publisher.is_configured():
        return
    try:
        bluesky_text, link_facets = post_format.build_bluesky_post(text, ticker=ticker)
        image_bytes = None
        content_type = "image/png"
        if image_path is not None:
            path = Path(image_path)
            content_type = mimetypes.guess_type(path.name)[0] or "image/png"
            image_bytes = path.read_bytes()
        bluesky_publisher.publish_post(
            bluesky_text,
            image_bytes=image_bytes,
            image_content_type=content_type,
            link_facets=link_facets,
        )
    except bluesky_publisher.BlueskyPublishError as e:
        logger.warning("Кросспост в Bluesky не удался: %s", e)



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

    try:
        result = opinion_generator.generate_opinion_post(theme)
    except Exception as e:
        logger.error("Ошибка генерации поста-мнения: %s", e)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    if result is None:
        logger.warning("Не удалось получить данные для темы %s - пропускаю до следующего окна", theme)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    post_text, allowed_numbers = result
    ok, reason = opinion_generator.validate_opinion_post_text(post_text, allowed_numbers)
    if not ok:
        logger.error("Пост-мнение не прошёл проверку, публикация отменена: %s", reason)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    try:
        published_result = binance_publisher.publish_post(post_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации поста-мнения: %s", e)
        queue_manager.set_retry_backoff("opinion", 1)
        return

    queue_manager.set_last_opinion_theme(theme)

    logger.info("Опубликовано (мнение): %s", published_result)
    _crosspost_to_telegram(post_text)
    _crosspost_to_bluesky(post_text)
    queue_manager.set_last_post_time("opinion")
    queue_manager.roll_new_jitter("opinion", config.OPINION_JITTER_HOURS * 3600)


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

    binance_text, telegram_text, _index_result = result

    try:
        published_result = binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации Treasury Index: %s", e)
        queue_manager.set_retry_backoff("treasury", 2)
        return

    logger.info("Опубликовано (Treasury Index): %s", published_result)
    _crosspost_to_telegram(telegram_text)
    _crosspost_to_bluesky(telegram_text)
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
        published_result = binance_publisher.publish_article(title, body, cover_path)
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

    binance_text, telegram_text = result

    try:
        published_result = binance_publisher.publish_post(binance_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации отчёта точности: %s", e)
        queue_manager.set_retry_backoff("accuracy_report", 2)
        return

    logger.info("Опубликован отчёт точности: %s", published_result)
    _crosspost_to_telegram(telegram_text)
    _crosspost_to_bluesky(telegram_text)
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

        try_publish_currency_post()
        try_publish_opinion_post()
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
        "treasury: %sч, статья: %sч, отчёт точности: %sч",
        config.POLL_INTERVAL_SECONDS, config.MIN_POST_INTERVAL_HOURS,
        config.OPINION_INTERVAL_HOURS, config.TREASURY_INTERVAL_HOURS, config.ARTICLE_INTERVAL_HOURS,
        config.ACCURACY_REPORT_INTERVAL_HOURS,
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