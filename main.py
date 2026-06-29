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
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

import article_generator
import binance_publisher
import chart_generator
import config
import image_analyzer
import opinion_generator
import post_format
import queue_manager
import scanner
import signal_parser
import telegram_listener
import text_generator
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
                # выбираем сигнал из пачки, избегая повтора недавних тикеров,
                # вместо того чтобы всегда брать первый (для разнообразия постов)
                recent = queue_manager.get_recent_tickers()
                chosen = signal_parser.pick_entry(signals, recent)
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
        chart_path = chart_generator.generate_chart_image(signal.ticker, days=2)
    except Exception as e:
        logger.warning("Не удалось сгенерировать график для %s: %s", signal.ticker, e)
        chart_path = None

    if chart_path is None:
        logger.warning(
            "Нет графика для %s - публикация пропущена, пост остаётся в очереди "
            "до появления следующего подходящего поста.", signal.ticker
        )
        return False

    published = _do_publish(post_text, [chart_path])
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
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

    published = _do_publish(post_text, [image_path])
    if published:
        queue_manager.set_last_hook_mode(hook_mode)
    return published


def _do_publish(post_text: str, image_paths) -> bool:
    try:
        result = binance_publisher.publish_post(post_text, image_paths=image_paths)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации: %s", e)
        return False

    logger.info("Опубликовано (валюта): %s", result)
    return True


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
    queue_manager.set_last_post_time("opinion")
    queue_manager.roll_new_jitter("opinion", config.OPINION_JITTER_HOURS * 3600)


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
    queue_manager.set_last_post_time("article")
    queue_manager.roll_new_jitter("article", config.ARTICLE_JITTER_HOURS * 3600)


# ============================================================
# Общий цикл
# ============================================================

def tick() -> None:
    try:
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
        try_publish_article_post()
    except Exception:
        logger.exception("Неожиданная ошибка в основном цикле")


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
        "Бот запущен. Интервал проверки: %sс. Окна публикации - валюта: %sч, мнение: %sч, статья: %sч",
        config.POLL_INTERVAL_SECONDS, config.MIN_POST_INTERVAL_HOURS,
        config.OPINION_INTERVAL_HOURS, config.ARTICLE_INTERVAL_HOURS,
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