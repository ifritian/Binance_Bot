"""
check_state.py - диагностика без побочных эффектов.

Показывает текущее состояние bot_state.db: что лежит в очереди на
публикацию, сколько осталось до каждого окна (валюта/мнение/статья),
и offset Telegram. НЕ публикует, НЕ ходит в Telegram/Binance API -
только читает локальную базу и config, чтобы можно было предсказать
поведение бота на следующем тике без угадывания.

Запуск: python check_state.py
"""

import config
import queue_manager


def fmt_seconds(s: float) -> str:
    if s == float("inf"):
        return "ещё не публиковал(ось) ни разу"
    if s < 0:
        return f"уже наступило ({-s/3600:.1f}ч назад)"
    h = s / 3600
    return f"{h:.1f}ч"


def report_window(post_type: str, interval_hours: float) -> None:
    elapsed = queue_manager.seconds_since_last_post(post_type)
    jitter = queue_manager.get_jitter_seconds(post_type)
    min_seconds = interval_hours * 3600 + jitter
    remaining = min_seconds - elapsed

    print(f"\n[{post_type}]")
    print(f"  интервал: {interval_hours}ч, джиттер сейчас: {jitter/60:+.1f} мин")
    if elapsed == float("inf"):
        print("  с момента последней публикации: ещё не публиковал(ось)")
        print("  -> окно ОТКРЫТО (публикация при первом подходящем контенте)")
    else:
        print(f"  с момента последней публикации: {fmt_seconds(elapsed)}")
        if remaining <= 0:
            print("  -> окно ОТКРЫТО прямо сейчас")
        else:
            mins = remaining / 60
            print(f"  -> окно откроется через ~{remaining/3600:.1f}ч ({mins:.0f} мин)")


def main() -> None:
    print("=== Состояние бота (только чтение, без запросов к API) ===")

    offset = queue_manager.get_telegram_update_offset()
    print(f"\nTelegram update offset: {offset}")

    pending = queue_manager.get_pending_post()
    if pending is None:
        print("\nОтложенный пост (валюта): НЕТ — даже если откроется окно, "
              "публиковать нечего, пока не придёт новый сигнал из канала.")
    else:
        kind, payload = pending
        if kind == "digest":
            top = payload.top
            print(f"\nОтложенный пост (валюта): ДА, тип=digest, "
                  f"тикер={top.ticker}, изменение={top.change_pct}%, score={top.score}")
        else:
            print(f"\nОтложенный пост (валюта): ДА, тип=image, "
                  f"тикер={payload.ticker}, направление={payload.direction}")
        print("  -> если окно публикации (currency) открыто, бот опубликует "
              "это на следующем тике (если пройдёт генерацию текста, "
              "проверку чисел и получится сделать график/скачать картинку).")

    report_window("currency", config.MIN_POST_INTERVAL_HOURS)
    report_window("opinion", config.OPINION_INTERVAL_HOURS)
    report_window("article", config.ARTICLE_INTERVAL_HOURS)

    print(f"\nИнтервал тика бота: {config.POLL_INTERVAL_SECONDS}с "
          "(в GitHub Actions фактически раз в ~10 мин по расписанию cron, "
          "независимо от этого значения).")

    print("\n--- Вывод ---")
    if pending is not None:
        elapsed = queue_manager.seconds_since_last_post("currency")
        jitter = queue_manager.get_jitter_seconds("currency")
        min_seconds = config.MIN_POST_INTERVAL_HOURS * 3600 + jitter
        if elapsed >= min_seconds:
            print("Если СЕЙЧАС появится новый тик (запуск бота) - пост на Binance "
                  "ОПУБЛИКУЕТСЯ (валютный формат), при условии что новый пост "
                  "уже лежит в очереди как показано выше.")
        else:
            print("Новый пост в канале ляжет в очередь, но публикация ВАЛЮТНОГО "
                  "формата произойдёт не раньше, чем откроется окно (см. выше).")
    else:
        print("Если новый пост появится в канале ДО следующего тика бота - "
              "он попадёт в очередь, но публикация всё равно произойдёт "
              "не раньше открытия окна (currency), см. выше.")


if __name__ == "__main__":
    main()