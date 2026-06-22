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

from apscheduler.schedulers.blocking import BlockingScheduler

import article_generator
import binance_publisher
import chart_generator
import config
import image_analyzer
import opinion_generator
import post_format
import queue_manager
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
            signal = signal_parser.parse_signal(post.text)
            if signal is not None:
                # выбираем запись из дайджеста, избегая повтора недавних тикеров,
                # вместо того чтобы всегда брать первую (для разнообразия постов)
                recent = queue_manager.get_recent_tickers()
                chosen = signal_parser.pick_entry(signal.entries, recent)
                logger.info(
                    "Новый дайджест: %s %s score %s (недавние тикеры: %s)",
                    chosen.ticker, chosen.change_pct, chosen.score, recent,
                )
                # сохраняем только выбранную запись - дальше публикуется именно она
                signal_to_store = signal_parser.Signal(
                    title=signal.title, entries=[chosen], raw_text=signal.raw_text
                )
                queue_manager.set_pending_digest(signal_to_store)
                queue_manager.log_digest_history(chosen, signal.title)  # для еженедельной статьи
                continue  # текст распознан как дайджест - картинку (если есть) не трогаем

        if post.image_url:
            insight = image_analyzer.analyze_chart_image(post.image_url)
            if insight is not None:
                logger.info("Новая картинка распознана: %s, направление %s", insight.ticker, insight.direction)
                queue_manager.set_pending_image(insight)


def _publish_digest(signal) -> bool:
    top = signal.top
    logger.info("Публикуем дайджест %s", top.ticker)

    hook_mode = post_format.pick_hook_mode(queue_manager.get_last_hook_mode())

    try:
        post_text = text_generator.generate_post_text(top, signal.title, hook_mode)
    except Exception as e:
        logger.error("Ошибка генерации текста: %s", e)
        return False

    ok, reason = validator.validate_post_text(post_text, top)
    if not ok:
        logger.error("Пост не прошёл проверку чисел, публикация отменена: %s", reason)
        return False

    try:
        chart_path = chart_generator.generate_chart_image(top.ticker, interval="1h", limit=48)
    except Exception as e:
        logger.warning("Не удалось сгенерировать график для %s: %s", top.ticker, e)
        chart_path = None

    if chart_path is None:
        logger.warning(
            "Нет графика для %s - публикация пропущена, пост остаётся в очереди "
            "до появления следующего подходящего поста.", top.ticker
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
    try:
        image_path = image_analyzer.download_to_tempfile(insight.image_url)
    except Exception as e:
        logger.warning("Не удалось скачать оригинальную картинку %s: %s", insight.image_url, e)

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
    min_seconds = config.MIN_POST_INTERVAL_HOURS * 3600

    if seconds_elapsed < min_seconds:
        return  # окно публикации ещё не открылось

    pending = queue_manager.get_pending_post()
    if pending is None:
        return  # нет накопленного поста - публиковать нечего

    kind, payload = pending
    logger.info("Окно публикации (валюта) открыто, тип отложенного поста: %s", kind)

    if kind == "digest":
        published = _publish_digest(payload)
    elif kind == "image":
        published = _publish_image_insight(payload)
    else:
        logger.error("Неизвестный тип отложенного поста: %s", kind)
        return

    if published:
        ticker = payload.top.ticker if kind == "digest" else payload.ticker
        queue_manager.log_posted_ticker(ticker)
        queue_manager.set_last_post_time("currency")
        queue_manager.clear_pending_post()
    # если не опубликовано - пост остаётся в очереди, попробуем на следующем тике


# ============================================================
# Формат 2: "opinion" - личное мнение по движению BTC
# ============================================================

def try_publish_opinion_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("opinion")
    min_seconds = config.OPINION_INTERVAL_HOURS * 3600

    if seconds_elapsed < min_seconds:
        return

    logger.info("Окно публикации (мнение) открыто - генерирую пост")

    try:
        result = opinion_generator.generate_opinion_post()
    except Exception as e:
        logger.error("Ошибка генерации поста-мнения: %s", e)
        return

    if result is None:
        logger.warning("Не удалось получить данные BTC для поста-мнения - пропускаю до следующего окна")
        return

    post_text, pct = result
    ok, reason = opinion_generator.validate_opinion_post_text(post_text, pct)
    if not ok:
        logger.error("Пост-мнение не прошёл проверку, публикация отменена: %s", reason)
        return

    try:
        published_result = binance_publisher.publish_post(post_text)
    except binance_publisher.PublishError as e:
        logger.error("Ошибка публикации поста-мнения: %s", e)
        return

    logger.info("Опубликовано (мнение): %s", published_result)
    queue_manager.set_last_post_time("opinion")


# ============================================================
# Формат 3: "article" - еженедельная статья-сводка
# ============================================================

def try_publish_article_post() -> None:
    seconds_elapsed = queue_manager.seconds_since_last_post("article")
    min_seconds = config.ARTICLE_INTERVAL_HOURS * 3600

    if seconds_elapsed < min_seconds:
        return

    logger.info("Окно публикации (статья) открыто - собираю историю за неделю")

    history = queue_manager.get_digest_history(min_seconds)
    try:
        result = article_generator.generate_weekly_article(history)
    except Exception as e:
        logger.error("Ошибка генерации статьи: %s", e)
        return

    if result is None:
        logger.warning("Недостаточно данных для статьи - пропускаю до следующего окна")
        # сдвигаем таймер, чтобы не пытаться каждую минуту - попробуем
        # снова через обычный интервал, а не спамить логи
        queue_manager.set_last_post_time("article")
        return

    title, body, _ = result
    ok, reason = article_generator.validate_article_text(title, body, history)
    if not ok:
        logger.error("Статья не прошла проверку, публикация отменена: %s", reason)
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
        return

    logger.info("Опубликовано (статья): %s", published_result)
    queue_manager.set_last_post_time("article")


# ============================================================
# Общий цикл
# ============================================================

def tick() -> None:
    try:
        check_for_new_signals()
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

    logger.info(
        "Бот запущен. Интервал проверки: %sс. Окна публикации - валюта: %sч, мнение: %sч, статья: %sч",
        config.POLL_INTERVAL_SECONDS, config.MIN_POST_INTERVAL_HOURS,
        config.OPINION_INTERVAL_HOURS, config.ARTICLE_INTERVAL_HOURS,
    )

    scheduler = BlockingScheduler()
    scheduler.add_job(tick, "interval", seconds=config.POLL_INTERVAL_SECONDS, next_run_time=None)
    tick()  # сразу один проход при старте, не дожидаясь первого интервала
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()